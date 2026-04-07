"""
ProactivePool — weighted random category selection with jitter windows.

Replaces cron-based fixed scheduling with a pool-based system where 2-4
categories are selected per user per day via weighted random, each assigned
a random time within its window. Jitter makes delivery times feel organic.

Key decisions: D-01 (weighted pool), D-04 (merge existing jobs), D-05 (jitter),
D-06 (2-4 per day), D-07 (1hr spacing).
"""
import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

import zoneinfo

logger = logging.getLogger(__name__)


@dataclass
class ProactiveCategory:
    """A category of proactive message that can be selected by the pool."""
    name: str                           # "morning_briefing", "profile_nudge", etc.
    handler_type: str                   # maps to worker dispatch payload["type"]
    window_start_hour: float            # earliest send hour (user local time)
    window_end_hour: float              # latest send hour
    base_weight: float                  # default weight
    weight_fn: Optional[Callable] = None  # (user_state) -> float bonus weight
    days_of_week: Optional[set] = None  # None = all days, {6} = Sunday only
    cooldown_hours: int = 24            # min hours since last send of THIS category
    requires: str = "always"            # "always" | "has_goals" | "incomplete_profile" | "has_memories"


# ─── Weight modifier functions ──────────────────────────────────────────────

def _goal_coaching_weight(state: dict) -> float:
    """Boost weight by +5 if any goal has a target_date within 7 days."""
    return 5.0 if state.get("approaching_goals") else 0.0


def _day_checkin_weight(state: dict) -> float:
    """Boost weight by +2 if calendar is connected."""
    return 2.0 if state.get("has_calendar") else 0.0


def _profile_nudge_weight(state: dict) -> float:
    """Boost weight by +7 if profile completeness < 50%."""
    completeness = state.get("profile_completeness", 1.0)
    return 7.0 if completeness < 0.5 else 0.0


def _insight_weight(state: dict) -> float:
    """Boost weight by +3 if user has > 20 memories."""
    return 3.0 if state.get("memory_count", 0) > 20 else 0.0


# ─── Default categories (D-04: merge existing + new) ────────────────────────

DEFAULT_CATEGORIES = [
    ProactiveCategory(
        name="morning_briefing",
        handler_type="morning_briefing",
        window_start_hour=7.5,    # 7:30 AM
        window_end_hour=9.0,      # 9:00 AM
        base_weight=8,
        requires="always",
    ),
    ProactiveCategory(
        name="evening_recap",
        handler_type="evening_recap",
        window_start_hour=17.5,   # 5:30 PM
        window_end_hour=19.0,     # 7:00 PM
        base_weight=7,
        requires="always",
    ),
    ProactiveCategory(
        name="weekly_digest",
        handler_type="weekly_digest",
        window_start_hour=9.0,
        window_end_hour=11.0,
        base_weight=6,
        days_of_week={6},         # Sunday only (Python weekday: Mon=0, Sun=6)
        requires="always",
    ),
    ProactiveCategory(
        name="goal_coaching",
        handler_type="goal_coaching",
        window_start_hour=11.0,   # Widened: was 10-4, now 11-5 (afternoon focus)
        window_end_hour=17.0,
        base_weight=5,
        weight_fn=_goal_coaching_weight,
        requires="has_goals",
    ),
    ProactiveCategory(
        name="day_checkin",
        handler_type="smart_checkin",
        window_start_hour=13.0,   # Shifted later: was 11-3, now 1-5 PM (afternoon)
        window_end_hour=17.0,
        base_weight=5,            # Bumped weight from 4 to 5 for better selection odds
        weight_fn=_day_checkin_weight,
        requires="always",
    ),
    ProactiveCategory(
        name="profile_nudge",
        handler_type="profile_nudge",
        window_start_hour=12.0,   # Shifted later: was 10-2, now 12-4 PM
        window_end_hour=16.0,
        base_weight=3,
        weight_fn=_profile_nudge_weight,
        requires="incomplete_profile",
    ),
    ProactiveCategory(
        name="insight_observation",
        handler_type="insight_observation",
        window_start_hour=14.0,   # Shifted later: was 12-5, now 2-6 PM
        window_end_hour=18.0,
        base_weight=3,
        weight_fn=_insight_weight,
        requires="has_memories",
    ),
    # New: afternoon follow-up — lightweight check-in for the second half of the day
    ProactiveCategory(
        name="afternoon_followup",
        handler_type="smart_checkin",
        window_start_hour=14.5,   # 2:30 PM
        window_end_hour=16.5,     # 4:30 PM
        base_weight=4,
        requires="always",
    ),
]


# ─── User state computation ─────────────────────────────────────────────────

