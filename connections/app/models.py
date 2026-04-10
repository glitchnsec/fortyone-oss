"""SQLAlchemy models: Connection, OAuthToken, OAuthState."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


def _uuid():
    return str(uuid.uuid4())


def _utcnow():
    return datetime.now(timezone.utc)


class Connection(Base):
    __tablename__ = "connections"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, nullable=False, index=True)   # FK to main API users table
    provider = Column(String, nullable=False)              # "google"
    status = Column(String, default="connected")           # connected | needs_reauth | error
    granted_scopes = Column(Text, nullable=True)           # space-separated scope URIs
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)
    persona_id = Column(String, nullable=True)   # which persona this connection belongs to (per D-09)
    execution_type = Column(String, default="native")   # "native" or "mcp"
    mcp_server_url = Column(Text, nullable=True)        # URL of the MCP server (mcp connections only)
    mcp_tools_json = Column(Text, nullable=True)        # JSON array of discovered tool schemas
    display_name = Column(String, nullable=True)        # User-provided name (e.g. "Notion", "Linear")
    token = relationship("OAuthToken", back_populates="connection", uselist=False, cascade="all, delete-orphan")


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"
    id = Column(String, primary_key=True, default=_uuid)
    connection_id = Column(String, ForeignKey("connections.id"), nullable=False, unique=True)
    access_token_enc = Column(Text, nullable=False)    # Fernet-encrypted
    refresh_token_enc = Column(Text, nullable=True)    # Fernet-encrypted
    expires_at = Column(DateTime(timezone=True), nullable=True)
    connection = relationship("Connection", back_populates="token")


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state = Column(String, primary_key=True)       # CSRF token
    user_id = Column(String, nullable=False)
    persona_id = Column(String, nullable=True)     # which persona initiated this OAuth flow
    metadata_json = Column("metadata", Text, nullable=True)  # flow-specific JSON payload
    created_at = Column(DateTime(timezone=True), default=_utcnow)
