"""
Fernet symmetric encryption utility.

Provides encrypt/decrypt for sensitive fields stored in the database (OAuth tokens, PII).
The key is loaded lazily from ENCRYPTION_KEY on first use — importing this module is safe
without the key being set.

Key generation (run once, store in secrets manager or .env):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

IMPORTANT: Do not rotate ENCRYPTION_KEY without a migration script.
Rotating the key renders all previously encrypted values unreadable.
Use MultiFernet for key rotation (see Phase 2 notes in RESEARCH.md Pitfall 5).
"""
import os
from functools import lru_cache

from cryptography.fernet import Fernet


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Return the Fernet instance. Cached after first call. Raises if key is absent."""
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY environment variable is required for encryption. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    """Encrypt a plaintext string. Returns base64-encoded ciphertext as a str."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a ciphertext string produced by encrypt(). Returns plaintext."""
    return _fernet().decrypt(value.encode()).decode()
