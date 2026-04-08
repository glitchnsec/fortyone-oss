import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base

# pgvector Vector type — only available when pgvector is installed (PostgreSQL).
# Falls back to None for SQLite (dev/test) — embedding column omitted from model,
# but the column exists in production via ALTER TABLE in migration 003.
try:
    from pgvector.sqlalchemy import Vector as _Vector
except ImportError:
    _Vector = None  # type: ignore[assignment,misc]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Role(Base):
    """System roles: 'user' and 'admin'. Seeded by migration 006."""
    __tablename__ = "roles"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, unique=True, nullable=False)

    users = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_uuid)
    phone = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=True)
    timezone = Column(String, default="America/New_York")
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    last_seen_at = Column(DateTime(timezone=True), default=_utcnow)

    # Auth columns (nullable for backward compat — SMS-only users have no email)
    email = Column(String, unique=True, nullable=True, index=True)
    password_hash = Column(String, nullable=True)
    phone_verified = Column(Boolean, default=False)
    assistant_name = Column(String, nullable=True)  # Onboarding step 3 (DASH-02 / D-12)
    personality_notes = Column(Text, nullable=True)  # Free-form personality/tone notes
    proactive_settings_json = Column(Text, nullable=True)  # JSON: max_daily_messages, quiet_hours, briefing_times, enabled

    # Slack identity (migration 009)
    slack_user_id = Column(String, unique=True, nullable=True, index=True)
    pending_slack_link = Column(Text, nullable=True)  # JSON: {"slack_user_id": "...", "channel": "slack"}

    # Role-based access control (migration 006)
    role_id = Column(String, ForeignKey("roles.id"), nullable=True)
    role = relationship("Role", back_populates="users")

    # Soft-delete and suspension (migration 006)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    suspended_at = Column(DateTime(timezone=True), nullable=True)

    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="user", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    personas = relationship("Persona", back_populates="user", cascade="all, delete-orphan")
    goals = relationship("Goal", back_populates="user", cascade="all, delete-orphan")
    action_logs = relationship("ActionLog", back_populates="user", cascade="all, delete-orphan")
    profile_entries = relationship("UserProfile", back_populates="user", cascade="all, delete-orphan")
    proactive_preferences = relationship("ProactivePreference", back_populates="user", cascade="all, delete-orphan")
    custom_agents = relationship("CustomAgent", back_populates="user", cascade="all, delete-orphan")


class Memory(Base):
    """
    Stores short-term, long-term, and behavioral memories for a user.

    memory_type values:
      - short_term   : current conversation context (capped at N entries)
      - long_term    : explicit facts (name, preferences)
      - behavioral   : inferred patterns (e.g. prefers_mornings=true)
    """
    __tablename__ = "memories"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    memory_type = Column(String, nullable=False)
    key = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)
    persona_tag = Column(String, nullable=True)   # D-07: all memory writes tagged with persona context

    user = relationship("User", back_populates="memories")


# Register the embedding column on Memory when pgvector is available (PostgreSQL).
# This MUST happen after the class definition — dynamic column attachment is required
# because pgvector is not available in SQLite test environments.
# In SQLite mode the column is absent from the ORM but search_memories() is never
# called (embed_text always returns [] without a real PG connection).
if _Vector is not None:
    Memory.embedding = Column(_Vector(1536), nullable=True)  # type: ignore[attr-defined]


class Task(Base):
    """
    Captures reminders, follow-ups, and scheduled events.
    """
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    task_type = Column(String, nullable=False)  # reminder | follow_up | schedule
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True)
    completed = Column(Boolean, default=False)
    # JSON-encoded dict for arbitrary extra fields (contact, recurrence, etc.)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="tasks")


