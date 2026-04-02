"""Config validation tests — catch empty required env vars BEFORE hitting external APIs.

This test would have caught the missing GOOGLE_CLIENT_ID issue.
"""
import pytest
from app.config import Settings


def test_google_client_id_default_is_empty():
    """Default google_client_id is empty string — code must handle this gracefully."""
    s = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite://",
    )
    assert s.google_client_id == ""


def test_google_client_secret_default_is_empty():
    """Default google_client_secret is empty string."""
    s = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite://",
    )
    assert s.google_client_secret == ""


def test_encryption_key_default_is_empty():
    """Default encryption_key is empty — crypto module should fail gracefully."""
    s = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite://",
    )
    assert s.encryption_key == ""
