"""
Regression guard tests — verify every debug-session fix is still present.

These are structural checks (grep, import, attribute) that catch silent
reversions from worktree merges or careless refactors. They do NOT test
behavior — they verify the fix code EXISTS in the codebase.

Each test is named after the debug session that produced the fix.
If a test fails, the corresponding .planning/debug/<slug>.md has context.
"""
import ast
import os
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(relpath: str) -> str:
    with open(os.path.join(ROOT, relpath)) as f:
        return f.read()


# ─── 1. PostgreSQL healthcheck includes -d assistant ─────────────────────────
# Debug: postgres-no-database.md

def test_pg_healthcheck_targets_assistant_db():
    content = _read("docker-compose.yml")
    assert "-d assistant" in content, (
        "pg_isready healthcheck missing '-d assistant' — will spam 'database operator does not exist'"
    )


# ─── 2. Alembic fallback URLs reference assistant, not operator ──────────────
# Debug: postgres-no-database.md

def test_alembic_ini_no_operator_db():
    content = _read("alembic.ini")
    assert "localhost/operator" not in content, (
        "alembic.ini still references 'operator' database — should be 'assistant'"
    )


def test_alembic_env_no_operator_db():
    content = _read("alembic/env.py")
    assert "localhost/operator" not in content, (
        "alembic/env.py still references 'operator' database — should be 'assistant'"
    )


# ─── 3. No register_vector in database.py ────────────────────────────────────
# Debug: vector-string-cast.md

def test_no_register_vector_in_database():
    content = _read("app/database.py")
    assert "register_vector" not in content, (
        "register_vector still present in database.py — conflicts with SQLAlchemy VECTOR type"
    )


def test_no_event_import_in_database():
    content = _read("app/database.py")
    assert "from sqlalchemy import event" not in content, (
        "sqlalchemy event import still in database.py — register_vector listener should be removed"
    )


# ─── 4. Migration 003 has _is_postgres guard ─────────────────────────────────
# Debug: alembic-sqlite-extension.md

def test_migration_003_has_postgres_guard():
    content = _read("alembic/versions/003_persona_and_vectors.py")
    assert "_is_postgres" in content, (
        "Migration 003 missing _is_postgres() guard — CREATE EXTENSION will crash on SQLite"
    )


# ─── 5. Connections startup migration for persona_id ─────────────────────────
# Debug: connections-missing-persona-id.md

def test_connections_startup_migration_exists():
    content = _read("connections/app/main.py")
    assert "_run_startup_migrations" in content, (
        "connections/app/main.py missing _run_startup_migrations — persona_id won't be added"
    )
    assert "persona_id" in content, (
        "connections/app/main.py startup migration doesn't reference persona_id"
    )


# ─── 6. Login uses reactive useEffect pattern ────────────────────────────────
# Debug: double-login-required.md

def test_login_uses_pending_redirect():
    content = _read("dashboard/src/routes/auth/login.tsx")
    assert "pendingRedirect" in content, (
        "login.tsx missing pendingRedirect ref — double-login bug will resurface"
    )
    # Should NOT have imperative navigate right after login()
    assert "login(access_token, user_id);\n    await router.invalidate" not in content, (
        "login.tsx still uses imperative invalidate after login() — race condition"
    )


# ─── 7. Router created once via useState ─────────────────────────────────────
# Debug: double-login-required.md

def test_router_wrapped_in_usestate():
    content = _read("dashboard/src/main.tsx")
    assert "useState" in content and "createRouter" in content, (
        "main.tsx must wrap createRouter in useState to prevent re-creation on re-render"
    )


# ─── 8. identity.py exists with identity_preamble ────────────────────────────
# Debug: assistant-settings-unused.md

def test_identity_module_exists():
    assert os.path.isfile(os.path.join(ROOT, "app/core/identity.py")), (
        "app/core/identity.py missing — assistant identity won't be used in prompts"
    )
    content = _read("app/core/identity.py")
    assert "def identity_preamble" in content, (
        "identity_preamble function missing from identity.py"
    )


# ─── 9. ACK uses identity_preamble ───────────────────────────────────────────
# Debug: assistant-settings-unused.md

def test_ack_uses_identity():
    content = _read("app/core/ack.py")
    assert "identity_preamble" in content, (
        "ack.py doesn't reference identity_preamble — ACKs won't use assistant name"
    )


# ─── 10. Greeter uses identity_preamble ──────────────────────────────────────
# Debug: assistant-settings-unused.md

def test_greeter_uses_identity():
    content = _read("app/core/greeter.py")
    assert "identity_preamble" in content, (
        "greeter.py doesn't reference identity_preamble — first greeting won't use assistant name"
    )