class Message(Base):
    """
    Full conversation history — inbound and outbound.
    """
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    direction = Column(String, nullable=False)   # inbound | outbound
    body = Column(Text, nullable=False)
    intent = Column(String, nullable=True)
    state = Column(String, nullable=True)        # pipeline state at time of write
    job_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    channel = Column(String, nullable=True, default="sms")      # D-01, D-02: scopes history per channel
    persona_tag = Column(String, nullable=True)                  # D-08: active persona at send time

    user = relationship("User", back_populates="messages")


class Persona(Base):
    """
    Work / Personal / custom identity contexts.

    Personas are credential profiles — each can have its own connections,
    context, and behavioral tone notes. The shared memory pool stores
    persona-tagged entries; detect_persona() routes each inbound message
    to the right context.
    """
    __tablename__ = "personas"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)           # "work" | "personal" | user-defined
    description = Column(Text, nullable=True)       # "I'm a PM at Acme Corp"
    tone_notes = Column(Text, nullable=True)        # "formal in work contexts"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="personas")


class Goal(Base):
    """
    User goals tracked via OKR, SMART, or custom frameworks.
    Supports hierarchical goal trees via parent_goal_id.
    """
    __tablename__ = "goals"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    persona_id = Column(String, ForeignKey("personas.id"), nullable=True)
    framework = Column(String, nullable=False, default="custom")  # okr | smart | custom
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    target_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, default="active")  # active | completed | archived
    parent_goal_id = Column(String, ForeignKey("goals.id"), nullable=True)
    metadata_json = Column(Text, nullable=True)
    version = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="goals")


class ActionLog(Base):
    """
    Audit trail for all actions taken by the proactive agent on behalf of a user.
    """
    __tablename__ = "action_log"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    action_type = Column(String, nullable=False)  # email_sent | event_created | search | briefing
    description = Column(Text, nullable=False)
    outcome = Column(String, nullable=True)  # success | failed | cancelled | pending
    trigger = Column(String, nullable=True)  # user_request | scheduled | event_driven
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="action_logs")


class PendingAction(Base):
    """
    Actions awaiting user confirmation before execution.
    High-risk actions (send_email, create_event) require explicit approval.
    """
    __tablename__ = "pending_actions"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    action_type = Column(String, nullable=False)
    action_params_json = Column(Text, nullable=False)
    risk_level = Column(String, nullable=False)  # low | medium | high
    status = Column(String, default="pending")  # pending | confirmed | rejected | expired
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User")


class UserProfile(Base):
    """
    Structured user profile entries using TELOS framework sections.
    Each entry is a labeled content snippet within a section.
    """
    __tablename__ = "user_profiles"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    section = Column(String, nullable=False)  # TELOS: problems | mission | goals | challenges | wisdom | ideas | predictions | preferences | narratives | history
    label = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    persona_id = Column(String, ForeignKey("personas.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="profile_entries")


class ProactivePreference(Base):
    """Per-user per-category proactive engagement preferences.

    Each row represents a user's override for one proactive category.
    If no row exists for a category, system defaults apply (enabled=true,
    default time windows from ProactiveCategory dataclass).
    """
    __tablename__ = "proactive_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "category_name", name="uq_proactive_pref_user_category"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    category_name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)
    window_start_hour = Column(Float, nullable=True)  # null = use system default
    window_end_hour = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="proactive_preferences")


class CustomAgent(Base):
    """User-defined custom agents: webhook, prompt, or YAML/script types."""
    __tablename__ = "custom_agents"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    agent_type = Column(String, nullable=False)  # webhook | prompt | yaml_script
    config_json = Column(Text, nullable=False)   # type-specific JSON config
    parameters_schema_json = Column(Text, nullable=True)  # OpenAI function parameters JSON
    risk_level = Column(String, default="low")   # low | medium | high
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="custom_agents")


# Import UserSession so SQLAlchemy can resolve the User.sessions relationship string reference.
# UserSession is defined in app/models/auth.py but User references it as relationship("UserSession").
# Without this import, any process that imports models.py without also importing auth.py
# (e.g. the worker) will crash with "failed to locate a name 'UserSession'".
from app.models.auth import UserSession  # noqa: E402, F401
