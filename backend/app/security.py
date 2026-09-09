import ctypes
import base64
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from .storage import Store


class SecretError(Exception):
    pass


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class Vault:
    def __init__(self, store: Store):
        self.store = store
        self._portable_key = self._load_portable_key()

    @staticmethod
    def _load_portable_key() -> bytes | None:
        encoded = os.environ.get("HARNESS_VAULT_KEY")
        if not encoded:
            if os.name != "nt":
                raise SecretError("HARNESS_VAULT_KEY is required outside Windows")
            return None
        try:
            key = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (InvalidTag, ValueError, UnicodeError):
            raise SecretError("HARNESS_VAULT_KEY must be URL-safe base64") from None
        if len(key) != 32:
            raise SecretError("HARNESS_VAULT_KEY must decode to exactly 32 bytes")
        return key

    @staticmethod
    def _crypt(value: bytes, decrypt: bool = False) -> bytes:
        if os.name != "nt":
            raise SecretError("Secure credential storage requires Windows DPAPI")
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        buffer = ctypes.create_string_buffer(value)
        source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = Blob()
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        if decrypt:
            operation = crypt32.CryptUnprotectData
        else:
            operation = crypt32.CryptProtectData
        operation.argtypes = [
            ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob),
        ]
        operation.restype = ctypes.c_int
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise SecretError("Windows could not unlock credentials; reconnect under the original Windows user")
        try:
            return ctypes.string_at(target.data, target.size)
        finally:
            kernel32.LocalFree(target.data)

    def put(self, name: str, value: str):
        self.store.put_secret(name, self._encrypt(value.encode("utf-8")))

    def put_many(self, values: dict[str, str]):
        encrypted = {name: self._encrypt(value.encode("utf-8")) for name, value in values.items()}
        self.store.put_secrets(encrypted)

    def get(self, name: str) -> str:
        value = self.store.secret(name)
        if value is None:
            raise SecretError(f"Configure {name} credentials in local setup first")
        if value.startswith(b"fieldhouse-v1:"):
            if self._portable_key is None:
                raise SecretError("HARNESS_VAULT_KEY is required to unlock these credentials")
            try:
                nonce, ciphertext = value[14:26], value[26:]
                return AESGCM(self._portable_key).decrypt(
                    nonce, ciphertext, b"fieldhouse-secrets-v1",
                ).decode("utf-8")
            except (InvalidTag, ValueError, UnicodeError):
                raise SecretError("Stored credentials could not be decrypted with HARNESS_VAULT_KEY") from None
        return self._crypt(value, decrypt=True).decode("utf-8")

    def _encrypt(self, value: bytes) -> bytes:
        if self._portable_key is None:
            return self._crypt(value)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._portable_key).encrypt(
            nonce, value, b"fieldhouse-secrets-v1",
        )
        return b"fieldhouse-v1:" + nonce + ciphertext


class InstanceLock:
    """An OS-held lock prevents a second scheduler sharing the same database."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = self.path.open("a+b")
            if os.fstat(self.handle.fileno()).st_size == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.handle:
                self.handle.close()
            self.handle = None
            raise RuntimeError("Another harness instance is using this data directory") from exc

    def release(self):
        if self.handle:
            self.handle.close()
            self.handle = None


def password_hash(password: str, salt: bytes | None = None) -> str:
    if not isinstance(password, str) or len(password) < 14 or len(password) > 200:
        raise SecretError("League password must be between 14 and 200 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=32768, r=8, p=1,
        maxmem=64 * 1024 * 1024,
    )
    return "scrypt:32768:8:1:" + base64.urlsafe_b64encode(salt).decode("ascii") + ":" + (
        base64.urlsafe_b64encode(digest).decode("ascii")
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split(":")
        if algorithm != "scrypt" or (int(n), int(r), int(p)) != (32768, 8, 1):
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt.encode("ascii")),
            n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024,
        )
        return secrets.compare_digest(actual, base64.urlsafe_b64decode(expected.encode("ascii")))
    except (ValueError, UnicodeError):
        return False


@dataclass
class SessionRecord:
    cookie: str
    csrf: str
    expires_at: float
    team_id: int | None = None


class LocalSession:
    def __init__(self, launch_token: str | None = None, password_digest: str | None = None,
                 default_team_id: int | None = None, database: Path | None = None):
        if launch_token and password_digest:
            raise SecretError("Configure either local token or public password authentication")
        self.launch_token = launch_token or secrets.token_urlsafe(32)
        self.password_digest = password_digest
        self.default_team_id = default_team_id
        self.database = database
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = threading.Lock()
        if database is not None:
            database.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS browser_sessions (
                    cookie_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL,
                    expires_at REAL NOT NULL, team_id INTEGER
                )""")

    def valid_login(self, value: str) -> bool:
        if self.password_digest:
            return verify_password(value, self.password_digest)
        return secrets.compare_digest(value, self.launch_token)

    def authenticated(self, cookie: str | None) -> bool:
        return self.get(cookie) is not None

    def create(self) -> SessionRecord:
        record = SessionRecord(
            cookie=secrets.token_urlsafe(32),
            csrf=secrets.token_urlsafe(32),
            expires_at=time.time() + 43_200,
            team_id=self.default_team_id,
        )
        if self.database is not None:
            with self._connect() as db:
                self._purge_persisted(db)
                db.execute(
                    "INSERT INTO browser_sessions VALUES (?,?,?,?)",
                    (self._cookie_hash(record.cookie), record.csrf,
                     record.expires_at, record.team_id),
                )
            return record
        with self._lock:
            self._purge_expired()
            self._sessions[record.cookie] = record
        return record

    def get(self, cookie: str | None) -> SessionRecord | None:
        if not cookie:
            return None
        if self.database is not None:
            with self._connect() as db:
                self._purge_persisted(db)
                row = db.execute(
                    "SELECT csrf,expires_at,team_id FROM browser_sessions WHERE cookie_hash=?",
                    (self._cookie_hash(cookie),),
                ).fetchone()
            return SessionRecord(cookie, row[0], row[1], row[2]) if row else None
        with self._lock:
            self._purge_expired()
            return self._sessions.get(cookie)

    def bind_team(self, cookie: str, team_id: int):
        if self.database is not None:
            with self._connect() as db:
                cursor = db.execute(
                    "UPDATE browser_sessions SET team_id=? WHERE cookie_hash=? AND expires_at>?",
                    (team_id, self._cookie_hash(cookie), time.time()),
                )
                if cursor.rowcount != 1:
                    raise SecretError("Your Fieldhouse session has expired")
            return
        with self._lock:
            self._purge_expired()
            record = self._sessions.get(cookie)
            if record is None:
                raise SecretError("Your Fieldhouse session has expired")
            record.team_id = team_id

    def revoke(self, cookie: str | None):
        if cookie:
            if self.database is not None:
                with self._connect() as db:
                    db.execute(
                        "DELETE FROM browser_sessions WHERE cookie_hash=?",
                        (self._cookie_hash(cookie),),
                    )
                return
            with self._lock:
                self._sessions.pop(cookie, None)

    def _purge_expired(self):
        clock = time.time()
        for cookie in [
            value for value, record in self._sessions.items()
            if record.expires_at <= clock
        ]:
            self._sessions.pop(cookie, None)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _cookie_hash(cookie: str) -> str:
        return hashlib.sha256(cookie.encode("ascii")).hexdigest()

    @staticmethod
    def _purge_persisted(db):
        db.execute("DELETE FROM browser_sessions WHERE expires_at<=?", (time.time(),))
