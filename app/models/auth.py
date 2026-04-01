"""UserSession — refresh token store for JWT rotation.

Each login creates a session row. Logout deletes it. Refresh validates and rotates it.
token_hash stores SHA-256 of the raw refresh token — never the raw token itself.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserSession(Base):
    """Refresh token row — one per active login session per user."""

    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True)  # SHA-256 of raw refresh token
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="sessions")
