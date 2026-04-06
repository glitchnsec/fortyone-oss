"""
Per-user proactive message rate limiting with Redis.

Safety rails per D-11 and Known Pitfall 4:
  - check_rate_limit(): per-user hourly + daily caps
  - check_idempotency(): prevent duplicate jobs (SET NX)
  - record_proactive_send(): increment counters after send
  - check_dead_man_switch(): if hourly limit exceeded, disable proactive for user
  - is_quiet_hours(): check if current time is within user's quiet period

All state is in Redis — survives process restarts and works across scheduler + worker.
"""
import json
import logging
import time
from datetime import datetime

import redis.asyncio as aioredis
import zoneinfo

logger = logging.getLogger(__name__)

# Defaults — can be overridden per-user via proactive_settings_json
DEFAULT_MAX_PER_HOUR = 10
DEFAULT_MAX_PER_DAY = 5
DEAD_MAN_SWITCH_THRESHOLD = 5  # If >5 in 1 hour, something is wrong


async def check_rate_limit(
    r: aioredis.Redis,
    user_id: str,
    max_per_day: int = DEFAULT_MAX_PER_DAY,
    max_per_hour: int = DEFAULT_MAX_PER_HOUR,
) -> bool:
    """Return True if the user can receive a proactive message."""
    now = int(time.time())
    hour_key = f"rate:proactive:{user_id}:hour:{now // 3600}"
    day_key = f"rate:proactive:{user_id}:day:{now // 86400}"

    pipe = r.pipeline()
    pipe.get(hour_key)
    pipe.get(day_key)
    hour_count, day_count = await pipe.execute()

    hour_int = int(hour_count) if hour_count else 0
    day_int = int(day_count) if day_count else 0

    if hour_int >= max_per_hour:
        logger.info("RATE_LIMITED user=%s reason=hourly count=%d max=%d", user_id[:8], hour_int, max_per_hour)
        return False
    if day_int >= max_per_day:
        logger.info("RATE_LIMITED user=%s reason=daily count=%d max=%d", user_id[:8], day_int, max_per_day)
        return False
    return True


async def record_proactive_send(r: aioredis.Redis, user_id: str) -> None:
    """Increment rate counters after sending a proactive message."""
    now = int(time.time())
    hour_key = f"rate:proactive:{user_id}:hour:{now // 3600}"
    day_key = f"rate:proactive:{user_id}:day:{now // 86400}"

    pipe = r.pipeline()
    pipe.incr(hour_key)
    pipe.expire(hour_key, 3600)
    pipe.incr(day_key)
    pipe.expire(day_key, 86400)
    await pipe.execute()


async def check_idempotency(r: aioredis.Redis, key: str, ttl: int = 86400) -> bool:
    """Return True if this is the first time this key is seen (not a duplicate)."""
    result = await r.set(f"idem:{key}", "1", nx=True, ex=ttl)
    return result is not None


async def check_dead_man_switch(r: aioredis.Redis, user_id: str) -> bool:
    """
    Return True if proactive messaging is safe (no runaway loop detected).
    Return False if hourly sends exceeded threshold — indicates a loop.
    """
    now = int(time.time())
    hour_key = f"rate:proactive:{user_id}:hour:{now // 3600}"
    count = await r.get(hour_key)
    if count and int(count) >= DEAD_MAN_SWITCH_THRESHOLD:
        logger.error(
            "DEAD_MAN_SWITCH  user=%s  hourly_count=%s — disabling proactive messaging",
            user_id[:8], count,
        )
        # Set a flag that disables proactive for this user (expires in 1 hour)
        await r.set(f"proactive:disabled:{user_id}", "1", ex=3600)
        return False

    # Check if user was previously disabled
    disabled = await r.get(f"proactive:disabled:{user_id}")
    if disabled:
        logger.info("PROACTIVE_DISABLED  user=%s — dead man switch active", user_id[:8])
        return False

    return True


def is_quiet_hours(
    user_timezone: str,
    settings_json: str | None,
    _override_hour: int | None = None,
) -> bool:
    """
    Return True if current local time is within user's quiet hours.

    Default quiet hours: 10 PM - 7 AM (22:00 - 07:00).
    Handles midnight wraparound (e.g., 22-7 means hour >= 22 OR hour < 7).

    Args:
        user_timezone: IANA timezone string (e.g. "America/New_York")
        settings_json: JSON string from user.proactive_settings_json (nullable)
        _override_hour: For testing — override the current hour
    """
    # Parse quiet hours from settings or use defaults
    start_hour = 22
    end_hour = 7
    if settings_json:
        try:
            settings = json.loads(settings_json)
            quiet = settings.get("quiet_hours", {})
            start_hour = quiet.get("start", 22)
            end_hour = quiet.get("end", 7)
        except (json.JSONDecodeError, TypeError):
            pass  # use defaults

    # Determine current hour in user's timezone
    if _override_hour is not None:
        hour = _override_hour
    else:
        tz = zoneinfo.ZoneInfo(user_timezone)
        now_local = datetime.now(tz)
        hour = now_local.hour

    # Handle midnight wraparound
    if start_hour > end_hour:
        # e.g. 22-7: quiet if hour >= 22 OR hour < 7
        return hour >= start_hour or hour < end_hour
    else:
        # e.g. 0-7: quiet if 0 <= hour < 7
        return start_hour <= hour < end_hour