async def compute_user_state(store, user_id: str) -> dict:
    """
    Build a state dict used by weight functions and category filtering.

    Returns:
        profile_completeness: float 0.0-1.0
        has_goals: bool
        approaching_goals: bool (any goal with target_date within 7 days)
        memory_count: int
        has_calendar: bool
    """
    from app.memory.models import User
    from sqlalchemy import select

    # Get user record
    result = await store.db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()

    # Profile completeness
    profile_entries = await store.get_profile_entries(user_id)
    checks = {
        "name": bool(user and user.name),
        "timezone": bool(user and user.timezone and user.timezone != "America/New_York"),
        "assistant_name": bool(user and getattr(user, "assistant_name", None)),
        "personality_notes": bool(user and getattr(user, "personality_notes", None)),
        "has_preferences": any(e.section == "preferences" for e in profile_entries),
        "has_goals_profile": any(e.section == "goals" for e in profile_entries),
        "has_challenges": any(e.section == "challenges" for e in profile_entries),
    }
    profile_completeness = sum(checks.values()) / len(checks) if checks else 0.0

    # Goals
    goals = await store.get_goals(user_id, status="active")
    has_goals = len(goals) > 0
    now = datetime.now(zoneinfo.ZoneInfo("UTC"))
    approaching_goals = any(
        g.target_date and (g.target_date - now) <= timedelta(days=7)
        for g in goals
    )

    # Memory count
    memories = await store.get_memories(user_id)
    memory_count = len(memories)

    # Calendar check — skip if connections service is unreachable
    has_calendar = False  # conservative default; future: check connections service

    return {
        "profile_completeness": profile_completeness,
        "has_goals": has_goals,
        "approaching_goals": approaching_goals,
        "memory_count": memory_count,
        "has_calendar": has_calendar,
    }


# ─── Category selection (D-01: weighted random without replacement) ──────────

def _check_requires(category: ProactiveCategory, user_state: dict) -> bool:
    """Return True if user state satisfies the category's requirements."""
    req = category.requires
    if req == "always":
        return True
    if req == "has_goals":
        return user_state.get("has_goals", False)
    if req == "incomplete_profile":
        return user_state.get("profile_completeness", 1.0) < 0.8
    if req == "has_memories":
        return user_state.get("memory_count", 0) > 15
    return True


def select_categories(
    categories: list[ProactiveCategory],
    user_state: dict,
    target_count: int = 3,
) -> list[ProactiveCategory]:
    """
    Select categories via weighted random without replacement.

    Filters by requires field and day_of_week, then uses random.choices
    one at a time (removing chosen) to sample without replacement.
    """
    today_weekday = datetime.now().weekday()  # Mon=0, Sun=6

    # Filter eligible categories
    eligible = []
    weights = []
    for cat in categories:
        # Day-of-week filter
        if cat.days_of_week is not None and today_weekday not in cat.days_of_week:
            continue
        # Requires filter
        if not _check_requires(cat, user_state):
            continue
        # Compute effective weight
        bonus = cat.weight_fn(user_state) if cat.weight_fn else 0.0
        w = cat.base_weight + bonus
        if w > 0:
            eligible.append(cat)
            weights.append(w)

    if not eligible:
        return []

    # Weighted random without replacement
    selected = []
    remaining_cats = list(eligible)
    remaining_ws = list(weights)
    for _ in range(min(target_count, len(remaining_cats))):
        chosen = random.choices(remaining_cats, weights=remaining_ws, k=1)[0]
        selected.append(chosen)
        idx = remaining_cats.index(chosen)
        remaining_cats.pop(idx)
        remaining_ws.pop(idx)

    return selected


# ─── Jitter time computation (D-05) ─────────────────────────────────────────

def compute_jitter_time(
    window_start_hour: float,
    window_end_hour: float,
    user_timezone: str,
    date: Optional[datetime] = None,
) -> float:
    """
    Return a Unix timestamp for a random moment within the category's time window.

    Uses minute-level granularity + random seconds for uniform distribution
    across the window. Clamps start to current time if the window is partially
    elapsed, ensuring the returned timestamp is always in the future.
    """
    tz = zoneinfo.ZoneInfo(user_timezone)
    if date is None:
        date = datetime.now(tz)

    # Clamp window start to current time so we never produce past timestamps
    current_hour = date.hour + date.minute / 60.0
    effective_start = max(window_start_hour, current_hour)

    start_minutes = int(effective_start * 60)
    end_minutes = int(window_end_hour * 60)

    # If the effective window is empty (current time past end), caller should
    # have already skipped this category. Defensive fallback: use end_minutes.
    if start_minutes > end_minutes:
        start_minutes = end_minutes

    chosen_minute = random.randint(start_minutes, end_minutes)

    chosen_dt = date.replace(
        hour=chosen_minute // 60,
        minute=chosen_minute % 60,
        second=random.randint(0, 59),
        microsecond=0,
        tzinfo=tz,
    )
    return chosen_dt.timestamp()


