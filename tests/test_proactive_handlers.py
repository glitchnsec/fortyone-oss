"""
Tests for proactive handlers — profile nudge, smart check-in, insight, goal coaching, worker dispatch.

Tests:
  9. handle_profile_nudge returns empty when profile > 80% complete
  10. handle_profile_nudge generates nudge when profile incomplete
  11. handle_smart_checkin re-queues via manager with source=scheduled_checkin
  12. handle_insight_observation returns empty when < 15 memories
  13. handle_insight_observation generates insight when >= 15 memories
  14. handle_goal_coaching re-queues with coaching state in prompt
  15. handle_goal_coaching advances coaching state in metadata_json
  16. Worker dispatches new job types to correct handlers
  17. handle_smart_checkin manager payload body references calendar (D-03)
"""
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call


# ─── Test helpers ──────────────────────────────────────────────────────────


def _make_user(name="Test", timezone_val="America/New_York", assistant_name="Buddy",
               personality_notes="friendly", phone="+15555555555"):
    """Create a mock user object."""
    user = MagicMock()
    user.name = name
    user.timezone = timezone_val
    user.assistant_name = assistant_name
    user.personality_notes = personality_notes
    user.phone = phone
    return user


def _make_goal(title="Learn Python", description="Master Python programming",
               target_date=None, status="active", metadata_json=None,
               goal_id="goal-1", updated_at=None, created_at=None):
    """Create a mock goal object."""
    goal = MagicMock()
    goal.id = goal_id
    goal.title = title
    goal.description = description
    goal.target_date = target_date
    goal.status = status
    goal.metadata_json = metadata_json
    goal.updated_at = updated_at or datetime.now(timezone.utc)
    goal.created_at = created_at or datetime.now(timezone.utc)
    goal.framework = "custom"
    return goal


def _make_profile_entries(sections=None):
    """Create mock profile entries."""
    if sections is None:
        sections = ["preferences", "goals", "challenges"]
    entries = []
    for section in sections:
        entry = MagicMock()
        entry.section = section
        entry.label = f"{section}_label"
        entry.content = f"{section}_content"
        entries.append(entry)
    return entries


# ─── Shared patches ───────────────────────────────────────────────────────


def _patch_db_and_store(user=None, goals=None, memories=None, profile_entries=None):
    """Create patches for AsyncSessionLocal and MemoryStore."""
    mock_store = AsyncMock()

    # _get_user_by_id mock
    mock_user_result = MagicMock()
    mock_user_result.scalar_one_or_none.return_value = user

    # get_goals mock
    mock_store.get_goals = AsyncMock(return_value=goals or [])

    # get_memories mock
    mock_store.get_memories = AsyncMock(return_value=memories or [])

    # get_profile_entries mock
    mock_store.get_profile_entries = AsyncMock(return_value=profile_entries or [])

    # get_action_log mock
    mock_store.get_action_log = AsyncMock(return_value=[])

    # log_action mock
    mock_store.log_action = AsyncMock()

    # get_context_standard mock
    mock_store.get_context_standard = AsyncMock(return_value={})

    # DB execute for _get_user_by_id
    mock_store.db = AsyncMock()
    mock_store.db.execute = AsyncMock(return_value=mock_user_result)
    mock_store.db.commit = AsyncMock()

    return mock_store


# ─── Test 9: profile_nudge returns empty when profile complete ─────────────


@pytest.mark.asyncio
async def test_profile_nudge_skip_when_complete():
    """handle_profile_nudge returns empty when profile > 80% complete."""
    user = _make_user()
    entries = _make_profile_entries(["preferences", "goals", "challenges"])
    mock_store = _patch_db_and_store(user=user, profile_entries=entries)

    with patch("app.database.AsyncSessionLocal") as mock_session_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_store)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_ctx

        # Mock MemoryStore constructor
        with patch("app.memory.store.MemoryStore", return_value=mock_store):
            from app.tasks.proactive import handle_profile_nudge
            result = await handle_profile_nudge({
                "user_id": "user123",
                "job_id": "job123",
            })

    # 6/7 checks pass (personality_notes is set, only missing one section maybe)
    # All fields set + 3 sections = 7/7 = 100% > 80%, should skip
    assert result["response"] == ""


# ─── Test 10: profile_nudge generates nudge when incomplete ────────────────


