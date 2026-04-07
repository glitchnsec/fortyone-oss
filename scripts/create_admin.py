#!/usr/bin/env python3
"""Promote a user to admin by email address.

Usage:
    python scripts/create_admin.py user@example.com

Finds the user by email, looks up (or creates) the 'admin' role row,
sets user.role_id = admin.id, and commits.

Exit codes:
    0 — success
    1 — user not found or other error
"""
import asyncio
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure app modules are importable (PYTHONPATH=/app in Docker, or run from repo root)
from app.database import AsyncSessionLocal
from app.memory.models import Role, User


async def main(email: str) -> None:
    async with AsyncSessionLocal() as db:
        # Find user
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user:
            print(f"ERROR: No user found with email '{email}'")
            sys.exit(1)

        # Find or create admin role
        role_result = await db.execute(select(Role).where(Role.name == "admin"))
        admin_role = role_result.scalar_one_or_none()
        if not admin_role:
            admin_role = Role(id=str(uuid.uuid4()), name="admin")
            db.add(admin_role)
            await db.flush()

        # Promote user
        user.role_id = admin_role.id
        await db.commit()
        print(f"SUCCESS: {email} (user_id={user.id}) promoted to admin")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/create_admin.py <email>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
