import json
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_fernet = Fernet(settings.encryption_key.encode())


# --- Passwords ---

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


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
