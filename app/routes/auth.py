"""Auth routes: register, login, refresh, logout.

Endpoints:
  POST /auth/register  — create a new account (email + phone + password)
  POST /auth/login     — validate credentials, return access token + httpOnly refresh cookie
  POST /auth/refresh   — exchange refresh cookie for new access token (rotates session)
  POST /auth/logout    — delete session row, clear cookie (idempotent 204)
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from jose import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.memory.models import User
from app.models.auth import UserSession

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class RegisterInput(BaseModel):
    email: EmailStr
    phone: str
    password: str


class LoginInput(BaseModel):
    email: EmailStr
    password: str


def _hash_token(token: str) -> str:
    """SHA-256 of the raw token — stored in DB, never the raw value."""
    return hashlib.sha256(token.encode()).hexdigest()


def _create_access_token(user_id: str) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.access_token_expire_minutes)
    return jwt.encode({"sub": user_id, "exp": exp}, s.jwt_secret, algorithm=s.jwt_algorithm)


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.post("/register", status_code=201)
async def register(body: RegisterInput, db: AsyncSession = Depends(_get_db)):
    """Create a new user account. Returns {user_id} on success; 409 if email already registered."""
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "An account with this email already exists. Sign in instead?")
    user = User(
        email=body.email,
        phone=body.phone,
        password_hash=pwd_context.hash(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"user_id": user.id}


@router.post("/login")
async def login(body: LoginInput, response: Response, db: AsyncSession = Depends(_get_db)):
    """Validate credentials. Returns {access_token} in body; sets refresh_token httpOnly cookie."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(body.password, user.password_hash or ""):
        raise HTTPException(401, "Email or password is incorrect. Try again or reset your password.")
    access_token = _create_access_token(user.id)
    raw_refresh = secrets.token_urlsafe(64)
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(days=s.refresh_token_expire_days)
    session_row = UserSession(user_id=user.id, token_hash=_hash_token(raw_refresh), expires_at=exp)
    db.add(session_row)
    await db.commit()
    response.set_cookie(
        "refresh_token",
        raw_refresh,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * s.refresh_token_expire_days,
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/refresh")
async def refresh(
    response: Response,
    refresh_token: str = Cookie(default=None),
    db: AsyncSession = Depends(_get_db),
):
    """Exchange a valid refresh cookie for a new access token. Rotates the session row."""
    if not refresh_token:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    h = _hash_token(refresh_token)
    result = await db.execute(select(UserSession).where(UserSession.token_hash == h))
    session_row = result.scalar_one_or_none()
    if not session_row or session_row.expires_at < datetime.now(timezone.utc):
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    user_id = session_row.user_id
    # Rotate: delete old session, create new one
    await db.execute(delete(UserSession).where(UserSession.token_hash == h))
    raw_new = secrets.token_urlsafe(64)
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(days=s.refresh_token_expire_days)
    db.add(UserSession(user_id=user_id, token_hash=_hash_token(raw_new), expires_at=exp))
    await db.commit()
    new_access = _create_access_token(user_id)
    response.set_cookie(
        "refresh_token",
        raw_new,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * s.refresh_token_expire_days,
    )
    return {"access_token": new_access, "token_type": "bearer"}


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    refresh_token: str = Cookie(default=None),
    db: AsyncSession = Depends(_get_db),
):
    """Delete session row and clear cookie. Always returns 204 (idempotent)."""
    if refresh_token:
        await db.execute(
            delete(UserSession).where(UserSession.token_hash == _hash_token(refresh_token))
        )
        await db.commit()
    response.delete_cookie("refresh_token")
