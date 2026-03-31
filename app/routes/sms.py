"""
SMS webhook routes.

POST /sms/inbound  — Twilio calls this for every inbound SMS.

The handler returns an empty TwiML 200 response to Twilio immediately
(Twilio requires a fast response or it retries).  All real work
(ACK send, intent classification, queue push) happens in a BackgroundTask
so it runs AFTER the HTTP response is committed.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import Response

from app.database import SessionLocal
from app.memory.store import MemoryStore
from app.queue.client import queue_client
from app.sms.client import SMSClient

logger = logging.getLogger(__name__)
router = APIRouter()

_sms_client = SMSClient()

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


async def _process_inbound(phone: str, body: str) -> None:
    """Runs in a background task after Twilio has received our 200."""
    from app.core.pipeline import MessagePipeline

    db = SessionLocal()
    try:
        store = MemoryStore(db)
        pipeline = MessagePipeline(sms=_sms_client, queue=queue_client, store=store)
        await pipeline.handle(phone=phone, body=body)
    except Exception as exc:
        logger.error("Pipeline error phone=%s: %s", phone, exc, exc_info=True)
        # Best-effort error reply so the user isn't left hanging
        try:
            await _sms_client.send(phone, "Something went wrong on my end — try again in a moment.")
        except Exception:
            pass
    finally:
        db.close()


@router.post("/inbound")
async def inbound_sms(
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
) -> Response:
    """
    Twilio SMS webhook.

    Required Twilio form fields:
      From — sender's phone number (e.g. +15551234567)
      Body — message text
    """
    logger.info("INBOUND sms from=%s body=%r", From, Body[:80])

    # Schedule the real work to run AFTER we return 200 to Twilio
    background_tasks.add_task(_process_inbound, From, Body.strip())

    return Response(content=_EMPTY_TWIML, media_type="text/xml")
