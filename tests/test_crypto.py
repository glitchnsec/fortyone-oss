import os
import pytest


def test_encrypt_decrypt_round_trip(monkeypatch):
    """Encrypted value must decrypt back to original."""
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)

    # Clear lru_cache so the new key is picked up
    from app.crypto import _fernet
    _fernet.cache_clear()

    from app.crypto import encrypt, decrypt
    original = "supersecret_oauth_token_abc123"
    ciphertext = encrypt(original)
    assert ciphertext != original
    assert decrypt(ciphertext) == original


def test_encrypt_produces_different_ciphertext_each_call(monkeypatch):
    """Fernet uses random IV — same plaintext must produce different ciphertext."""
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    from app.crypto import _fernet
    _fernet.cache_clear()

    from app.crypto import encrypt
    c1 = encrypt("same value")
    c2 = encrypt("same value")
    assert c1 != c2


def test_encrypt_empty_string(monkeypatch):
    """Empty string must round-trip."""
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    from app.crypto import _fernet
    _fernet.cache_clear()

    from app.crypto import encrypt, decrypt
    assert decrypt(encrypt("")) == ""


def test_encrypt_multiline_string(monkeypatch):
    """Multi-line strings with special chars must round-trip."""
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    from app.crypto import _fernet
    _fernet.cache_clear()

    from app.crypto import encrypt, decrypt
    value = "line one\nline two with 'quotes' and \"double quotes\""
    assert decrypt(encrypt(value)) == value


def test_import_without_key_does_not_raise():
    """Importing crypto module must not raise RuntimeError even without key set."""
    import importlib
    import sys
    # Remove ENCRYPTION_KEY from env if present
    env_backup = os.environ.pop("ENCRYPTION_KEY", None)
    try:
        if "app.crypto" in sys.modules:
            del sys.modules["app.crypto"]
        import app.crypto  # Should not raise
    finally:
        if env_backup:
            os.environ["ENCRYPTION_KEY"] = env_backup


def test_encrypt_without_key_raises_runtime_error():
    """Calling encrypt() without ENCRYPTION_KEY set must raise RuntimeError."""
    import os
    env_backup = os.environ.pop("ENCRYPTION_KEY", None)
    try:
        from app.crypto import _fernet
        _fernet.cache_clear()
        from app.crypto import encrypt
        with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
            encrypt("test")
    finally:
        if env_backup:
            os.environ["ENCRYPTION_KEY"] = env_backup
        from app.crypto import _fernet
        _fernet.cache_clear()
