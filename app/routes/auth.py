"""Auth routes: register, login, refresh, logout, OTP verification.

Endpoints:
  POST /auth/register    — create a new account (email + phone + password)
  POST /auth/login       — validate credentials, return access token + httpOnly refresh cookie
  POST /auth/refresh     — exchange refresh cookie for new access token (rotates session)
  POST /auth/logout      — delete session row, clear cookie (idempotent 204)
  POST /auth/send-otp    — send SMS OTP via Twilio Verify (D-03, AUTH-02)
  POST /auth/verify-otp  — verify SMS OTP and set phone_verified=True on User (D-03)
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from jose import jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.memory.models import User
from app.middleware.auth import get_current_user as _get_current_user
from app.models.auth import UserSession

router = APIRouter()
logger = logging.getLogger(__name__)


def _hash_password(password: str) -> str:
    """Hash password with SHA-256 pre-hash + bcrypt (handles any password length)."""
    pre = hashlib.sha256(password.encode()).digest()
    return bcrypt.hashpw(pre, bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash (with SHA-256 pre-hash)."""
    pre = hashlib.sha256(password.encode()).digest()
    return bcrypt.checkpw(pre, hashed.encode())


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
async def register(body: RegisterInput, response: Response, db: AsyncSession = Depends(_get_db)):
    """Create a new user account and auto-login. Returns access_token; sets refresh_token cookie."""
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "An account with this email already exists. Sign in instead?")
    user = User(
        email=body.email,
        phone=body.phone,
        password_hash=_hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Auto-login: issue tokens so the user is authenticated immediately after registration
    access_token = _create_access_token(user.id)
    raw_refresh = secrets.token_urlsafe(64)
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(days=s.refresh_token_expire_days)
    session_row = UserSession(user_id=user.id, token_hash=_hash_token(raw_refresh), expires_at=exp)
    db.add(session_row)
    await db.commit()
    response.set_cookie(
        key="refresh_token", value=raw_refresh, httponly=True, secure=True,
        samesite="lax", max_age=s.refresh_token_expire_days * 86400, path="/auth",
    )
    return {"access_token": access_token, "user_id": user.id}


@router.post("/login")
async def login(body: LoginInput, response: Response, db: AsyncSession = Depends(_get_db)):
    """Validate credentials. Returns {access_token} in body; sets refresh_token httpOnly cookie."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not _verify_password(body.password, user.password_hash or ""):
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
    # Compare as naive UTC — SQLite doesn't preserve timezone info
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if not session_row or session_row.expires_at.replace(tzinfo=None) < now_utc:
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


# ─── SMS OTP Verification (D-03, AUTH-02) ─────────────────────────────────────

class SendOtpInput(BaseModel):
    phone: str  # E.164 format e.g. "+15551234567"


class VerifyOtpInput(BaseModel):
    phone: str
    code: str  # 6-digit code from Twilio Verify


@router.post("/send-otp")
async def send_otp(body: SendOtpInput):
    """Send SMS OTP via Twilio Verify. Called from onboarding step 2."""
    s = get_settings()
    if not s.twilio_verify_service_sid or not s.twilio_account_sid:
        # Dev mode: log the code, don't actually send
        logger.info("DEV MODE: OTP send skipped for phone=%s", body.phone)
        return {"status": "sent", "dev_mode": True}
    try:
        from twilio.rest import Client
        client = Client(s.twilio_account_sid, s.twilio_auth_token)
        client.verify.v2.services(s.twilio_verify_service_sid).verifications.create(
            to=body.phone, channel="sms"
        )
        return {"status": "sent"}
    except Exception as e:
        logger.error("OTP send failed phone=%s error=%s", body.phone, e, exc_info=True)
        raise HTTPException(500, "Failed to send verification code")


@router.post("/verify-otp")
async def verify_otp(
    body: VerifyOtpInput,
    user: User = Depends(_get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Verify SMS OTP and mark phone as verified on the User row."""
    s = get_settings()
    if not s.twilio_verify_service_sid or not s.twilio_account_sid:
        # Dev mode: accept any 6-digit code
        if len(body.code) == 6 and body.code.isdigit():
            await db.execute(
                update(User).where(User.id == user.id).values(phone_verified=True)
            )
            await db.commit()
            return {"verified": True}
        raise HTTPException(400, "Invalid verification code")
    try:
        from twilio.rest import Client
        client = Client(s.twilio_account_sid, s.twilio_auth_token)
        check = client.verify.v2.services(s.twilio_verify_service_sid).verification_checks.create(
            to=body.phone, code=body.code
        )
        if check.status != "approved":
            raise HTTPException(400, "Invalid or expired verification code")
        await db.execute(update(User).where(User.id == user.id).values(phone_verified=True))
        await db.commit()
        return {"verified": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("OTP verify failed phone=%s error=%s", body.phone, e, exc_info=True)
        raise HTTPException(500, "Verification check failed")
