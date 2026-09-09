import base64
import getpass
import hashlib
from pathlib import Path
import secrets


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=32768, r=8, p=1,
        maxmem=64 * 1024 * 1024,
    )
    return ":".join((
        "scrypt", "32768", "8", "1",
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    ))


def main():
    target = Path(__file__).resolve().parents[1] / ".env"
    if target.exists():
        raise SystemExit(".env already exists; move it aside before generating new encryption keys")
    email = input("Email for HTTPS certificate notices: ").strip()
    if ("@" not in email or any(char.isspace() for char in email)
            or not email.isascii()):
        raise SystemExit("Enter a valid email address")
    password = getpass.getpass("Fieldhouse league password (14+ characters): ")
    confirmation = getpass.getpass("Confirm league password: ")
    if password != confirmation:
        raise SystemExit("Passwords did not match")
    if len(password) < 14 or len(password) > 200:
        raise SystemExit("Password must be between 14 and 200 characters")
    vault_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    target.write_text(
        "\n".join((
            "FIELDHOUSE_DOMAIN=fantasy-coach.tech",
            f"ACME_EMAIL={email}",
            f"HARNESS_PASSWORD_HASH={password_hash(password)}",
            f"HARNESS_VAULT_KEY={vault_key}",
            "",
        )),
        encoding="ascii",
    )
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print("Created .env. Back it up securely; losing HARNESS_VAULT_KEY loses saved credentials.")


if __name__ == "__main__":
    main()
