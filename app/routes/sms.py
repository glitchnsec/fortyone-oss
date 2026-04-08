"""
Twilio SMS webhook.

POST /sms/inbound — Twilio calls this for every inbound SMS.

Returns an empty TwiML 200 immediately (Twilio retries on slow responses).
All real work runs in a BackgroundTask after the HTTP response is committed.

Signature validation:
  Every request is validated against X-Twilio-Signature using the Twilio
  RequestValidator.  Validation is skipped when MOCK_SMS=true or when no
  Twilio credentials are configured (TWILIO_ACCOUNT_SID is empty).

  IMPORTANT: Behind a reverse proxy, set BASE_URL env var or configure
  X-Forwarded-Proto forwarding so the reconstructed URL matches what Twilio
  signed.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Header, HTTPException, Request
from fastapi.responses import Response
from twilio.request_validator import RequestValidator

from app.channels.sms import SMSChannel
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.memory.store import MemoryStore
from app.queue.client import queue_client

logger = logging.getLogger(__name__)
router = APIRouter()

_channel = SMSChannel()
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


async def _validate_twilio_signature(
    request: Request,
    x_twilio_signature: str = Header(default=""),
) -> None:
    """
    Validate that the request came from Twilio by verifying the X-Twilio-Signature header.
    Skipped in mock/dev mode when Twilio credentials are not configured.

    IMPORTANT: Behind a reverse proxy, set BASE_URL env var or configure
    X-Forwarded-Proto forwarding so the URL matches what Twilio signed.
    """
    settings = get_settings()
    if settings.is_mock_sms:
        return  # Skip validation in mock/dev mode (no real Twilio creds)
    validator = RequestValidator(settings.twilio_auth_token)
    # Reconstruct the URL Twilio signed — behind a reverse proxy (ngrok, nginx),
    # request.url is http://localhost but Twilio signed against the public https URL.
    if settings.base_url:
        url = settings.base_url.rstrip("/") + request.url.path
    else:
        url = str(request.url)
    # For form-encoded bodies, pass the form params dict (Twilio signs these)
    form = await request.form()
    params = dict(form)
    if not validator.validate(url, params, x_twilio_signature):
        logger.warning(
            "REJECTED invalid Twilio signature from=%s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


async def _process_inbound(address: str, body: str) -> None:
    from app.core.pipeline import MessagePipeline

    async with AsyncSessionLocal() as db:
        try:
            store = MemoryStore(db)

            # D-05/AUTH-02: Check if this phone number belongs to a registered user.
            # If not, send a registration link and do NOT create an account or bypass
            # phone verification.
            existing_user = await store.lookup_by_phone(address)
            if not existing_user:
                s = get_settings()
                base = s.base_url.rstrip('/') if s.base_url else 'https://your-app.com'
                # Prefill phone (E.164 from Twilio) so the user doesn't have to retype it
                from urllib.parse import quote
                registration_url = f"{base}/auth/register?phone={quote(address)}"
                reply = f"Hi! To use Operator, please create your account here: {registration_url}"
                logger.info(
                    "UNREGISTERED  channel=sms  from=%s  sending_registration_link",
                    address,
                )
                await _channel.send(address, reply)
                return

            pipeline = MessagePipeline(
                channel=_channel, queue=queue_client, store=store)
            await pipeline.handle(address=address, body=body)
        except Exception as exc:
            logger.error("Pipeline error address=%s: %s",
                         address, exc, exc_info=True)
            try:
                await _channel.send(address, _channel.error_reply)
            except Exception:
                pass


@router.post("/inbound")
async def inbound_sms(
    request: Request,
    background_tasks: BackgroundTasks,
    _: None = Depends(_validate_twilio_signature),
    From: str = Form(...),
    Body: str = Form(...),
) -> Response:
    logger.info("INBOUND  channel=sms  from=%s  body=%r", From, Body[:80])
    background_tasks.add_task(_process_inbound, From, Body.strip())
    return Response(content=_EMPTY_TWIML, media_type="text/xml")
