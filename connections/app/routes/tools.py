"""Tool execution endpoints — called by main API agent core."""
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.database import AsyncSessionLocal
from app.tools.gmail import read_emails, send_email
from app.tools.calendar import list_events, create_event
from app.tools.slack import slack_read_channels, slack_get_workspace, slack_read_threads

router = APIRouter(prefix="/tools")
logger = logging.getLogger(__name__)


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


class GmailReadInput(BaseModel):
    user_id: str
    max_results: int = 10
    persona_id: str | None = None


class GmailSendInput(BaseModel):
    user_id: str
    to: str
    subject: str
    body: str
    persona_id: str | None = None


class CalendarListInput(BaseModel):
    user_id: str
    max_results: int = 10
    persona_id: str | None = None


class CalendarCreateInput(BaseModel):
    user_id: str
    summary: str
    start_datetime: str
    end_datetime: str
    timezone_str: str = "UTC"
    description: Optional[str] = ""
    persona_id: str | None = None


@router.post("/gmail/read_emails")
async def gmail_read(body: GmailReadInput, db: AsyncSession = Depends(_get_db)):
    emails = await read_emails(body.user_id, body.max_results, db, persona_id=body.persona_id)
    return {"emails": emails}


@router.post("/gmail/send_email")
async def gmail_send(body: GmailSendInput, db: AsyncSession = Depends(_get_db)):
    return await send_email(body.user_id, body.to, body.subject, body.body, db, persona_id=body.persona_id)


@router.post("/calendar/list_events")
async def calendar_events(body: CalendarListInput, db: AsyncSession = Depends(_get_db)):
    events = await list_events(body.user_id, body.max_results, db, persona_id=body.persona_id)
    return {"events": events}


@router.post("/calendar/create_event")
async def calendar_create(body: CalendarCreateInput, db: AsyncSession = Depends(_get_db)):
    return await create_event(
        body.user_id, body.summary, body.start_datetime,
        body.end_datetime, body.timezone_str, body.description or "", db,
        persona_id=body.persona_id,
    )


# ── Slack tools ─────────────────────────────────────────────────────────

class SlackReadChannelsInput(BaseModel):
    user_id: str
    channel_id: str | None = None
    limit: int = Field(default=20, ge=1, le=200)
    persona_id: str | None = None


class SlackGetWorkspaceInput(BaseModel):
    user_id: str
    persona_id: str | None = None


class SlackReadThreadsInput(BaseModel):
    user_id: str
    channel_id: str
    thread_ts: str
    limit: int = Field(default=50, ge=1, le=200)
    persona_id: str | None = None


@router.post("/slack/slack_read_channels")
async def slack_channels(body: SlackReadChannelsInput, db: AsyncSession = Depends(_get_db)):
    return await slack_read_channels(
        body.user_id, db, persona_id=body.persona_id,
        channel_id=body.channel_id, limit=body.limit,
    )


@router.post("/slack/slack_get_workspace")
async def slack_workspace(body: SlackGetWorkspaceInput, db: AsyncSession = Depends(_get_db)):
    return await slack_get_workspace(body.user_id, db, persona_id=body.persona_id)


@router.post("/slack/slack_read_threads")
async def slack_threads(body: SlackReadThreadsInput, db: AsyncSession = Depends(_get_db)):
    return await slack_read_threads(
        body.user_id, db, persona_id=body.persona_id,
        channel_id=body.channel_id, thread_ts=body.thread_ts, limit=body.limit,
    )
