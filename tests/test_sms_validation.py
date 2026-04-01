"""
Tests for Twilio SMS webhook signature validation.

Verifies that:
- Requests with invalid signatures return 403
- Requests are accepted in mock/dev mode (no real Twilio creds)
- Requests with valid signatures are processed normally
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_twilio_validation_skipped_in_mock_mode():
    """Signature check must be skipped when is_mock_sms is True."""
    from app.routes.sms import _validate_twilio_signature
    with patch("app.routes.sms.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(is_mock_sms=True)
        request = MagicMock()
        result = await _validate_twilio_signature(request, x_twilio_signature="any")
        assert result is None  # No exception raised


@pytest.mark.asyncio
async def test_twilio_validation_rejects_invalid_signature():
    """Invalid Twilio signature must raise HTTPException 403."""
    from app.routes.sms import _validate_twilio_signature
    with patch("app.routes.sms.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            is_mock_sms=False,
            twilio_auth_token="test_token",
        )
        with patch("app.routes.sms.RequestValidator") as mock_validator_cls:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = False
            mock_validator_cls.return_value = mock_validator

            mock_request = MagicMock()
            mock_request.url = MagicMock()
            mock_request.url.__str__ = lambda self: "https://example.com/sms/inbound"
            mock_request.client = MagicMock(host="1.2.3.4")
            mock_request.form = AsyncMock(return_value={"From": "+1555", "Body": "hello"})

            with pytest.raises(HTTPException) as exc_info:
                await _validate_twilio_signature(mock_request, x_twilio_signature="bad_sig")
            assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_twilio_validation_passes_valid_signature():
    """Valid Twilio signature must not raise an exception."""
    from app.routes.sms import _validate_twilio_signature
    with patch("app.routes.sms.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            is_mock_sms=False,
            twilio_auth_token="test_token",
        )
        with patch("app.routes.sms.RequestValidator") as mock_validator_cls:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = True
            mock_validator_cls.return_value = mock_validator

            mock_request = MagicMock()
            mock_request.url = MagicMock()
            mock_request.url.__str__ = lambda self: "https://example.com/sms/inbound"
            mock_request.form = AsyncMock(return_value={})

            result = await _validate_twilio_signature(mock_request, x_twilio_signature="good_sig")
            assert result is None  # No exception