# ─── 11. personality_notes column on User model ──────────────────────────────
# Debug: assistant-settings-unused.md

def test_user_model_has_personality_notes():
    content = _read("app/memory/models.py")
    assert "personality_notes" in content, (
        "User model missing personality_notes column"
    )


def test_migration_004_exists():
    migration_dir = os.path.join(ROOT, "alembic/versions")
    files = os.listdir(migration_dir)
    assert any("004" in f and "personality" in f for f in files), (
        "Migration 004 for personality_notes not found"
    )


# ─── 12. Context methods include assistant_name ──────────────────────────────
# Debug: assistant-settings-unused.md

def test_context_methods_include_assistant_name():
    content = _read("app/memory/store.py")
    assert "assistant_name" in content, (
        "store.py context methods don't include assistant_name"
    )
    assert "personality_notes" in content, (
        "store.py context methods don't include personality_notes"
    )


# ─── 13. IDENTITY intent exists and is fast-path ─────────────────────────────
# Debug: fast-ack-sufficiency.md

def test_identity_intent_exists():
    from app.core.intent import IntentType
    assert hasattr(IntentType, "IDENTITY"), "IntentType.IDENTITY missing"


def test_identity_is_fast_path():
    from app.core.intent import IntentType, FAST_PATH_INTENTS
    assert IntentType.IDENTITY in FAST_PATH_INTENTS, (
        "IDENTITY not in FAST_PATH_INTENTS — will go through worker instead of direct response"
    )


# ─── 14. Meta questions route to manager (Phase 4: NEEDS_MANAGER) ────────────
# Debug: assistant-settings-unused.md (03-06 gap closure)
# Updated: Phase 4 (04-02) removed RECALL regex — meta questions now route
# through NEEDS_MANAGER to the LLM manager dispatch for richer handling.

def test_meta_who_am_i_routes_to_manager():
    from app.core.intent import classify_intent, IntentType
    intent = classify_intent("who am i")
    assert intent.type == IntentType.NEEDS_MANAGER, (
        f"'who am i' classified as {intent.type} instead of NEEDS_MANAGER"
    )


def test_meta_what_do_you_know_routes_to_manager():
    from app.core.intent import classify_intent, IntentType
    intent = classify_intent("what do you know about me")
    assert intent.type == IntentType.NEEDS_MANAGER, (
        f"'what do you know about me' classified as {intent.type} instead of NEEDS_MANAGER"
    )


# ─── 15. Race pattern in pipeline ────────────────────────────────────────────
# Debug: smart-ack-race.md

def test_pipeline_has_race_pattern():
    content = _read("app/core/pipeline.py")
    assert "wait_for_result" in content, (
        "pipeline.py missing wait_for_result — race pattern not implemented"
    )
    assert "claim_delivery" in content, (
        "pipeline.py missing claim_delivery — double-delivery prevention missing"
    )
    assert "RACE_WON" in content, (
        "pipeline.py missing RACE_WON log — race pattern not implemented"
    )
    assert "RACE_TIMEOUT" in content, (
        "pipeline.py missing RACE_TIMEOUT log — race pattern not implemented"
    )


# ─── 16. Queue client has race methods ───────────────────────────────────────
# Debug: smart-ack-race.md + race-double-delivery.md

def test_queue_client_has_wait_for_result():
    from app.queue.client import QueueClient
    assert hasattr(QueueClient, "wait_for_result"), (
        "QueueClient missing wait_for_result method"
    )


def test_queue_client_has_claim_delivery():
    from app.queue.client import QueueClient
    assert hasattr(QueueClient, "claim_delivery"), (
        "QueueClient missing claim_delivery method"
    )


# ─── 17. Personality notes persist in dashboard ──────────────────────────────
# Debug: personality-notes-no-persist.md

def test_dashboard_loads_personality_notes():
    content = _read("dashboard/src/routes/settings/assistant.tsx")
    assert "personality_notes" in content, (
        "assistant.tsx doesn't reference personality_notes — won't load saved value"
    )


# ─── 18. PATCH response uses personality_notes field name ────────────────────
# Debug: personality-notes-no-persist.md

def test_dashboard_api_returns_personality_notes():
    content = _read("app/routes/dashboard.py")
    assert "personality_notes" in content, (
        "dashboard.py doesn't reference personality_notes in GET/PATCH response"
    )


# ─── 19. Connections .dockerignore excludes .db files ────────────────────────

def test_connections_dockerignore_excludes_db():
    content = _read("connections/.dockerignore")
    assert "*.db" in content, (
        "connections/.dockerignore doesn't exclude *.db — stale DBs get baked into image"
    )
