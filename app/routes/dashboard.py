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
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.memory.models import Message, User, Goal, ActionLog, Persona, UserProfile, ProactivePreference
from app.memory.store import MemoryStore
from app.middleware.auth import get_current_user

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


async def _try_record_milestone(user_id: str, milestone_name: str):
    """Best-effort milestone recording. Silent on failure."""
    try:
        from app.database import AsyncSessionLocal
        from app.memory.store import MemoryStore
        async with AsyncSessionLocal() as db:
            store = MemoryStore(db)
            await store.record_milestone(user_id, milestone_name)
    except Exception:
        pass  # Non-critical -- milestones are advisory


async def _invalidate_user_capabilities(user_id: str):
    """Best-effort capability cache invalidation after connection changes (D-04)."""
    try:
        from app.core.capabilities import invalidate_capabilities
        from app.queue.client import queue_client
        if queue_client._redis:
            await invalidate_capabilities(queue_client._redis, user_id)
    except Exception as exc:
        logger.warning("capabilities invalidation failed user=%s error=%s", str(user_id)[:8], exc)


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _connections_client():
    """Yield a short-lived httpx client pointed at the connections service."""
    s = get_settings()
    headers = {"X-Service-Token": s.service_auth_token} if s.service_auth_token else {}
    async with httpx.AsyncClient(
        base_url=s.connections_service_url, timeout=10.0, headers=headers,
    ) as client:
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
        "name": user.name,
        "timezone": user.timezone,
        "assistant_name": user.assistant_name,
        "personality_notes": getattr(user, "personality_notes", None),
        "role": user.role.name if user.role else "user",
    }


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    timezone: Optional[str] = None