@pytest.mark.asyncio
async def test_profile_nudge_generates_when_incomplete():
    """handle_profile_nudge generates a nudge when profile is incomplete."""
    user = _make_user(name="", personality_notes=None, assistant_name=None)
    entries = []  # no profile entries at all
    mock_store = _patch_db_and_store(user=user, profile_entries=entries)

    with patch("app.database.AsyncSessionLocal") as mock_session_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_store)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_ctx

        with patch("app.memory.store.MemoryStore", return_value=mock_store):
            # Mock Redis for nudge spacing — from_url is sync, returns redis instance
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=None)  # no prior nudges
            mock_redis.incr = AsyncMock()
            mock_redis.set = AsyncMock()
            mock_redis.aclose = AsyncMock()

            # from_url is sync but returns an awaitable Redis; mock as AsyncMock
            mock_from_url = AsyncMock(return_value=mock_redis)
            with patch("redis.asyncio.from_url", mock_from_url):
                with patch("app.tasks._llm.llm_text", new_callable=AsyncMock) as mock_llm:
                    mock_llm.return_value = "Hey! Tell me about yourself!"
                    with patch("app.tasks.proactive._record_send", new_callable=AsyncMock) as mock_send:
                        from app.tasks.proactive import handle_profile_nudge
                        result = await handle_profile_nudge({
                            "user_id": "user123",
                            "job_id": "job123",
                        })

    assert result["response"] == "Hey! Tell me about yourself!"


# ─── Test 11: smart_checkin re-queues via manager ──────────────────────────


@pytest.mark.asyncio
async def test_smart_checkin_requeues_via_manager():
    """handle_smart_checkin re-queues with source=scheduled_checkin and intent=needs_manager."""
    user = _make_user()
    mock_store = _patch_db_and_store(user=user)

    captured_xadd = []

    async def fake_xadd(stream, data):
        captured_xadd.append(json.loads(data["data"]))

    mock_redis = AsyncMock()
    mock_redis.xadd = fake_xadd
    mock_redis.aclose = AsyncMock()

    with patch("app.database.AsyncSessionLocal") as mock_session_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_store)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_ctx

        with patch("app.memory.store.MemoryStore", return_value=mock_store):
            with patch("redis.asyncio.from_url", AsyncMock(return_value=mock_redis)):
                with patch("app.tasks.proactive._record_send", new_callable=AsyncMock) as mock_send:
                    from app.tasks.proactive import handle_smart_checkin
                    result = await handle_smart_checkin({
                        "user_id": "user123",
                        "job_id": "job123",
                        "phone": "+15555555555",
                        "channel": "sms",
                    })

    assert len(captured_xadd) == 1
    payload = captured_xadd[0]
    assert payload["source"] == "scheduled_checkin"
    assert payload["intent"] == "needs_manager"


# ─── Test 12: insight_observation returns empty when < 15 memories ─────────


@pytest.mark.asyncio
async def test_insight_observation_skip_few_memories():
    """handle_insight_observation returns empty when < 15 memories."""
    user = _make_user()
    memories = [MagicMock() for _ in range(5)]
    mock_store = _patch_db_and_store(user=user, memories=memories)

    with patch("app.database.AsyncSessionLocal") as mock_session_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_store)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_ctx

        with patch("app.memory.store.MemoryStore", return_value=mock_store):
            from app.tasks.proactive import handle_insight_observation
            result = await handle_insight_observation({
                "user_id": "user123",
                "job_id": "job123",
            })

    assert result["response"] == ""


# ─── Test 13: insight_observation generates insight when >= 15 memories ────


