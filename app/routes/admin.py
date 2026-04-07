"""Admin API routes — user management, analytics, and system health.

All endpoints require admin role via `require_admin` dependency.
Plans 03 and 04 (frontend dashboard) consume these endpoints.

Endpoints:
  User Management:
    GET    /api/v1/admin/users                    - list users with search/filter/pagination
    GET    /api/v1/admin/users/{user_id}          - user detail with counts
    GET    /api/v1/admin/users/{user_id}/activity  - user message activity
    GET    /api/v1/admin/users/{user_id}/connections - proxy to connections service
    POST   /api/v1/admin/users/{user_id}/suspend   - suspend a user
    POST   /api/v1/admin/users/{user_id}/restore   - restore a user
    DELETE /api/v1/admin/users/{user_id}           - soft delete
    DELETE /api/v1/admin/users/{user_id}/purge     - hard purge (must be soft-deleted first)

  Analytics:
    GET /api/v1/admin/analytics/overview    - summary stats
    GET /api/v1/admin/analytics/signups     - signups time series
    GET /api/v1/admin/analytics/active-users - active users time series
    GET /api/v1/admin/analytics/messages     - messages time series
    GET /api/v1/admin/analytics/intents      - top intents
    GET /api/v1/admin/analytics/channels     - channel breakdown

  System Health:
    GET /api/v1/admin/health - Redis, DB, worker status
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import cast, Date, delete, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.memory.models import (
    ActionLog,
    Goal,
    Memory,
    Message,
    Persona,
    Task,
    User,
    UserProfile,
)
from app.middleware.auth import require_admin
from app.models.auth import UserSession

router = APIRouter(prefix="/api/v1/admin")
logger = logging.getLogger(__name__)


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _connections_client():
    """Yield a short-lived httpx client pointed at the connections service."""
    s = get_settings()
    async with httpx.AsyncClient(base_url=s.connections_service_url, timeout=10.0) as client:
        yield client


def _user_status(user: User) -> str:
    """Derive a human-readable status string from user columns."""
    if user.deleted_at:
        return "deleted"
    if user.suspended_at:
        return "suspended"
    return "active"


# ─── User Management ────────────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=200),
    status: str = Query("all"),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """List all users with search, status filter, and pagination."""
    stmt = select(User).options(selectinload(User.role))

    # Search filter
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            or_(
                User.email.ilike(pattern),
                User.phone.ilike(pattern),
                User.name.ilike(pattern),
            )
        )

    # Status filter
    if status == "active":
        stmt = stmt.where(User.deleted_at.is_(None), User.suspended_at.is_(None))
    elif status == "suspended":
        stmt = stmt.where(User.suspended_at.isnot(None))
    elif status == "deleted":
        stmt = stmt.where(User.deleted_at.isnot(None))
    # "all" — no filter

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate
    stmt = stmt.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit)
    result = await db.execute(stmt)
    users = result.scalars().all()

    return {
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "phone": u.phone,
                "name": u.name,
                "role": u.role.name if u.role else "user",
                "status": _user_status(u),
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Return full profile for a user, including activity counts."""
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    # Counts
    message_count = (await db.execute(
        select(func.count()).select_from(Message).where(Message.user_id == user_id)
    )).scalar_one()
    task_count = (await db.execute(
        select(func.count()).select_from(Task).where(Task.user_id == user_id)
    )).scalar_one()
    goal_count = (await db.execute(
        select(func.count()).select_from(Goal).where(Goal.user_id == user_id)
    )).scalar_one()
    memory_count = (await db.execute(
        select(func.count()).select_from(Memory).where(Memory.user_id == user_id)
    )).scalar_one()

    return {
        "id": user.id,
        "email": user.email,
        "phone": user.phone,
        "name": user.name,
        "timezone": user.timezone,
        "role": user.role.name if user.role else "user",
        "status": _user_status(user),
        "assistant_name": user.assistant_name,
        "personality_notes": user.personality_notes,
        "phone_verified": user.phone_verified,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
        "deleted_at": user.deleted_at.isoformat() if user.deleted_at else None,
        "suspended_at": user.suspended_at.isoformat() if user.suspended_at else None,
        "message_count": message_count,
        "task_count": task_count,
        "goal_count": goal_count,
        "memory_count": memory_count,
    }


