"""
Gap tests for embed_text() — timeout, exception, and truncation paths.

Existing test_memory_engine.py tests: no-key skip, successful vector, truncation (indirect).
These tests cover the error-handling paths directly.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_embed_text_timeout_returns_empty():
    """asyncio.TimeoutError during embedding call → returns [] (not crash)."""
    from app.memory.embeddings import embed_text

    mock_settings = MagicMock()
    mock_settings.has_llm = True

    mock_client = MagicMock()
    mock_client.embeddings.create = AsyncMock(side_effect=asyncio.TimeoutError)

    with patch("app.config.get_settings", return_value=mock_settings), \
         patch("app.tasks._llm._client", return_value=mock_client):
        result = await embed_text("test input")

    assert result == []


@pytest.mark.asyncio
async def test_embed_text_exception_returns_empty():
    """Generic exception during embedding call → returns [] gracefully."""
    from app.memory.embeddings import embed_text

    mock_settings = MagicMock()
    mock_settings.has_llm = True

    mock_client = MagicMock()
    mock_client.embeddings.create = AsyncMock(side_effect=RuntimeError("API down"))

    with patch("app.config.get_settings", return_value=mock_settings), \
         patch("app.tasks._llm._client", return_value=mock_client):
        result = await embed_text("test input")

    assert result == []


@pytest.mark.asyncio
async def test_embed_text_truncates_at_max_chars():
    """Input longer than MAX_INPUT_CHARS (8000) is truncated before API call."""
    from app.memory.embeddings import embed_text, MAX_INPUT_CHARS

    mock_settings = MagicMock()
    mock_settings.has_llm = True

    captured_input = []

    async def fake_create(**kwargs):
        captured_input.append(kwargs.get("input", ""))
        resp = MagicMock()
        resp.data = [MagicMock(embedding=[0.1] * 1536)]
        return resp

    mock_client = MagicMock()
    mock_client.embeddings.create = fake_create

    long_text = "x" * 10000  # well over 8000

    with patch("app.config.get_settings", return_value=mock_settings), \
         patch("app.tasks._llm._client", return_value=mock_client):
        result = await embed_text(long_text)

    assert len(result) == 1536
    assert len(captured_input[0]) == MAX_INPUT_CHARS
