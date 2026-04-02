"""Unit tests for web search task handler.

Tests graceful degradation when BRAVE_API_KEY is unset,
response formatting, error handling for rate limits and failures.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.tasks.web_search import handle_web_search


SAMPLE_PAYLOAD = {
    "job_id": "test-123",
    "phone": "+15551234567",
    "message": "weather in Austin",
}


@pytest.mark.asyncio
async def test_no_api_key_returns_fallback():
    """When BRAVE_API_KEY is not set, returns a helpful message (not a crash)."""
    with patch("app.tasks.web_search.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(brave_api_key="")
        result = await handle_web_search(SAMPLE_PAYLOAD)

    assert result["job_id"] == "test-123"
    assert result["phone"] == "+15551234567"
    assert "not configured" in result["response"].lower() or "BRAVE_API_KEY" in result["response"]


@pytest.mark.asyncio
async def test_successful_search_formats_results():
    """Successful search returns formatted top 3 results."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "web": {
            "results": [
                {"title": "Austin Weather", "description": "Sunny 85F", "url": "https://example.com/1"},
                {"title": "Forecast", "description": "Clear skies", "url": "https://example.com/2"},
                {"title": "Climate", "description": "Hot and humid", "url": "https://example.com/3"},
                {"title": "Extra", "description": "Ignored", "url": "https://example.com/4"},
            ]
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.tasks.web_search.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(brave_api_key="test-key")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await handle_web_search(SAMPLE_PAYLOAD)

    assert "Austin Weather" in result["response"]
    assert "Forecast" in result["response"]
    assert "Climate" in result["response"]
    assert "Extra" not in result["response"]  # Only top 3


@pytest.mark.asyncio
async def test_empty_results_returns_no_results_message():
    """When search returns no results, says so clearly."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"web": {"results": []}}
    mock_response.raise_for_status = MagicMock()

    with patch("app.tasks.web_search.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(brave_api_key="test-key")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await handle_web_search(SAMPLE_PAYLOAD)

    assert "no results" in result["response"].lower()


@pytest.mark.asyncio
async def test_rate_limit_returns_retry_message():
    """429 rate limit returns a user-friendly retry message."""
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 429

    with patch("app.tasks.web_search.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(brave_api_key="test-key")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.HTTPStatusError(
                "rate limited", request=MagicMock(), response=mock_response
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await handle_web_search(SAMPLE_PAYLOAD)

    assert "rate limit" in result["response"].lower()


@pytest.mark.asyncio
async def test_handler_returns_required_fields():
    """Every response must contain job_id, phone, and response."""
    with patch("app.tasks.web_search.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(brave_api_key="")
        result = await handle_web_search(SAMPLE_PAYLOAD)

    assert "job_id" in result
    assert "phone" in result
    assert "response" in result
