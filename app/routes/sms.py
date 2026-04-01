"""
Twilio SMS webhook.

POST /sms/inbound — Twilio calls this for every inbound SMS.

Returns an empty TwiML 200 immediately (Twilio retries on slow responses).
All real work runs in a BackgroundTask after the HTTP response is committed.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Form
from fastapi.responses import Response

from app.channels.sms import SMSChannel
from app.database import AsyncSessionLocal
from app.memory.store import MemoryStore
from app.queue.client import queue_client

logger = logging.getLogger(__name__)
router = APIRouter()

_channel = SMSChannel()
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


async def _process_inbound(address: str, body: str) -> None:
    from app.core.pipeline import MessagePipeline

    async with AsyncSessionLocal() as db:
        try:
            store = MemoryStore(db)
            pipeline = MessagePipeline(channel=_channel, queue=queue_client, store=store)
            await pipeline.handle(address=address, body=body)
        except Exception as exc:
            logger.error("Pipeline error address=%s: %s", address, exc, exc_info=True)
            try:
                await _channel.send(address, _channel.error_reply)
            except Exception:
                pass


@router.post("/inbound")
async def inbound_sms(
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
) -> Response:
    logger.info("INBOUND  channel=sms  from=%s  body=%r", From, Body[:80])
    background_tasks.add_task(_process_inbound, From, Body.strip())
    return Response(content=_EMPTY_TWIML, media_type="text/xml")
