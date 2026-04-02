"""Dashboard API routes — protected endpoints for the React SPA.

Endpoints:
  GET  /api/v1/me              — current user profile
  PATCH /api/v1/me/assistant   — update assistant name / personality
  DELETE /api/v1/me            — permanently delete account and all user data
  GET  /api/v1/conversations   — paginated message history (user-scoped)
  GET  /api/v1/connections     — proxy to connections service
  POST /api/v1/connections/initiate — initiate OAuth flow (proxy)
  DELETE /api/v1/connections/{conn_id} — delete a connection (proxy)

All routes require a valid Bearer JWT token (get_current_user dependency).
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.memory.models import Message, User
from app.middleware.auth import get_current_user

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _connections_client():
    """Yield a short-lived httpx client pointed at the connections service."""
    s = get_settings()
    async with httpx.AsyncClient(base_url=s.connections_service_url, timeout=10.0) as client:
        yield client


# ─── User / Me ────────────────────────────────────────────────────────────────

@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    """Return basic profile for the authenticated user."""
    return {
        "user_id": user.id,
        "email": user.email,
        "phone": user.phone,
        "phone_verified": user.phone_verified,
        "assistant_name": user.assistant_name,
        "personality_notes": getattr(user, "personality_notes", None),
    }


class AssistantUpdate(BaseModel):
    assistant_name: str
    personality_notes: Optional[str] = None


@router.delete("/me", status_code=204)
async def delete_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Permanently delete the authenticated user and all related data.

    Cascade order:
      1. Best-effort: notify connections service to purge OAuth tokens for this user.
      2. Delete User row — SQLAlchemy cascade="all, delete-orphan" removes Memories,
         Messages, Tasks, and UserSessions automatically.
    Returns 204 on success (no body).
    """
    # Step 1: best-effort connections cleanup (non-fatal if service is down)
    try:
        resp = await client.delete(f"/connections/user/{user.id}")
        resp.raise_for_status()
    except Exception as e:
        logger.warning("connections purge failed user_id=%s error=%s", user.id, e)

    # Step 2: delete User row — cascades to all child tables
    db_user = await db.get(User, user.id)
    if db_user:
        await db.delete(db_user)
        await db.commit()
    logger.info("ACCOUNT_DELETED  user_id=%s  email=%s", user.id, user.email)


@router.patch("/me/assistant")
async def update_assistant(
    body: AssistantUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Update the assistant's name (and optional personality notes)."""
    values = {"assistant_name": body.assistant_name}
    if body.personality_notes is not None:
        values["personality_notes"] = body.personality_notes
    await db.execute(
        update(User).where(User.id == user.id).values(**values)
    )
    await db.commit()
    return {"assistant_name": body.assistant_name, "personality_notes": body.personality_notes}


# ─── Conversations ────────────────────────────────────────────────────────────

@router.get("/conversations")
async def list_conversations(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return paginated message history for the authenticated user."""
    offset = (page - 1) * limit
    result = await db.execute(
        select(Message)
        .where(Message.user_id == user.id)
        .order_by(Message.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    messages = result.scalars().all()
    total_result = await db.execute(
        select(func.count()).select_from(Message).where(Message.user_id == user.id)
    )
    total = total_result.scalar_one()
    return {
        "conversations": [
            {
                "id": m.id,
                "direction": m.direction,
                "body": m.body,
                "intent": m.intent,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
        "total": total,
        "page": page,
        "limit": limit,
    }


# ─── Connections proxy ────────────────────────────────────────────────────────

@router.get("/connections")
async def list_connections(
    user: User = Depends(get_current_user),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Proxy GET /connections/{user_id} to the connections service."""
    try:
        resp = await client.get(f"/connections/{user.id}")
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        logger.error("connections service error: %s", e)
        raise HTTPException(502, "Connections service unavailable")


class InitiateBody(BaseModel):
    provider: str


@router.post("/connections/initiate")
async def initiate_connection(
    body: InitiateBody,
    user: User = Depends(get_current_user),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Initiate OAuth flow: proxy to connections service oauth initiate endpoint."""
    try:
        resp = await client.get(
            f"/oauth/initiate/{body.provider}", params={"user_id": user.id}
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        logger.error("initiate error: %s", e)
        raise HTTPException(502, "Connections service unavailable")


@router.delete("/connections/{conn_id}", status_code=204)
async def delete_connection(
    conn_id: str,
    user: User = Depends(get_current_user),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Delete a connection: proxy DELETE to the connections service."""
    try:
        resp = await client.delete(f"/connections/{conn_id}")
        if resp.status_code == 404:
            raise HTTPException(404, "Connection not found")
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("delete connection error: %s", e)
        raise HTTPException(502, "Connections service unavailable")