@router.get("/users/{user_id}/activity")
async def get_user_activity(
    user_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Return paginated message activity for a user."""
    # Verify user exists
    user_exists = (await db.execute(
        select(func.count()).select_from(User).where(User.id == user_id)
    )).scalar_one()
    if not user_exists:
        raise HTTPException(404, "User not found")

    offset = (page - 1) * limit
    result = await db.execute(
        select(Message)
        .where(Message.user_id == user_id)
        .order_by(Message.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    messages = result.scalars().all()

    total = (await db.execute(
        select(func.count()).select_from(Message).where(Message.user_id == user_id)
    )).scalar_one()

    return {
        "activity": [
            {
                "id": m.id,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "direction": m.direction,
                "body": m.body[:200] if m.body else "",
                "channel": m.channel,
                "intent": m.intent,
            }
            for m in messages
        ],
        "total": total,
    }


@router.get("/users/{user_id}/connections")
async def get_user_connections(
    user_id: str,
    admin: User = Depends(require_admin),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Proxy to connections service to get a user's connections."""
    try:
        resp = await client.get(f"/connections/{user_id}")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("connections service unavailable user_id=%s error=%s", user_id, e)
        return {"connections": [], "error": "Connections service unavailable"}


@router.post("/users/{user_id}/suspend")
async def suspend_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Suspend a user account."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if user.suspended_at:
        raise HTTPException(409, "User already suspended")

    user.suspended_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("USER_SUSPENDED user_id=%s by_admin=%s", user_id, admin.id)
    return {"status": "suspended", "suspended_at": user.suspended_at.isoformat()}


@router.post("/users/{user_id}/restore")
async def restore_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Restore a suspended or soft-deleted user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    user.suspended_at = None
    user.deleted_at = None
    await db.commit()
    logger.info("USER_RESTORED user_id=%s by_admin=%s", user_id, admin.id)
    return {"status": "active"}


@router.delete("/users/{user_id}")
async def soft_delete_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Soft delete a user (set deleted_at timestamp)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if user.deleted_at:
        raise HTTPException(409, "User already deleted")

    user.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("USER_SOFT_DELETED user_id=%s by_admin=%s", user_id, admin.id)
    return {"status": "deleted", "deleted_at": user.deleted_at.isoformat()}


@router.delete("/users/{user_id}/purge")
async def hard_purge_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Hard purge a user and all related data. User must be soft-deleted first."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if user.deleted_at is None:
        raise HTTPException(409, "User must be soft-deleted before purge")

    # Delete all related data explicitly (in case cascades aren't set for all tables)
    await db.execute(delete(Memory).where(Memory.user_id == user_id))
    await db.execute(delete(Message).where(Message.user_id == user_id))
    await db.execute(delete(Task).where(Task.user_id == user_id))
    await db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    await db.execute(delete(Goal).where(Goal.user_id == user_id))
    await db.execute(delete(ActionLog).where(ActionLog.user_id == user_id))
    await db.execute(delete(UserProfile).where(UserProfile.user_id == user_id))
    await db.execute(delete(Persona).where(Persona.user_id == user_id))

    # Delete the user row
    await db.delete(user)
    await db.commit()
    logger.info("USER_HARD_PURGED user_id=%s by_admin=%s", user_id, admin.id)
    return {"status": "purged"}


# ─── Analytics Cache ────────────────────────────────────────────────────────

_analytics_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 60  # seconds


async def _cached(key: str, range_key: str, db: AsyncSession, query_fn):
    """Return cached result or call query_fn and cache the result."""
    cache_key = f"{key}:{range_key}"
    now = time.time()
    if cache_key in _analytics_cache:
        cached_at, data = _analytics_cache[cache_key]
        if now - cached_at < _CACHE_TTL:
            return data
    data = await query_fn(db)
    _analytics_cache[cache_key] = (now, data)
    return data


def _parse_range(range_param: str) -> Optional[datetime]:
    """Convert range string to a UTC cutoff datetime, or None for 'all'."""
    days_map = {"7d": 7, "30d": 30, "90d": 90, "all": None}
    days = days_map.get(range_param, 30)
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def _apply_cutoff(stmt, column, cutoff: Optional[datetime]):
    """Apply a time cutoff filter to a SQLAlchemy statement."""
    if cutoff is not None:
        stmt = stmt.where(column >= cutoff)
    return stmt


# ─── Analytics Endpoints ────────────────────────────────────────────────────


@router.get("/analytics/overview")
async def analytics_overview(
    range: str = Query("30d"),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Overview stats: total users, active today, messages today, pending tasks."""
    async def _query(db: AsyncSession):
        today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        total_users = (await db.execute(
            select(func.count()).select_from(User).where(User.deleted_at.is_(None))
        )).scalar_one()

        active_today = (await db.execute(
            select(func.count()).select_from(User).where(
                User.last_seen_at >= today_midnight,
                User.deleted_at.is_(None),
            )
        )).scalar_one()

        messages_today = (await db.execute(
            select(func.count()).select_from(Message).where(
                Message.created_at >= today_midnight,
            )
        )).scalar_one()

        pending_tasks = (await db.execute(
            select(func.count()).select_from(Task).where(
                Task.completed == False,  # noqa: E712
            )
        )).scalar_one()

        return {
            "total_users": total_users,
            "active_today": active_today,
            "messages_today": messages_today,
            "pending_tasks": pending_tasks,
        }

    return await _cached("overview", range, db, _query)


@router.get("/analytics/signups")
async def analytics_signups(
    range: str = Query("30d"),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Signups per day time series."""
    cutoff = _parse_range(range)

    async def _query(db: AsyncSession):
        stmt = (
            select(
                cast(User.created_at, Date).label("date"),
                func.count().label("count"),
            )
            .where(User.deleted_at.is_(None))
        )
        stmt = _apply_cutoff(stmt, User.created_at, cutoff)
        stmt = stmt.group_by(cast(User.created_at, Date)).order_by("date")
        result = await db.execute(stmt)
        return {"data": [{"date": str(row.date), "count": row.count} for row in result]}

    return await _cached("signups", range, db, _query)


@router.get("/analytics/active-users")
async def analytics_active_users(
    range: str = Query("30d"),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Active users per day (DAU). WAU/MAU computed client-side from daily data."""
    cutoff = _parse_range(range)

    async def _query(db: AsyncSession):
        stmt = (
            select(
                cast(User.last_seen_at, Date).label("date"),
                func.count(distinct(User.id)).label("dau"),
            )
            .where(User.deleted_at.is_(None))
        )
        stmt = _apply_cutoff(stmt, User.last_seen_at, cutoff)
        stmt = stmt.group_by(cast(User.last_seen_at, Date)).order_by("date")
        result = await db.execute(stmt)
        return {"data": [{"date": str(row.date), "dau": row.dau} for row in result]}

    return await _cached("active_users", range, db, _query)


@router.get("/analytics/messages")
async def analytics_messages(
    range: str = Query("30d"),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Messages per day time series."""
    cutoff = _parse_range(range)

    async def _query(db: AsyncSession):
        stmt = select(
            cast(Message.created_at, Date).label("date"),
            func.count().label("count"),
        )
        stmt = _apply_cutoff(stmt, Message.created_at, cutoff)
        stmt = stmt.group_by(cast(Message.created_at, Date)).order_by("date")
        result = await db.execute(stmt)
        return {"data": [{"date": str(row.date), "count": row.count} for row in result]}

    return await _cached("messages", range, db, _query)


@router.get("/analytics/intents")
async def analytics_intents(
    range: str = Query("30d"),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Top 10 intents by message count."""
    cutoff = _parse_range(range)

    async def _query(db: AsyncSession):
        stmt = (
            select(
                Message.intent,
                func.count().label("count"),
            )
            .where(Message.intent.isnot(None))
        )
        stmt = _apply_cutoff(stmt, Message.created_at, cutoff)
        stmt = stmt.group_by(Message.intent).order_by(func.count().desc()).limit(10)
        result = await db.execute(stmt)
        return {"data": [{"intent": row.intent, "count": row.count} for row in result]}

    return await _cached("intents", range, db, _query)


@router.get("/analytics/channels")
async def analytics_channels(
    range: str = Query("30d"),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """Channel breakdown: distinct users per channel."""
    cutoff = _parse_range(range)

    async def _query(db: AsyncSession):
        stmt = select(
            Message.channel,
            func.count(distinct(Message.user_id)).label("user_count"),
        )
        stmt = _apply_cutoff(stmt, Message.created_at, cutoff)
        stmt = stmt.group_by(Message.channel)
        result = await db.execute(stmt)
        return {"data": [{"channel": row.channel or "unknown", "user_count": row.user_count} for row in result]}

    return await _cached("channels", range, db, _query)


# ─── System Health ──────────────────────────────────────────────────────────


@router.get("/health")
async def admin_health(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(_get_db),
):
    """System health: Redis status + queue depth, DB status, worker pending jobs."""
    import redis.asyncio as aioredis

    settings = get_settings()

    # Redis check
    redis_status = {"status": "error", "queue_depth": 0}
    try:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.ping()
        # Queue depth — use llen since the queue is a Redis list (LPUSH/BRPOP pattern)
        queue_depth = await r.llen(settings.queue_name)
        redis_status = {"status": "ok", "queue_depth": queue_depth}
        await r.aclose()
    except Exception as e:
        logger.warning("admin_health redis_check=error err=%s", e)

    # DB check
    db_status = {"status": "error"}
    try:
        await db.execute(select(func.count()).select_from(User))
        db_status = {"status": "ok"}
    except Exception as e:
        logger.warning("admin_health db_check=error err=%s", e)

    # Worker pending jobs
    worker_status = {"pending_jobs": redis_status.get("queue_depth", 0)}

    return {
        "redis": redis_status,
        "database": db_status,
        "worker": worker_status,
    }
