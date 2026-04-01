"""List and delete user connections."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Connection
from app.providers.google import get_provider

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/connections/{user_id}")
async def list_connections(user_id: str, db: AsyncSession = Depends(_get_db)):
    result = await db.execute(select(Connection).where(Connection.user_id == user_id))
    conns = result.scalars().all()
    out = []
    for c in conns:
        p = get_provider(c.provider)
        scopes = c.granted_scopes.split(" ") if c.granted_scopes else []
        manifest = p.capability_manifest(scopes)
        out.append({
            "id": c.id,
            "provider": c.provider,
            "status": c.status,
            "capabilities": {
                "can_read_email": manifest.can_read_email,
                "can_send_email": manifest.can_send_email,
                "can_read_calendar": manifest.can_read_calendar,
                "can_write_calendar": manifest.can_write_calendar,
            },
        })
    return {"connections": out}


@router.delete("/connections/{conn_id}", status_code=204)
async def delete_connection(conn_id: str, db: AsyncSession = Depends(_get_db)):
    result = await db.execute(select(Connection).where(Connection.id == conn_id))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    await db.delete(conn)
    await db.commit()
    logger.info("Connection deleted id=%s", conn_id)
