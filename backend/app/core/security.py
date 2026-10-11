import json
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from cryptography.fernet import Fernet
from jose import JWTError, jwt

from app.core.config import settings

_fernet = Fernet(settings.encryption_key.encode())


# --- Agent-Token (Konsolen-Container → Backend) ---
#
# Die Konsolen-Agenten schicken ihre Nutzer-ID als X-CS-User-ID. Bis 2026-10-11 wurde sie
# ungeprüft geglaubt: jeder Container (und jeder, der /api erreicht) konnte sich als
# beliebiger Nutzer ausgeben — beim MCP-Server (Freigabe-Prüfung) und bei
# /computer/agent-credentials, der die OAuth-Tokens der CLI-Agenten herausgibt. Das Token
# bindet die ID an das SECRET_KEY des Backends; ein fremder Container kennt es nicht.

AGENT_TOKEN_HEADER = "x-cs-agent-token"


def agent_token(user_id: str) -> str:
    import hashlib
    import hmac
    return hmac.new(settings.secret_key.encode(), f"cs-agent:{user_id}".encode(),
                    hashlib.sha256).hexdigest()


def verify_agent_token(user_id: str | None, token: str | None) -> bool:
    import hmac
    if not user_id or not token:
        return False
    return hmac.compare_digest(agent_token(user_id), token.strip())


# --- Passwords ---

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# --- JWT ---

def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload["type"] = "access"
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_refresh_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    payload["type"] = "refresh"
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_ide_token(data: dict, hours: int = 12) -> str:
    """Long-lived, IDE-scoped token stored in the cs_ide_token cookie. Decoupled
    from the short access token so the embedded code-server iframe survives."""
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload["type"] = "ide"
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return {}


# --- Credential Encryption (Connector-Zugangsdaten) ---

def encrypt_credentials(data: dict) -> bytes:
    return _fernet.encrypt(json.dumps(data).encode())


def decrypt_credentials(ciphertext: bytes) -> dict:
    return json.loads(_fernet.decrypt(ciphertext).decode())
