import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


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

    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="user", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")


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

    user = relationship("User", back_populates="memories")


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

    user = relationship("User", back_populates="messages")