# ─── Spacing enforcement (D-07: 1hr minimum) ────────────────────────────────

async def check_spacing(r, user_id: str, proposed_time: float, min_gap: int = 3600) -> float | None:
    """
    Check that no other proactive job for this user is within min_gap seconds.

    If spacing violated, shift by 30 minutes and retry up to 3 times.
    Returns adjusted timestamp or None if all attempts fail.
    """
    for attempt in range(4):  # original + 3 retries
        t = proposed_time + (attempt * 1800)  # shift by 30 min each retry
        nearby = await r.zrangebyscore(
            "scheduled_jobs",
            t - min_gap,
            t + min_gap,
        )
        conflict = False
        for job_data in nearby:
            try:
                payload = json.loads(job_data)
                if payload.get("user_id") == user_id:
                    conflict = True
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        if not conflict:
            return t

    return None


# ─── Day planning orchestrator ───────────────────────────────────────────────

async def plan_day(r, user_id: str, user_timezone: str, store) -> list[str]:
    """
    Plan one day of proactive messages for a user.

    Idempotent via SET NX guard — safe to call every poll cycle.
    Returns list of scheduled category names (empty if already planned).
    """
    tz = zoneinfo.ZoneInfo(user_timezone)
    today = datetime.now(tz)
    date_str = today.strftime("%Y-%m-%d")

    # SET NX guard — one plan per user per day
    plan_key = f"proactive:plan:{user_id}:{date_str}"
    was_set = await r.set(plan_key, "1", nx=True, ex=86400)
    if not was_set:
        return []  # already planned today

    # Compute user state for weight evaluation
    user_state = await compute_user_state(store, user_id)

    # Select 3-5 categories (D-06, widened from 2-4 for better day coverage)
    target_count = random.randint(3, 5)
    selected = select_categories(DEFAULT_CATEGORIES, user_state, target_count=target_count)

    if not selected:
        logger.info("POOL_PLAN user=%s categories=[] (none eligible)", user_id[:8])
        return []

    # Look up user phone for job payloads
    from app.memory.models import User
    from sqlalchemy import select as sa_select
    result = await store.db.execute(sa_select(User).where(User.id == user_id))
    user = result.scalars().first()
    phone = user.phone if user else ""

    scheduled_names = []
    current_hour = today.hour + today.minute / 60.0
    for cat in selected:
        # Skip categories whose time window has already passed today.
        # This prevents scheduling past-timestamp jobs that would fire immediately.
        if cat.window_end_hour <= current_hour:
            logger.info(
                "POOL_SKIP_PAST_WINDOW user=%s category=%s window_end=%.1f current=%.1f",
                user_id[:8], cat.name, cat.window_end_hour, current_hour,
            )
            continue

        jitter_ts = compute_jitter_time(
            cat.window_start_hour, cat.window_end_hour, user_timezone, today,
        )

        # Enforce minimum spacing (D-07)
        adjusted_ts = await check_spacing(r, user_id, jitter_ts)
        if adjusted_ts is None:
            logger.info(
                "POOL_SKIP_SPACING user=%s category=%s — could not find open slot",
                user_id[:8], cat.name,
            )
            continue

        # ZADD to scheduled_jobs
        payload = {
            "type": cat.handler_type,
            "category": cat.name,  # Used for idempotency (distinct from handler_type)
            "user_id": user_id,
            "channel": "sms",
            "phone": phone,
            "source": "scheduler",
            "job_id": str(uuid.uuid4()),
        }
        await r.zadd("scheduled_jobs", {json.dumps(payload): adjusted_ts})
        scheduled_names.append(cat.name)

    logger.info(
        "POOL_PLAN user=%s categories=%s target=%d",
        user_id[:8], scheduled_names, target_count,
    )
    return scheduled_names


# ─── Public API ──────────────────────────────────────────────────────────────

class ProactivePool:
    """Namespace for pool operations — categories, selection, planning."""

    categories = DEFAULT_CATEGORIES

    @staticmethod
    async def plan_day(r, user_id: str, user_timezone: str, store) -> list[str]:
        return await plan_day(r, user_id, user_timezone, store)

    @staticmethod
    def select_categories(user_state: dict, target_count: int = 3) -> list[ProactiveCategory]:
        return select_categories(DEFAULT_CATEGORIES, user_state, target_count)
