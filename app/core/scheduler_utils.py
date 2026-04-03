"""Scheduler utilities — cron expression handling for proactive jobs."""
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def compute_next_run(cron_expr: str, user_timezone: str = "America/New_York") -> float | None:
    """
    Compute the next Unix timestamp for a cron expression in the user's timezone.
    Returns None if croniter is not available or expression is invalid.
    """
    try:
        from croniter import croniter
        import zoneinfo

        tz = zoneinfo.ZoneInfo(user_timezone)
        now = datetime.now(tz)
        cron = croniter(cron_expr, now)
        next_dt = cron.get_next(datetime)
        # Convert to UTC timestamp for Redis ZADD
        return next_dt.timestamp()
    except ImportError:
        logger.warning("croniter not installed — cannot compute next run")
        return None
    except Exception as exc:
        logger.error("Failed to compute next run: %s", exc)
        return None


def schedule_user_briefings(user_id: str, timezone_str: str = "America/New_York") -> list[dict]:
    """
    Generate scheduled job payloads for a user's default briefings (D-02).
    Returns list of {payload, scheduled_at} dicts.

    Default: morning briefing at 8:00 AM, evening recap at 6:00 PM user local time.
    """
    jobs = []
    for job_type, cron_expr in [
        ("morning_briefing", "0 8 * * *"),
        ("evening_recap", "0 18 * * *"),
    ]:
        next_time = compute_next_run(cron_expr, timezone_str)
        if next_time:
            jobs.append({
                "payload": {
                    "type": job_type,
                    "user_id": user_id,
                    "timezone": timezone_str,
                    "cron": cron_expr,
                    "reschedule_at": True,
                    "source": "scheduler",
                },
                "scheduled_at": next_time,
            })
    return jobs