@router.patch("/me")
async def update_me(
    body: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Update user profile fields (name, timezone)."""
    # Re-fetch user within this session so changes persist on commit
    result = await db.execute(select(User).where(User.id == user.id))
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(404, "User not found")
    if body.name is not None:
        db_user.name = body.name
    if body.timezone is not None:
        import zoneinfo
        try:
            zoneinfo.ZoneInfo(body.timezone)
        except (KeyError, zoneinfo.ZoneInfoNotFoundError):
            raise HTTPException(400, f"Invalid timezone: '{body.timezone}'")
        db_user.timezone = body.timezone
    await db.commit()
    return {"ok": True, "name": db_user.name, "timezone": db_user.timezone}


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
    """Update the assistant's name (and optional personality notes).

    On the first call (welcome_sms_sent=False), sends the one-time welcome SMS.
    This fires after onboarding step 3 (name your assistant) so the message
    can include the assistant's name.
    """
    should_send_welcome = not getattr(user, "welcome_sms_sent", False) and user.phone

    values = {"assistant_name": body.assistant_name}
    if body.personality_notes is not None:
        values["personality_notes"] = body.personality_notes
    # Set welcome_sms_sent flag in the same transaction to prevent duplicates
    if should_send_welcome:
        values["welcome_sms_sent"] = True
    await db.execute(
        update(User).where(User.id == user.id).values(**values)
    )
    await db.commit()

    # Fire-and-forget the actual SMS delivery (flag already persisted above)
    if should_send_welcome:
        import asyncio
        asyncio.create_task(_send_welcome_sms(str(user.id), user.phone, user.name, body.assistant_name))

    return {"assistant_name": body.assistant_name, "personality_notes": body.personality_notes}


async def _send_welcome_sms(user_id: str, phone: str, user_name: str | None, assistant_name: str) -> None:
    """Send one-time welcome SMS. Fire-and-forget (flag already persisted by caller)."""
    try:
        from app.channels.sms import SMSChannel
        channel = SMSChannel()
        greeting = f"Hey {user_name}!" if user_name else "Hey!"
        message = (
            f"{greeting} Your assistant {assistant_name} is ready. "
            "Just text me here anytime -- I can set reminders, remember "
            "things for you, and help manage your day."
        )
        sent = await channel.send(phone, message)
        logger.info("WELCOME_SMS  user=%s  phone=***%s", user_id[:8], phone[-4:] if len(phone) >= 4 else phone)

        # Persist welcome SMS so it appears in conversation history
        if sent:
            async with AsyncSessionLocal() as db:
                store = MemoryStore(db)
                await store.store_message(
                    user_id=user_id,
                    direction="outbound",
                    body=message,
                    state="confirm",
                    channel="sms",
                )
    except Exception as exc:
        logger.warning("Welcome SMS failed user=%s: %s", user_id[:8], exc)


# ─── Conversations ────────────────────────────────────────────────────────────

@router.get("/conversations")
async def list_conversations(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    channel: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return paginated message history for the authenticated user.

    Optional query params:
      channel — filter by channel ("sms" or "slack"). Omit for all channels.
    """
    offset = (page - 1) * limit

    # Base filters
    filters = [Message.user_id == user.id]
    if channel is not None:
        filters.append(Message.channel == channel)

    result = await db.execute(
        select(Message)
        .where(*filters)
        .order_by(Message.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    messages = result.scalars().all()
    total_result = await db.execute(
        select(func.count()).select_from(Message).where(*filters)
    )
    total = total_result.scalar_one()
    return {
        "conversations": [
            {
                "id": m.id,
                "direction": m.direction,
                "body": m.body,
                "intent": m.intent,
                "channel": m.channel or "sms",
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


class MCPConnectBody(BaseModel):
    persona_id: str | None = None
    server_url: str
    auth_type: str = "none"
    api_key: str | None = None
    name: str | None = None


class MCPOAuthInitiateBody(BaseModel):
    persona_id: str | None = None
    server_url: str
    name: str | None = None


class MCPOAuthCallbackBody(BaseModel):
    code: str
    state: str


@router.get("/mcp/oauth/client-metadata")
async def mcp_oauth_client_metadata():
    """Public client metadata document for MCP auth servers using metadata-based client IDs."""
    settings = get_settings()
    return {
        "client_name": "Operator MCP Client",
        "redirect_uris": [f"{settings.dashboard_url}/connections/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


@router.post("/mcp/connect")
async def proxy_mcp_connect(
    body: MCPConnectBody,
    user: User = Depends(get_current_user),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Proxy MCP connect request to the connections service."""
    try:
        resp = await client.post(
            "/mcp/connect",
            json={
                "user_id": str(user.id),
                "persona_id": body.persona_id,
                "server_url": body.server_url,
                "auth_type": body.auth_type,
                "api_key": body.api_key,
                "name": body.name,
            },
        )
        if resp.status_code >= 400:
            data = resp.json()
            raise HTTPException(resp.status_code, data.get("detail", "MCP connect failed"))
        await _invalidate_user_capabilities(str(user.id))
        return resp.json()
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        logger.error("mcp connect proxy error: %s", e)
        raise HTTPException(502, "Connections service unavailable")


@router.post("/mcp/oauth/initiate")
async def proxy_mcp_oauth_initiate(
    body: MCPOAuthInitiateBody,
    user: User = Depends(get_current_user),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Proxy MCP OAuth initiation to the connections service."""
    try:
        resp = await client.post(
            "/mcp/oauth/initiate",
            json={
                "user_id": str(user.id),
                "persona_id": body.persona_id,
                "server_url": body.server_url,
                "name": body.name,
            },
        )
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, resp.json())
        return resp.json()
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        logger.error("mcp oauth initiate proxy error: %s", e)
        raise HTTPException(502, "Connections service unavailable")


@router.post("/mcp/oauth/callback")
async def proxy_mcp_oauth_callback(
    body: MCPOAuthCallbackBody,
    user: User = Depends(get_current_user),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Complete the MCP OAuth flow after the browser returns to the dashboard."""
    try:
        resp = await client.post("/mcp/oauth/callback", json=body.model_dump())
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, resp.json())
        await _invalidate_user_capabilities(str(user.id))
        return resp.json()
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        logger.error("mcp oauth callback proxy error: %s", e)
        raise HTTPException(502, "Connections service unavailable")


class InitiateBody(BaseModel):
    provider: str
    persona_id: str  # UUID — required per D-01 for per-persona connections


@router.post("/connections/initiate")
async def initiate_connection(
    body: InitiateBody,
    user: User = Depends(get_current_user),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Initiate OAuth flow: proxy to connections service oauth initiate endpoint."""
    try:
        resp = await client.get(
            f"/oauth/initiate/{body.provider}",
            params={"user_id": user.id, "persona_id": body.persona_id},
        )
        resp.raise_for_status()
        await _invalidate_user_capabilities(str(user.id))
        return resp.json()
    except httpx.HTTPError as e:
        logger.error("initiate error: %s", e)
        raise HTTPException(502, "Connections service unavailable")


@router.patch("/connections/{conn_id}")
async def update_connection(
    conn_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Update a connection (e.g. persona assignment): proxy PATCH with ownership check."""
    body = await request.json()
    try:
        # Verify ownership: fetch user's connections and check conn_id belongs to them
        verify_resp = await client.get(f"/connections/{user.id}")
        if verify_resp.status_code == 200:
            connections = verify_resp.json().get("connections", [])
            owned_ids = {c["id"] for c in connections}
            if conn_id not in owned_ids:
                raise HTTPException(403, "Not your connection")
        resp = await client.patch(f"/connections/{conn_id}", json=body)
        resp.raise_for_status()
        await _invalidate_user_capabilities(str(user.id))
        return resp.json()
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        logger.error("update connection error: %s", e)
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
        await _invalidate_user_capabilities(str(user.id))
    except httpx.HTTPError as e:
        logger.error("delete connection error: %s", e)
        raise HTTPException(502, "Connections service unavailable")


# ─── Request Models ──────────────────────────────────────────────────────────

class GoalCreate(BaseModel):
    title: str
    framework: str = "custom"  # okr | smart | custom
    description: Optional[str] = None
    target_date: Optional[str] = None  # ISO 8601
    persona_id: Optional[str] = None
    parent_goal_id: Optional[str] = None
    metadata: Optional[dict] = None


class GoalUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    target_date: Optional[str] = None
    status: Optional[str] = None  # active | completed | archived
    framework: Optional[str] = None
    metadata_json: Optional[str] = None


class PersonaCreate(BaseModel):
    name: str
    description: Optional[str] = None
    tone_notes: Optional[str] = None


class PersonaUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tone_notes: Optional[str] = None
    is_active: Optional[bool] = None


class ProfileEntryCreate(BaseModel):
    section: str
    label: str
    content: str
    persona_id: Optional[str] = None


class TaskCreate(BaseModel):
    title: str
    task_type: str = "reminder"  # reminder | follow_up | schedule
    description: Optional[str] = None
    due_at: Optional[str] = None  # ISO 8601
    metadata: Optional[dict] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_at: Optional[str] = None
    task_type: Optional[str] = None
    completed: Optional[bool] = None


# ─── Tasks ──────────────────────────────────────────────────────────────────

@router.get("/tasks")
async def list_tasks(
    status: str = Query("active"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return tasks for the authenticated user. status: active, completed, all."""
    store = MemoryStore(db)
    tasks = await store.get_tasks(user.id, status=status)
    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "task_type": t.task_type,
                "description": t.description,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "completed": t.completed,
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tasks
        ]
    }


@router.post("/tasks", status_code=201)
async def create_task(
    body: TaskCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Create a new task."""
    from datetime import datetime, timezone
    store = MemoryStore(db)
    due_at = None
    if body.due_at:
        from dateutil.parser import parse as parse_date
        import zoneinfo
        parsed = parse_date(body.due_at)
        if parsed.tzinfo is None:
            user_tz_name = getattr(user, "timezone", None) or "America/New_York"
            try:
                user_tz = zoneinfo.ZoneInfo(user_tz_name)
                parsed = parsed.replace(tzinfo=user_tz)
            except (KeyError, Exception):
                parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed < datetime.now(timezone.utc):
            raise HTTPException(400, "Due date cannot be in the past")
        due_at = parsed
    task = await store.store_task(
        user_id=user.id,
        task_type=body.task_type,
        title=body.title,
        description=body.description,
        due_at=due_at,
        metadata=body.metadata,
    )
    # Schedule reminder delivery at due_at via Redis sorted set
    if due_at:
        from app.tasks.reminder import schedule_task_reminder
        phone = getattr(user, "phone", "") or ""
        await schedule_task_reminder(user.id, task.id, task.title, phone, due_at)
    return {"id": task.id, "title": task.title, "task_type": task.task_type}


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: str,
    body: TaskUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Update a task's fields."""
    from datetime import datetime, timezone
    store = MemoryStore(db)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "due_at" in updates and updates["due_at"]:
        from dateutil.parser import parse as parse_date
        import zoneinfo
        parsed = parse_date(updates["due_at"])
        # If naive (no timezone from datetime-local input), treat as user's local time
        if parsed.tzinfo is None:
            user_tz_name = getattr(user, "timezone", None) or "America/New_York"
            try:
                user_tz = zoneinfo.ZoneInfo(user_tz_name)
                parsed = parsed.replace(tzinfo=user_tz)
            except (KeyError, Exception):
                parsed = parsed.replace(tzinfo=timezone.utc)
        # Guard: reject dates in the past
        if parsed < datetime.now(timezone.utc):
            raise HTTPException(400, "Due date cannot be in the past")
        updates["due_at"] = parsed
    task = await store.update_task(user.id, task_id, **updates)
    if not task:
        raise HTTPException(404, "Task not found")
    # Schedule/reschedule reminder delivery when due_at is updated
    if task.due_at and "due_at" in updates:
        from app.tasks.reminder import schedule_task_reminder
        phone = getattr(user, "phone", "") or ""
        await schedule_task_reminder(user.id, task.id, task.title, phone, task.due_at)
    return {
        "id": task.id,
        "title": task.title,
        "completed": task.completed,
        "due_at": task.due_at.isoformat() if task.due_at else None,
    }


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Mark a task as complete."""
    store = MemoryStore(db)
    completed = await store.complete_task(task_id, user.id)
    if not completed:
        raise HTTPException(404, "Task not found")
    return {"id": task_id, "completed": True}


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Delete a task."""
    store = MemoryStore(db)
    deleted = await store.delete_task(user.id, task_id)
    if not deleted:
        raise HTTPException(404, "Task not found")


# ─── Goals ───────────────────────────────────────────────────────────────────

@router.get("/goals")
async def list_goals(
    status: str = Query("active"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return goals for the authenticated user, filtered by status."""
    store = MemoryStore(db)
    goals = await store.get_goals(user.id, status=status)
    return {
        "goals": [
            {
                "id": g.id,
                "title": g.title,
                "framework": g.framework,
                "description": g.description,
                "target_date": g.target_date.isoformat() if g.target_date else None,
                "status": g.status,
                "persona_id": g.persona_id,
                "parent_goal_id": g.parent_goal_id,
                "version": g.version,
                "created_at": g.created_at.isoformat(),
                "updated_at": g.updated_at.isoformat(),
            }
            for g in goals
        ]
    }


@router.post("/goals", status_code=201)
async def create_goal(
    body: GoalCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Create a new goal for the authenticated user."""
    store = MemoryStore(db)
    from dateutil.parser import parse as parse_date
    target_date = parse_date(body.target_date) if body.target_date else None
    goal = await store.create_goal(
        user_id=user.id,
        title=body.title,
        framework=body.framework,
        description=body.description,
        target_date=target_date,
        persona_id=body.persona_id,
        parent_goal_id=body.parent_goal_id,
        metadata=body.metadata,
    )
    await _try_record_milestone(user.id, "set_goal")
    return {"id": goal.id, "title": goal.title, "status": goal.status}


@router.patch("/goals/{goal_id}")
async def update_goal(
    goal_id: str,
    body: GoalUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Update a goal's fields. Increments version on each update."""
    store = MemoryStore(db)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "target_date" in updates and updates["target_date"]:
        from dateutil.parser import parse as parse_date
        updates["target_date"] = parse_date(updates["target_date"])
    goal = await store.update_goal(user.id, goal_id, **updates)
    if not goal:
        raise HTTPException(404, "Goal not found")
    return {"id": goal.id, "title": goal.title, "status": goal.status, "version": goal.version}


@router.delete("/goals/{goal_id}", status_code=204)
async def delete_goal(
    goal_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Delete a goal."""
    store = MemoryStore(db)
    deleted = await store.delete_goal(user.id, goal_id)
    if not deleted:
        raise HTTPException(404, "Goal not found")


# ─── Action Log ──────────────────────────────────────────────────────────────

@router.get("/actions")
async def list_actions(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return paginated action log timeline for the authenticated user."""
    store = MemoryStore(db)
    offset = (page - 1) * limit
    actions = await store.get_action_log(user.id, limit=limit, offset=offset)
    return {
        "actions": [
            {
                "id": a.id,
                "action_type": a.action_type,
                "description": a.description,
                "outcome": a.outcome,
                "trigger": a.trigger,
                "created_at": a.created_at.isoformat(),
            }
            for a in actions
        ],
        "page": page,
        "limit": limit,
    }


# ─── Personas ────────────────────────────────────────────────────────────────

@router.get("/personas")
async def list_personas(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return all active personas for the authenticated user."""
    store = MemoryStore(db)
    personas = await store.get_personas(user.id)
    return {
        "personas": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "tone_notes": p.tone_notes,
                "is_active": p.is_active,
                "created_at": p.created_at.isoformat(),
            }
            for p in personas
        ]
    }


@router.post("/personas", status_code=201)
async def create_persona(
    body: PersonaCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Create a new persona."""
    store = MemoryStore(db)
    persona = await store.create_persona(
        user_id=user.id, name=body.name,
        description=body.description, tone_notes=body.tone_notes,
    )
    await _try_record_milestone(user.id, "created_persona")
    return {"id": persona.id, "name": persona.name}


@router.patch("/personas/{persona_id}")
async def update_persona(
    persona_id: str,
    body: PersonaUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Update a persona's fields."""
    store = MemoryStore(db)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    persona = await store.update_persona(user.id, persona_id, **updates)
    if not persona:
        raise HTTPException(404, "Persona not found")
    return {"id": persona.id, "name": persona.name}


@router.delete("/personas/{persona_id}", status_code=204)
async def delete_persona(
    persona_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Delete a persona."""
    store = MemoryStore(db)
    deleted = await store.delete_persona(user.id, persona_id)
    if not deleted:
        raise HTTPException(404, "Persona not found")


# ─── User Profile (TELOS) ───────────────────────────────────────────────────

@router.get("/profile")
async def get_profile(
    section: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return TELOS profile entries, optionally filtered by section."""
    store = MemoryStore(db)
    entries = await store.get_profile_entries(user.id, section=section)
    return {
        "entries": [
            {
                "id": e.id,
                "section": e.section,
                "label": e.label,
                "content": e.content,
                "persona_id": e.persona_id,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ]
    }


@router.post("/profile", status_code=201)
async def upsert_profile(
    body: ProfileEntryCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Create or update a TELOS profile entry."""
    store = MemoryStore(db)
    entry = await store.upsert_profile_entry(
        user_id=user.id, section=body.section,
        label=body.label, content=body.content,
        persona_id=body.persona_id,
    )
    return {"id": entry.id, "section": entry.section, "label": entry.label}


@router.delete("/profile/{entry_id}", status_code=204)
async def delete_profile_entry(
    entry_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Delete a TELOS profile entry."""
    store = MemoryStore(db)
    deleted = await store.delete_profile_entry(user.id, entry_id)
    if not deleted:
        raise HTTPException(404, "Profile entry not found")


# ─── Proactive Preferences ──────────────────────────────────────────────────

CATEGORY_DESCRIPTIONS = {
    "morning_briefing": "Daily morning briefing with your schedule and priorities",
    "evening_recap": "End-of-day summary of accomplishments and tomorrow's outlook",
    "weekly_digest": "Weekly summary of goals, tasks, and highlights (Sundays)",
    "goal_coaching": "Check-ins on goal progress with suggestions",
    "day_checkin": "Midday check-in to see how your day is going",
    "profile_nudge": "Gentle prompts to complete your profile",
    "insight_observation": "Observations and patterns from your activity",
    "afternoon_followup": "Afternoon follow-up on earlier conversations",
    "feature_discovery": "Tips about features you haven't explored yet",
}

from app.core.throttle import DEFAULT_MAX_PER_DAY

DEFAULT_GLOBAL_SETTINGS = {
    "max_daily_messages": DEFAULT_MAX_PER_DAY,
    "quiet_hours_start": 22,
    "quiet_hours_end": 7,
    "enabled": True,
    "preferred_channel": "sms",
}


class CategoryPrefIn(BaseModel):
    name: str
    enabled: bool = True
    window_start_hour: Optional[float] = None
    window_end_hour: Optional[float] = None


class GlobalSettingsIn(BaseModel):
    max_daily_messages: int = DEFAULT_MAX_PER_DAY
    quiet_hours_start: int = 22
    quiet_hours_end: int = 7
    enabled: bool = True
    preferred_channel: str = "sms"


class ProactivePreferencesIn(BaseModel):
    categories: list[CategoryPrefIn] = []
    global_settings: GlobalSettingsIn = GlobalSettingsIn()


@router.get("/proactive-preferences")
async def get_proactive_preferences(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return all proactive categories merged with user overrides + global settings."""
    import json as _json
    from app.core.proactive_pool import DEFAULT_CATEGORIES

    # Fetch user overrides
    result = await db.execute(
        select(ProactivePreference).where(ProactivePreference.user_id == user.id)
    )
    overrides = {p.category_name: p for p in result.scalars().all()}

    # Build merged category list
    categories = []
    for cat in DEFAULT_CATEGORIES:
        override = overrides.get(cat.name)
        has_override = override is not None
        categories.append({
            "name": cat.name,
            "description": CATEGORY_DESCRIPTIONS.get(cat.name, ""),
            "default_window_start": cat.window_start_hour,
            "default_window_end": cat.window_end_hour,
            "enabled": override.enabled if has_override else cat.default_enabled,
            "window_start_hour": override.window_start_hour if has_override and override.window_start_hour is not None else cat.window_start_hour,
            "window_end_hour": override.window_end_hour if has_override and override.window_end_hour is not None else cat.window_end_hour,
            "has_override": has_override,
        })

    # Parse global settings from User.proactive_settings_json
    global_settings = dict(DEFAULT_GLOBAL_SETTINGS)
    if user.proactive_settings_json:
        try:
            global_settings.update(_json.loads(user.proactive_settings_json))
        except (_json.JSONDecodeError, TypeError):
            pass

    return {
        "categories": categories,
        "global_settings": global_settings,
        "has_slack_linked": bool(user.slack_user_id),
    }


@router.put("/proactive-preferences")
async def update_proactive_preferences(
    body: ProactivePreferencesIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Upsert per-category preferences and update global settings."""
    import json as _json
    from datetime import datetime, timezone as tz
    from app.core.proactive_pool import DEFAULT_CATEGORIES

    # Build a lookup of default windows
    defaults = {c.name: c for c in DEFAULT_CATEGORIES}

    for cat_in in body.categories:
        default_cat = defaults.get(cat_in.name)
        if not default_cat:
            continue  # skip unknown categories

        # Determine if window values match defaults (store null if so)
        ws = cat_in.window_start_hour
        we = cat_in.window_end_hour
        if ws is not None and ws == default_cat.window_start_hour:
            ws = None
        if we is not None and we == default_cat.window_end_hour:
            we = None

        # Upsert: check for existing row
        result = await db.execute(
            select(ProactivePreference).where(
                ProactivePreference.user_id == user.id,
                ProactivePreference.category_name == cat_in.name,
            )
        )
        existing = result.scalars().first()
        if existing:
            existing.enabled = cat_in.enabled
            existing.window_start_hour = ws
            existing.window_end_hour = we
            existing.updated_at = datetime.now(tz.utc)
        else:
            import uuid
            pref = ProactivePreference(
                id=str(uuid.uuid4()),
                user_id=user.id,
                category_name=cat_in.name,
                enabled=cat_in.enabled,
                window_start_hour=ws,
                window_end_hour=we,
            )
            db.add(pref)

    # Update global settings on User
    await db.execute(
        update(User).where(User.id == user.id).values(
            proactive_settings_json=_json.dumps(body.global_settings.model_dump())
        )
    )
    await db.commit()

    # Record milestones for quiet hours and category configuration
    gs = body.global_settings
    if gs.quiet_hours_start != 22 or gs.quiet_hours_end != 7:
        await _try_record_milestone(user.id, "configured_quiet_hours")
    if body.categories:
        await _try_record_milestone(user.id, "configured_proactive")

    return {"status": "saved"}
