import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings


def derive_key(secret: str) -> bytes:
    """Accept a proper Fernet key as-is; otherwise derive one from the secret (SHA-256).

    Tolerates keys mangled by copy/paste. Use a long random value (32+ chars).
    """
    try:
        Fernet(secret.encode())
        return secret.encode()
    except (ValueError, TypeError):
        return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _fernet() -> Fernet:
    if not settings.encryption_key:
        raise RuntimeError("ENCRYPTION_KEY is not set (generate with Fernet.generate_key())")
    return Fernet(derive_key(settings.encryption_key))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
