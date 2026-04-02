"""Tool execution endpoints — called by main API agent core."""
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.database import AsyncSessionLocal
from app.tools.gmail import read_emails, send_email
from app.tools.calendar import list_events, create_event

router = APIRouter(prefix="/tools")
logger = logging.getLogger(__name__)


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


class GmailReadInput(BaseModel):
    user_id: str
    max_results: int = 10


class GmailSendInput(BaseModel):
    user_id: str
    to: str
    subject: str
    body: str


class CalendarListInput(BaseModel):
    user_id: str
    max_results: int = 10


class CalendarCreateInput(BaseModel):
    user_id: str
    summary: str
    start_datetime: str
    end_datetime: str
    timezone_str: str = "UTC"
    description: Optional[str] = ""


@router.post("/gmail/read")
async def gmail_read(body: GmailReadInput, db: AsyncSession = Depends(_get_db)):
    emails = await read_emails(body.user_id, body.max_results, db)
    return {"emails": emails}


@router.post("/gmail/send")
async def gmail_send(body: GmailSendInput, db: AsyncSession = Depends(_get_db)):
    return await send_email(body.user_id, body.to, body.subject, body.body, db)


@router.post("/calendar/events")
async def calendar_events(body: CalendarListInput, db: AsyncSession = Depends(_get_db)):
    events = await list_events(body.user_id, body.max_results, db)
    return {"events": events}


@router.post("/calendar/create")
async def calendar_create(body: CalendarCreateInput, db: AsyncSession = Depends(_get_db)):
    return await create_event(
        body.user_id, body.summary, body.start_datetime,
        body.end_datetime, body.timezone_str, body.description or "", db
    )