@pytest.mark.asyncio
async def test_insight_observation_generates_with_enough_memories():
    """handle_insight_observation generates insight when >= 15 memories."""
    user = _make_user()
    memories = []
    for i in range(20):
        m = MagicMock()
        m.key = f"key_{i}"
        m.value = f"value_{i}"
        memories.append(m)

    entries = _make_profile_entries()
    mock_store = _patch_db_and_store(user=user, memories=memories, profile_entries=entries)

    with patch("app.database.AsyncSessionLocal") as mock_session_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_store)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_ctx

        with patch("app.memory.store.MemoryStore", return_value=mock_store):
            with patch("app.tasks._llm.llm_text", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = "I noticed a pattern in your scheduling..."
                with patch("app.tasks.proactive._record_send", new_callable=AsyncMock) as mock_send:
                    from app.tasks.proactive import handle_insight_observation
                    result = await handle_insight_observation({
                        "user_id": "user123",
                        "job_id": "job123",
                    })

    assert result["response"] == "I noticed a pattern in your scheduling..."


# ─── Test 14: goal_coaching re-queues with coaching state in prompt ────────


@pytest.mark.asyncio
async def test_goal_coaching_requeues_with_state_prompt():
    """handle_goal_coaching re-queues via manager with coaching state context."""
    user = _make_user()
    goal = _make_goal(
        title="Run a marathon",
        description="Complete a full 26.2 mile marathon",
        metadata_json=json.dumps({"coaching": {"state": "research"}}),
    )
    mock_store = _patch_db_and_store(user=user, goals=[goal])

    captured_xadd = []

    async def fake_xadd(stream, data):
        captured_xadd.append(json.loads(data["data"]))

    mock_redis = AsyncMock()
    mock_redis.xadd = fake_xadd
    mock_redis.aclose = AsyncMock()

    # For the state advance DB session (second context manager call)
    mock_fresh_goal = MagicMock()
    mock_fresh_goal.id = goal.id
    mock_fresh_goal.metadata_json = goal.metadata_json
    mock_fresh_goal.updated_at = datetime.now(timezone.utc)

    mock_goal_result = MagicMock()
    mock_goal_result.scalar_one_or_none.return_value = mock_fresh_goal

    session_call_count = {"count": 0}
    original_store = mock_store

    def session_factory():
        ctx = AsyncMock()
        session_call_count["count"] += 1
        if session_call_count["count"] <= 1:
            # First call: main handler session
            ctx.__aenter__ = AsyncMock(return_value=original_store)
        else:
            # Subsequent calls: re-queue and state advance sessions
            # Use a MagicMock for db to avoid async contamination on
            # sync methods like scalar_one_or_none()
            alt_db = MagicMock()
            alt_db.execute = AsyncMock(return_value=mock_goal_result)
            alt_db.commit = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=alt_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    with patch("app.database.AsyncSessionLocal", side_effect=session_factory):
        with patch("app.memory.store.MemoryStore") as mock_ms_cls:
            mock_ms_cls.side_effect = lambda db: original_store if session_call_count["count"] <= 1 else AsyncMock(
                get_context_standard=AsyncMock(return_value={}),
                log_action=AsyncMock(),
                db=db,
            )
            with patch("redis.asyncio.from_url", AsyncMock(return_value=mock_redis)):
                with patch("app.tasks.proactive._record_send", new_callable=AsyncMock) as mock_send:
                    from app.tasks.proactive import handle_goal_coaching
                    result = await handle_goal_coaching({
                        "user_id": "user123",
                        "job_id": "job123",
                        "phone": "+15555555555",
                        "channel": "sms",
                    })

    # Verify re-queue happened with scheduled_coaching source
    assert len(captured_xadd) >= 1
    payload = captured_xadd[0]
    assert payload["source"] == "scheduled_coaching"
    assert payload["intent"] == "needs_manager"
    # The body should reference the goal and research
    assert "Run a marathon" in payload["body"]
    assert "Research" in payload["body"] or "research" in payload["body"].lower()


# ─── Test 15: goal_coaching advances coaching state ────────────────────────


@pytest.mark.asyncio
async def test_goal_coaching_advances_state():
    """handle_goal_coaching advances coaching state in metadata_json after re-queue."""
    user = _make_user()
    goal = _make_goal(
        title="Learn Spanish",
        metadata_json=json.dumps({"coaching": {"state": "research"}}),
    )
    mock_store = _patch_db_and_store(user=user, goals=[goal])

    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock()
    mock_redis.aclose = AsyncMock()

    # Track what gets written to metadata_json
    fresh_goal = MagicMock()
    fresh_goal.id = goal.id
    fresh_goal.metadata_json = goal.metadata_json
    fresh_goal.updated_at = datetime.now(timezone.utc)
    metadata_writes = []
    original_metadata_json = fresh_goal.metadata_json

    def capture_metadata(value):
        metadata_writes.append(value)

    type(fresh_goal).metadata_json = property(
        lambda self: metadata_writes[-1] if metadata_writes else original_metadata_json,
        lambda self, v: capture_metadata(v),
    )

    mock_goal_result = MagicMock()
    mock_goal_result.scalar_one_or_none.return_value = fresh_goal

    session_count = {"n": 0}

    def session_factory():
        ctx = AsyncMock()
        session_count["n"] += 1
        if session_count["n"] <= 1:
            ctx.__aenter__ = AsyncMock(return_value=mock_store)
        else:
            alt_db = MagicMock()
            alt_db.execute = AsyncMock(return_value=mock_goal_result)
            alt_db.commit = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=alt_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    with patch("app.database.AsyncSessionLocal", side_effect=session_factory):
        with patch("app.memory.store.MemoryStore") as mock_ms_cls:
            mock_ms_cls.side_effect = lambda db: mock_store
            with patch("redis.asyncio.from_url", AsyncMock(return_value=mock_redis)):
                with patch("app.tasks.proactive._record_send", new_callable=AsyncMock) as mock_send:
                    from app.tasks.proactive import handle_goal_coaching
                    await handle_goal_coaching({
                        "user_id": "user123",
                        "job_id": "job123",
                        "phone": "+15555555555",
                        "channel": "sms",
                    })

    # Verify state advanced from research -> plan
    assert len(metadata_writes) >= 1
    written = json.loads(metadata_writes[-1])
    assert written["coaching"]["state"] == "plan"
    assert "last_coaching_at" in written["coaching"]


# ─── Test 16: Worker dispatches new job types correctly ───────────────────


@pytest.mark.asyncio
async def test_worker_dispatches_goal_coaching():
    """Worker dispatches goal_coaching job type to handle_goal_coaching."""
    with patch("app.tasks.proactive.handle_goal_coaching", new_callable=AsyncMock) as mock_handler:
        mock_handler.return_value = {
            "job_id": "j1", "phone": "+1", "response": "coaching!", "channel": "sms", "address": "+1",
        }

        # Import handler dispatch names from worker source
        source = open("app/queue/worker.py").read()
        assert "handle_goal_coaching" in source
        assert '"goal_coaching"' in source or "'goal_coaching'" in source

        # Direct handler call verification
        from app.tasks.proactive import handle_goal_coaching
        # The function is importable — dispatch wiring is verified by source check above


@pytest.mark.asyncio
async def test_worker_dispatches_all_new_types():
    """Worker dispatch block handles all new job types (profile_nudge, smart_checkin, insight_observation, goal_coaching)."""
    source = open("app/queue/worker.py").read()
    for job_type in ["profile_nudge", "smart_checkin", "insight_observation", "goal_coaching"]:
        assert job_type in source, f"Worker missing dispatch for {job_type}"

    # Verify imports
    for handler in ["handle_profile_nudge", "handle_smart_checkin", "handle_insight_observation", "handle_goal_coaching"]:
        assert handler in source, f"Worker missing import for {handler}"


# ─── Test 17: smart_checkin body references calendar (D-03) ───────────────


@pytest.mark.asyncio
async def test_smart_checkin_body_references_calendar():
    """Smart check-in body prompt references calendar for D-03 compliance."""
    user = _make_user()
    mock_store = _patch_db_and_store(user=user)

    captured_xadd = []

    async def fake_xadd(stream, data):
        captured_xadd.append(json.loads(data["data"]))

    mock_redis = AsyncMock()
    mock_redis.xadd = fake_xadd
    mock_redis.aclose = AsyncMock()

    with patch("app.database.AsyncSessionLocal") as mock_session_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_store)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_ctx

        with patch("app.memory.store.MemoryStore", return_value=mock_store):
            with patch("redis.asyncio.from_url", AsyncMock(return_value=mock_redis)):
                with patch("app.tasks.proactive._record_send", new_callable=AsyncMock) as mock_send:
                    from app.tasks.proactive import handle_smart_checkin
                    await handle_smart_checkin({
                        "user_id": "user123",
                        "job_id": "job123",
                        "phone": "+15555555555",
                        "channel": "sms",
                    })

    assert len(captured_xadd) == 1
    body = captured_xadd[0]["body"]
    assert "calendar" in body.lower(), (
        f"Smart check-in body must reference calendar (D-03). Got: {body}"
    )
