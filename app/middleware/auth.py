"""get_current_user — FastAPI dependency for JWT Bearer token validation.

Usage:
    from app.middleware.auth import get_current_user

    @app.get("/protected")
    async def protected(user: User = Depends(get_current_user)):
        return {"user_id": user.id}

Raises HTTPException(401) if:
  - No Authorization: Bearer header
  - Token is expired or signature invalid
  - User ID in token does not exist in the database
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.memory.models import User

_bearer = HTTPBearer()


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(_get_db),
) -> User:
    """Validate JWT Bearer token; return the authenticated User or raise 401."""
    s = get_settings()
    try:
        payload = jwt.decode(
            credentials.credentials,
            s.jwt_secret,
            algorithms=[s.jwt_algorithm],
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(401, "Invalid token")
    except JWTError:
        raise HTTPException(401, "Invalid token")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    return user
