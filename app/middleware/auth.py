"""get_current_user / require_admin -- FastAPI dependencies for JWT Bearer auth.

Usage:
    from app.middleware.auth import get_current_user, require_admin

    @app.get("/protected")
    async def protected(user: User = Depends(get_current_user)):
        return {"user_id": user.id}

    @app.get("/admin-only")
    async def admin_only(user: User = Depends(require_admin)):
        return {"admin": user.id}

get_current_user raises HTTPException(401) if:
  - No Authorization: Bearer header
  - Token is expired or signature invalid
  - User ID in token does not exist in the database

get_current_user raises HTTPException(403) if:
  - User account is soft-deleted (deleted_at is set)
  - User account is suspended (suspended_at is set)

require_admin raises HTTPException(403) if:
  - User's DB role is not 'admin'
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    """Validate JWT Bearer token; return the authenticated User or raise 401/403."""
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

    # Store JWT role claim on user for informational purposes
    jwt_role = payload.get("role", "user")

    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")

    # Reject deleted or suspended accounts
    if user.deleted_at:
        raise HTTPException(403, "Account deleted")
    if user.suspended_at:
        raise HTTPException(403, "Account suspended")

    # Attach JWT role claim for convenience (frontend display)
    user._jwt_role = jwt_role  # type: ignore[attr-defined]
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require the authenticated user to have the 'admin' role in the database.

    The DB role relationship is the authoritative gate -- not the JWT claim.
    """
    if not user.role or user.role.name != "admin":
        raise HTTPException(403, "Admin access required")
    return user
