import hashlib
import hmac
import secrets

import bcrypt

from config import settings

# The frontend already hashes the raw password client-side as SHA-256(salt + password)
# and sends the hex digest. We treat that digest as the "password" and hash it again
# with bcrypt before storing it, so the database never holds a directly usable value.


def hash_client_hash(client_hash: str) -> str:
    return bcrypt.hashpw(client_hash.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_client_hash(client_hash: str, stored: str) -> bool:
    try:
        return bcrypt.checkpw(client_hash.encode("utf-8"), stored.encode("utf-8"))
    except ValueError:
        return False


def derive_pseudo_salt(username: str) -> str:
    """Stable 32-hex salt for an unknown username, so /auth/salt cannot be used
    to probe which accounts exist. Deterministic per (secret_key, username)."""
    digest = hmac.new(
        settings.secret_key.encode("utf-8"), username.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:32]


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
