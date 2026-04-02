"""
Async MemoryStore — all DB interactions go through here.

Every public method is async and filters by user_id to enforce tenant isolation.
Injected into the pipeline and task handlers via constructor dependency injection.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, nullslast
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.models import Memory, Message, Task, User


class MemoryStore:
    """All DB interactions go through here. Injected into pipeline and tasks."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ─── Users ───────────────────────────────────────────────────────────────

    async def get_or_create_user(self, phone: str) -> User:
        result = await self.db.execute(select(User).where(User.phone == phone))
        user = result.scalars().first()
        if not user:
            user = User(phone=phone)
            self.db.add(user)
            await self.db.commit()
            await self.db.refresh(user)
        else:
            user.last_seen_at = datetime.now(timezone.utc)
            await self.db.commit()
        return user

    async def lookup_by_email(self, email: str) -> Optional[User]:
        """Return the User with the given email, or None if not found."""
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def lookup_by_phone(self, phone: str) -> Optional[User]:
        """Return the User with the given phone number, or None if not found."""
        result = await self.db.execute(select(User).where(User.phone == phone))
        return result.scalar_one_or_none()

    async def update_user_name(self, user_id: str, name: str) -> None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalars().first()
        if user:
            user.name = name
            await self.db.commit()

    async def update_user_timezone(self, user_id: str, tz: str) -> None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalars().first()
        if user:
            user.timezone = tz
            await self.db.commit()

    # ─── Memory ──────────────────────────────────────────────────────────────

    async def store_memory(
        self,
        user_id: str,
        memory_type: str,
        key: str,
        value: str,
        confidence: float = 1.0,
        persona_tag: str = "shared",   # D-07: all memory writes tagged with persona context
    ) -> Memory:
        result = await self.db.execute(
            select(Memory).where(Memory.user_id == user_id, Memory.key == key)
        )
        existing = result.scalars().first()
        if existing:
            existing.value = value
            existing.confidence = confidence
            existing.updated_at = datetime.now(timezone.utc)
            existing.persona_tag = persona_tag
            await self.db.commit()
            return existing

        memory = Memory(
            user_id=user_id,
            memory_type=memory_type,
            key=key,
            value=value,
            confidence=confidence,
            persona_tag=persona_tag,
        )
        self.db.add(memory)
        await self.db.commit()
        await self.db.refresh(memory)
        return memory

    async def get_memories(
        self, user_id: str, memory_type: Optional[str] = None
    ) -> list[Memory]:
        stmt = select(Memory).where(Memory.user_id == user_id)
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type)
        stmt = stmt.order_by(Memory.updated_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_context(self, user_id: str, channel: str = "sms") -> dict:
        """
        Assemble the full context packet that gets passed into every worker job.
        Keeps the critical path thin — DB reads only, no LLM.

        Scoped to a specific channel (D-01, D-02) so SMS and Slack histories
        are never intermixed in the sliding window.
        Legacy rows (channel IS NULL) are treated as 'sms' for backward compat.
        """
        result = await self.db.execute(
            select(Message)
            .where(
                Message.user_id == user_id,
                (Message.channel == channel) | (Message.channel.is_(None)),
            )
            .order_by(Message.created_at.desc())
            .limit(20)   # D-01: last 20 messages
        )
        recent_messages = list(result.scalars().all())

        all_memories = await self.get_memories(user_id)
        memory_dict = {m.key: m.value for m in all_memories}

        active_tasks = await self.get_active_tasks(user_id)

        user_result = await self.db.execute(select(User).where(User.id == user_id))
        user = user_result.scalars().first()

        return {
            "user": {
                "id": user_id,
                "name": user.name if user else None,
                "timezone": user.timezone if user else "America/New_York",
                "phone": user.phone if user else "",
            },
            "recent_messages": [
                {
                    "direction": m.direction,
                    "body": m.body,
                    "at": m.created_at.isoformat(),
                    "intent": m.intent,
                }
                for m in reversed(recent_messages)
            ],
            "memories": memory_dict,
            "active_tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "due_at": t.due_at.isoformat() if t.due_at else None,
                    "type": t.task_type,
                }
                for t in active_tasks[:5]
            ],
            "message_count": len(recent_messages),
        }

    # ─── Tasks ───────────────────────────────────────────────────────────────

    async def store_task(
        self,
        user_id: str,
        task_type: str,
        title: str,
        description: Optional[str] = None,
        due_at: Optional[datetime] = None,
        metadata: Optional[dict] = None,
    ) -> Task:
        task = Task(
            user_id=user_id,
            task_type=task_type,
            title=title,
            description=description,
            due_at=due_at,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def get_active_tasks(
        self, user_id: str, task_type: Optional[str] = None
    ) -> list[Task]:
        stmt = (
            select(Task)
            .where(Task.user_id == user_id, Task.completed == False)  # noqa: E712
            .order_by(nullslast(Task.due_at.asc()))
        )
        if task_type:
            stmt = stmt.where(Task.task_type == task_type)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def complete_task(self, task_id: str, user_id: str) -> bool:
        """Mark a task complete — requires matching user_id to prevent cross-user completion."""
        result = await self.db.execute(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        task = result.scalars().first()
        if task:
            task.completed = True
            task.updated_at = datetime.now(timezone.utc)
            await self.db.commit()
            return True
        return False

    # ─── Messages ────────────────────────────────────────────────────────────

    async def store_message(
        self,
        user_id: str,
        direction: str,
        body: str,
        intent: Optional[str] = None,
        state: Optional[str] = None,
        job_id: Optional[str] = None,
        channel: Optional[str] = None,      # NEW — per D-01, D-02: scopes history per channel
        persona_tag: Optional[str] = None,  # NEW — per D-08: records active persona at send time
    ) -> Message:
        message = Message(
            user_id=user_id,
            direction=direction,
            body=body,
            intent=intent,
            state=state,
            job_id=job_id,
            channel=channel or "sms",   # default sms for backward compat
            persona_tag=persona_tag,
        )
        self.db.add(message)
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def message_count(self, user_id: str) -> int:
        result = await self.db.execute(
            select(Message).where(
                Message.user_id == user_id, Message.direction == "inbound"
            )
        )
        return len(result.scalars().all())

    # ─── Personas ────────────────────────────────────────────────────────────

    async def create_persona(
        self,
        user_id: str,
        name: str,
        description: Optional[str] = None,
        tone_notes: Optional[str] = None,
    ) -> "Persona":
        from app.memory.models import Persona
        persona = Persona(
            user_id=user_id,
            name=name,
            description=description,
            tone_notes=tone_notes,
        )
        self.db.add(persona)
        await self.db.commit()
        await self.db.refresh(persona)
        return persona

    async def get_personas(self, user_id: str) -> list["Persona"]:
        from app.memory.models import Persona
        result = await self.db.execute(
            select(Persona)
            .where(Persona.user_id == user_id, Persona.is_active == True)  # noqa: E712
            .order_by(Persona.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_persona(self, user_id: str, persona_id: str) -> Optional["Persona"]:
        from app.memory.models import Persona
        result = await self.db.execute(
            select(Persona).where(Persona.id == persona_id, Persona.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def update_persona(self, user_id: str, persona_id: str, **fields) -> Optional["Persona"]:
        persona = await self.get_persona(user_id, persona_id)
        if not persona:
            return None
        allowed = {"name", "description", "tone_notes", "is_active"}
        for key, value in fields.items():
            if key in allowed:
                setattr(persona, key, value)
        await self.db.commit()
        await self.db.refresh(persona)
        return persona

    async def delete_persona(self, user_id: str, persona_id: str) -> bool:
        persona = await self.get_persona(user_id, persona_id)
        if not persona:
            return False
        await self.db.delete(persona)
        await self.db.commit()
        return True
