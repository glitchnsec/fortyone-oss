"""
Persona management endpoints.

Endpoints:
  GET    /api/v1/personas            — list personas for authenticated user
  POST   /api/v1/personas            — create a new persona
  PATCH  /api/v1/personas/{id}       — update name, description, or tone_notes
  DELETE /api/v1/personas/{id}       — soft-delete (marks is_active=False) via hard delete

All routes require a valid Bearer JWT (get_current_user dependency).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.memory.store import MemoryStore
from app.middleware.auth import get_current_user

router = APIRouter(prefix="/api/v1/personas", tags=["Personas"])
logger = logging.getLogger(__name__)


async def _get_db():
    async with AsyncSessionLocal() as db:
        yield db


class PersonaCreate(BaseModel):
    name: str
    description: Optional[str] = None
    tone_notes: Optional[str] = None


class PersonaUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tone_notes: Optional[str] = None


class PersonaResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    tone_notes: Optional[str]
    is_active: bool
    created_at: str


def _to_response(p) -> PersonaResponse:
    return PersonaResponse(
        id=p.id,
        name=p.name,
        description=p.description,
        tone_notes=p.tone_notes,
        is_active=p.is_active,
        created_at=p.created_at.isoformat() if p.created_at else "",
    )


@router.get("", response_model=list[PersonaResponse])
async def list_personas(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return all active personas for the authenticated user."""
    store = MemoryStore(db)
    personas = await store.get_personas(current_user.id)
    return [_to_response(p) for p in personas]


@router.post("", response_model=PersonaResponse, status_code=status.HTTP_201_CREATED)
async def create_persona(
    payload: PersonaCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Create a new persona for the authenticated user."""
    store = MemoryStore(db)
    persona = await store.create_persona(
        user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        tone_notes=payload.tone_notes,
    )
    logger.info("PERSONA_CREATED user_id=%s persona_id=%s name=%r", current_user.id, persona.id, persona.name)
    return _to_response(persona)


@router.patch("/{persona_id}", response_model=PersonaResponse)
async def update_persona(
    persona_id: str,
    payload: PersonaUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Update name, description, or tone_notes on an owned persona."""
    store = MemoryStore(db)
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    persona = await store.update_persona(current_user.id, persona_id, **updates)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return _to_response(persona)


@router.delete("/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_persona(
    persona_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Delete a persona owned by the authenticated user."""
    store = MemoryStore(db)
    deleted = await store.delete_persona(current_user.id, persona_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Persona not found")
