import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


class ConflictError(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_version VALUES (1);
                CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, provider TEXT NOT NULL,
                    model TEXT NOT NULL, mode TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, objective TEXT NOT NULL, summary TEXT,
                    error TEXT, usage TEXT NOT NULL DEFAULT '{}'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs ((1))
                    WHERE status IN ('queued','running','cancelling');
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
                    created_at TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL, result TEXT NOT NULL, policy_version INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS action_fingerprint ON actions (fingerprint);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else default

    def set(self, key: str, value):
        with self.connect() as db:
            db.execute("INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, encode(value)))

    def set_policy(self, policy: dict):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM config WHERE key='policy_version'").fetchone()
            version = json.loads(row["value"]) + 1 if row else 1
            for key, value in (("policy", policy), ("policy_version", version)):
                db.execute("INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, encode(value)))
            db.execute("INSERT INTO events(run_id,created_at,kind,data) VALUES (NULL,?,?,?)",
                       (now(), "policy_changed", encode({"version": version})))
        return version

    def put_secret(self, name: str, ciphertext: bytes):
        self.put_secrets({name: ciphertext})

    def put_secrets(self, values: dict[str, bytes]):
        with self.connect() as db:
            db.executemany("INSERT INTO secrets VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                           values.items())

    def secret(self, name: str) -> bytes | None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM secrets WHERE name=?", (name,)).fetchone()
            return bytes(row["value"]) if row else None

    def delete_secret(self, name: str):
        with self.connect() as db:
            db.execute("DELETE FROM secrets WHERE name=?", (name,))

    def credential_status(self) -> dict:
        with self.connect() as db:
            names = {row["name"] for row in db.execute("SELECT name FROM secrets")}
        return {name: name in names for name in ("grok", "gemini", "espn_s2", "swid")}

    def event(self, kind: str, data: dict, run_id: str | None = None):
        with self.connect() as db:
            db.execute("INSERT INTO events(run_id,created_at,kind,data) VALUES (?,?,?,?)",
                       (run_id, now(), kind, encode(data)))

    def create_run(self, settings: dict, objective: str, daily_limit: int) -> dict:
        identifier = str(uuid.uuid4())
        stamp = now()
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                count = db.execute("SELECT COUNT(*) FROM runs WHERE started_at>=?",
                                   (stamp[:10],)).fetchone()[0]
                if count >= daily_limit:
                    raise ConflictError("Daily run limit reached (UTC accounting day)")
                db.execute("""INSERT INTO runs
                    (id,status,provider,model,mode,started_at,objective) VALUES (?,?,?,?,?,?,?)""",
                           (identifier, "queued", settings["provider"], settings["model"],
                            settings["mode"], stamp, objective))
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Another agent run is already active") from exc
        return self.run(identifier)

    def update_run(self, identifier: str, **fields):
        allowed = {"status", "finished_at", "summary", "error", "usage"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid run update")
        if "usage" in fields:
            fields["usage"] = encode(fields["usage"])
        with self.connect() as db:
            db.execute(f"UPDATE runs SET {','.join(key + '=?' for key in fields)} WHERE id=?",
                       (*fields.values(), identifier))

    @staticmethod
    def _run(row):
        if not row:
            return None
        result = dict(row)
        result["usage"] = json.loads(result["usage"])
        return result

    def run(self, identifier: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (identifier,)).fetchone()
            result = self._run(row)
            if result:
                result["events"] = [
                    {**dict(event), "data": json.loads(event["data"])}
                    for event in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (identifier,))
                ]
            return result

    def runs(self) -> list[dict]:
        with self.connect() as db:
            return [self._run(row) for row in db.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT 100")]

    def active_run(self) -> dict | None:
        with self.connect() as db:
            return self._run(db.execute(
                "SELECT * FROM runs WHERE status IN ('queued','running','cancelling')"
            ).fetchone())

    def recover(self):
        with self.connect() as db:
            db.execute("""UPDATE runs SET status='interrupted',finished_at=?,
                error='Backend restarted; this run was not replayed'
                WHERE status IN ('queued','running','cancelling')""", (now(),))
            db.execute("""UPDATE actions SET status='unknown',updated_at=?,
                result=? WHERE status='submitting'""",
                       (now(), encode({"detail": "Interrupted submission requires ESPN reconciliation"})))

    def add_action(self, run_id: str, payload: dict, status: str, result: dict,
                   policy_version: int, fingerprint: str) -> dict:
        identifier = str(uuid.uuid4())
        stamp = now()
        with self.connect() as db:
            db.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (identifier, run_id, payload["action"], status, stamp, stamp,
                        encode(payload), encode(result), policy_version, fingerprint))
        return self.action(identifier)

    def action(self, identifier: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (identifier,)).fetchone()
        return self._action(row) if row else None

    def update_action(self, identifier: str, expected_status: str | None = None, **fields) -> dict:
        allowed = {"status", "result"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid action update")
        if "result" in fields:
            fields["result"] = encode(fields["result"])
        fields["updated_at"] = now()
        where = "id=?"
        arguments = [*fields.values(), identifier]
        if expected_status is not None:
            where += " AND status=?"
            arguments.append(expected_status)
        with self.connect() as db:
            cursor = db.execute(
                f"UPDATE actions SET {','.join(key + '=?' for key in fields)} WHERE {where}",
                arguments,
            )
            if cursor.rowcount != 1:
                raise ConflictError("This lineup confirmation is no longer available")
        return self.action(identifier)

    @staticmethod
    def _action(row):
        item = dict(row)
        for key in ("payload", "result"):
            item[key] = json.loads(item[key])
        return item

    def actions(self) -> list[dict]:
        with self.connect() as db:
            return [self._action(row) for row in db.execute(
                "SELECT * FROM actions ORDER BY created_at DESC LIMIT 200")]

    def unresolved_live_action(self) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM actions WHERE status IN ('submitting','unknown') "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return self._action(row) if row else None
