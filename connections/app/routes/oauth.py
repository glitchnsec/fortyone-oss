"""OAuth initiate and callback routes."""
import secrets
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from authlib.integrations.httpx_client import AsyncOAuth2Client
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Connection, OAuthToken, OAuthState
from app.providers.google import get_provider
from app.crypto import encrypt

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/oauth/initiate/{provider}")
async def initiate_oauth(provider: str, user_id: str, persona_id: str, db: AsyncSession = Depends(_get_db)):
    p = get_provider(provider)
    s = get_settings()
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state=state, user_id=user_id, persona_id=persona_id))
    await db.commit()
    client = AsyncOAuth2Client(
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        redirect_uri=s.google_redirect_uri,
        scope=" ".join(p.scopes),
    )
    auth_url, _ = client.create_authorization_url(
        p.auth_url, state=state, access_type="offline", prompt="consent"
    )
    return {"auth_url": auth_url}


@router.get("/oauth/callback/{provider}")
async def oauth_callback(provider: str, code: str, state: str, db: AsyncSession = Depends(_get_db)):
    # Validate CSRF state
    result = await db.execute(select(OAuthState).where(OAuthState.state == state))
    state_row = result.scalar_one_or_none()
    if not state_row:
        raise HTTPException(400, "Invalid OAuth state")
    user_id = state_row.user_id
    persona_id = state_row.persona_id
    await db.execute(delete(OAuthState).where(OAuthState.state == state))

    s = get_settings()
    p = get_provider(provider)
    client = AsyncOAuth2Client(
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        redirect_uri=s.google_redirect_uri,
    )
    try:
        token_data = await client.fetch_token(p.token_url, code=code, grant_type="authorization_code")
    except Exception as e:
        logger.error("OAuth token exchange failed provider=%s error=%s", provider, e, exc_info=True)
        return RedirectResponse(f"{s.dashboard_url}/connections?error=token_exchange_failed")

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in")
    granted_scopes = token_data.get("scope", "")

    # Upsert: remove old connection for this user+provider+persona before creating new one
    old = await db.execute(
        select(Connection).where(
            Connection.user_id == user_id,
            Connection.provider == provider,
            Connection.persona_id == persona_id,
        )
    )
    old_conn = old.scalar_one_or_none()
    if old_conn:
        await db.delete(old_conn)
        await db.commit()

    conn = Connection(user_id=user_id, provider=provider, persona_id=persona_id, status="connected", granted_scopes=granted_scopes)
    db.add(conn)
    await db.flush()  # get conn.id

    exp = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
    db.add(OAuthToken(
        connection_id=conn.id,
        access_token_enc=encrypt(access_token),
        refresh_token_enc=encrypt(refresh_token) if refresh_token else None,
        expires_at=exp,
    ))
    await db.commit()
    logger.info("OAuth connected user_id=%s provider=%s", user_id, provider)
    return RedirectResponse(f"{s.dashboard_url}/connections?connected={provider}&persona_id={persona_id}")
