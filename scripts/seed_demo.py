#!/usr/bin/env python3
"""
Seed a demo user with memories, preferences, and sample tasks.

Run:
    python scripts/seed_demo.py

Useful for testing the full pipeline without having to send a bunch of
real/mock SMS messages first.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.memory.store import MemoryStore

DEMO_PHONE = "+15550001234"


def seed() -> None:
    init_db()
    db = SessionLocal()
    store = MemoryStore(db)

    # ── User ─────────────────────────────────────────────────────────────────
    user = store.get_or_create_user(DEMO_PHONE)
    user.name = "Alex"
    user.timezone = "America/New_York"
    db.commit()
    print(f"User: {user.name} ({user.phone})")

    # ── Long-term memories ───────────────────────────────────────────────────
    long_term = [
        ("name", "Alex"),
        ("timezone", "America/New_York"),
        ("preferred_meeting_time", "morning"),
        ("preferred_days", "Tuesday and Thursday"),
        ("work_hours", "9am–6pm EST"),
        ("communication_style", "brief and direct"),
    ]
    for key, value in long_term:
        store.store_memory(user.id, "long_term", key, value)
        print(f"  Memory (long_term): {key} = {value}")

    # ── Behavioral memories ──────────────────────────────────────────────────
    behavioral = [
        ("preferred_time_of_day", "morning"),
        ("reminder_count", "3"),
        ("scheduling_requests", "2"),
    ]
    for key, value in behavioral:
        store.store_memory(user.id, "behavioral", key, value, confidence=0.75)
        print(f"  Memory (behavioral): {key} = {value}")

    # ── Sample tasks ─────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)

    tasks = [
        {
            "task_type": "reminder",
            "title": "Call John about Q2 budget",
            "due_at": now + timedelta(days=1, hours=3),
            "metadata": {"contact": "John", "recurrence": "none"},
        },
        {
            "task_type": "follow_up",
            "title": "Follow up with Sarah on contract",
            "due_at": now + timedelta(days=3),
            "metadata": {"contact": "Sarah"},
        },
        {
            "task_type": "reminder",
            "title": "Renew gym membership",
            "due_at": now + timedelta(days=7),
            "metadata": {"recurrence": "monthly"},
        },
    ]

    for t in tasks:
        task = store.store_task(
            user_id=user.id,
            task_type=t["task_type"],
            title=t["title"],
            due_at=t["due_at"],
            metadata=t.get("metadata"),
        )
        print(f"  Task: [{task.id[:8]}] {task.title}")

    db.close()
    print(f"\nSeed complete — demo user phone: {DEMO_PHONE}")
    print("\nTest flows:")
    print(f'  curl -X POST http://localhost:8000/sms/inbound -d "From={DEMO_PHONE}&Body=What+reminders+do+I+have"')
    print(f'  curl -X POST http://localhost:8000/sms/inbound -d "From={DEMO_PHONE}&Body=Remind+me+to+call+Mom+tomorrow+at+9am"')
    print(f'  curl -X POST http://localhost:8000/sms/inbound -d "From={DEMO_PHONE}&Body=When+should+I+schedule+a+team+sync"')
    print(f'  curl -X POST http://localhost:8000/sms/inbound -d "From={DEMO_PHONE}&Body=I+prefer+morning+meetings+before+10am"')


if __name__ == "__main__":
    seed()
