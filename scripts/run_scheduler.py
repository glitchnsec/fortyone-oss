#!/usr/bin/env python3
"""
Scheduler process — runs as a separate Docker service.

Two-phase loop (per D-04, D-08):
  Phase 1: Daily planning — for each active user, call plan_day() to select
           weighted random categories and ZADD jitter-timed jobs to Redis.
           Idempotent via SET NX (safe to call every poll cycle).
  Phase 2: Dispatch — poll Redis sorted set for due jobs, check rate limits,
           quiet hours, and idempotency, then push to Redis Stream.

Task reminders (scheduled by the reminder system) are NOT part of the pool
and continue to work independently.

Frequency: every 30 seconds.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# Make sure the project root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis.asyncio as aioredis

from app.config import get_settings
from app.core.proactive_pool import plan_day
from app.core.throttle import is_quiet_hours

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

POLL_INTERVAL = 30  # seconds
BATCH_SIZE = 50
USER_CACHE_TTL = 300  # 5 minutes — avoid DB hit every poll
STALE_JOB_THRESHOLD = 6 * 3600  # 6 hours — discard pool jobs older than this

# Job types managed by the pool (NOT task_reminder)
POOL_JOB_TYPES = {
    "morning_briefing", "evening_recap", "weekly_digest",
    "goal_coaching", "smart_checkin", "profile_nudge", "insight_observation",
    "feature_discovery",
}


async def scheduler_loop():
    settings = get_settings()
    r = await aioredis.from_url(
        settings.redis_url, encoding="utf-8", decode_responses=True,
    )
    logger.info("Scheduler started — polling every %ds  redis=%s", POLL_INTERVAL, settings.redis_url)

    poll_count = 0
    # User cache: (users_list, cached_at_epoch)
    _user_cache: tuple[list, float] = ([], 0.0)

    while True:
        try:
            now = time.time()

            # ── Phase 1: Daily planning ──────────────────────────────────
            # Refresh user cache every 5 minutes
            if now - _user_cache[1] > USER_CACHE_TTL:
                try:
                    from app.database import AsyncSessionLocal
                    from app.memory.store import MemoryStore
                    async with AsyncSessionLocal() as db:
                        store = MemoryStore(db)
                        _user_cache = (await store.get_proactive_users(), now)
                    logger.info("USER_CACHE_REFRESH users=%d", len(_user_cache[0]))
                except Exception as exc:
                    logger.warning("USER_CACHE_REFRESH failed: %s", exc)

            # Plan day for each active user (idempotent via SET NX)
            for user_id, user_tz, settings_json in _user_cache[0]:
                try:
                    from app.database import AsyncSessionLocal
                    from app.memory.store import MemoryStore
                    async with AsyncSessionLocal() as db:
                        store = MemoryStore(db)
                        scheduled = await plan_day(r, user_id, user_tz, store)
                    if scheduled:
                        logger.info(
                            "POOL_PLAN user=%s categories=%s",
                            user_id[:8], scheduled,
                        )
                except Exception as exc:
                    logger.error("POOL_PLAN_ERROR user=%s err=%s", user_id[:8], exc)

            # ── Phase 2: Dispatch ────────────────────────────────────────
            ready_with_scores = await r.zrangebyscore(
                "scheduled_jobs", "-inf", now, start=0, num=BATCH_SIZE,
                withscores=True,
            )
            poll_count += 1
            # Heartbeat every 10 polls (~5 min) so logs show the scheduler is alive
            if poll_count % 10 == 0:
                pending_total = await r.zcard("scheduled_jobs")
                logger.info("SCHEDULER_HEARTBEAT  polls=%d  pending_jobs=%d", poll_count, pending_total)
            if ready_with_scores:
                logger.info("SCHEDULER_POLL  found=%d due jobs", len(ready_with_scores))
            for job_data, score in ready_with_scores:
                removed = await r.zrem("scheduled_jobs", job_data)
                if removed:  # Atomic claim — prevents duplicate processing
                    payload = json.loads(job_data)
                    payload["source"] = "scheduler"

                    # Discard stale pool jobs — prevents morning dispatch of
                    # evening jobs that accumulated during scheduler downtime.
                    job_type = payload.get("type", "unknown")
                    if job_type in POOL_JOB_TYPES and (now - score) > STALE_JOB_THRESHOLD:
                        logger.warning(
                            "SCHEDULER_DISCARD_STALE type=%s user=%s age_hours=%.1f",
                            job_type, payload.get("user_id", "")[:8],
                            (now - score) / 3600,
                        )
                        continue

                    # Rate limit check before dispatching
                    from app.core.throttle import check_rate_limit, check_dead_man_switch
                    user_id = payload.get("user_id", "")
                    if user_id:
                        safe = await check_dead_man_switch(r, user_id)
                        if not safe:
                            logger.warning("SCHEDULER_SKIP dead_man_switch user=%s", user_id[:8])
                            continue
                        allowed = await check_rate_limit(r, user_id)
                        if not allowed:
                            logger.info("SCHEDULER_SKIP rate_limited user=%s", user_id[:8])
                            continue

                    # Quiet hours check — defer pool jobs, not task reminders
                    job_type = payload.get("type", "unknown")
                    if job_type in POOL_JOB_TYPES and user_id:
                        # Look up user timezone and settings from cache
                        user_tz = "America/New_York"
                        user_settings_json = None
                        for uid, tz, sj in _user_cache[0]:
                            if uid == user_id:
                                user_tz = tz
                                user_settings_json = sj
                                break
                        if is_quiet_hours(user_tz, user_settings_json):
                            # Re-ZADD 1 hour into the future
                            defer_time = now + 3600
                            await r.zadd("scheduled_jobs", {job_data: defer_time})
                            logger.info(
                                "QUIET_HOURS_DEFER user=%s type=%s deferred_1h",
                                user_id[:8], job_type,
                            )
                            continue

                    # Idempotency check — key varies by job type:
                    # - task_reminder: per task_id (user can have multiple reminders per day)
                    # - pool jobs: per user per day (one per type per day)
                    from app.core.throttle import check_idempotency
                    if job_type == "task_reminder":
                        # Per-task-per-day idempotency — recurring reminders need to
                        # fire again the next day, so include a date component.
                        day_bucket = int(now // 86400)
                        idem_key = f"proactive:{user_id}:task_reminder:{payload.get('task_id', 'unknown')}:{day_bucket}"
                    else:
                        # Per-user-per-day for recurring proactive jobs
                        # Use category (pool name) for idempotency, not handler_type,
                        # so categories sharing a handler (e.g. day_checkin and
                        # afternoon_followup both use smart_checkin) don't collide.
                        idem_category = payload.get("category", job_type)
                        idem_key = f"proactive:{user_id}:{idem_category}:{int(now // 86400)}"
                    is_new = await check_idempotency(r, idem_key)
                    if not is_new:
                        logger.info("SCHEDULER_SKIP duplicate idem_key=%s", idem_key)
                        continue

                    await r.xadd(settings.queue_name, {"data": json.dumps(payload)})

                    # Record category cooldown to prevent re-selection
                    # during the next plan_day call.
                    from app.core.proactive_pool import record_category_cooldown
                    idem_category = payload.get("category", job_type)
                    await record_category_cooldown(r, user_id, idem_category)

                    logger.info(
                        "SCHEDULED_DISPATCH  type=%s  user=%s",
                        payload.get("type"), user_id[:8],
                    )

        except Exception as exc:
            logger.error("Scheduler error: %s", exc, exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(scheduler_loop())
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")
