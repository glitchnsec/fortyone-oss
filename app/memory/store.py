import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.memory.models import Memory, Message, Task, User


class MemoryStore:
    """All DB interactions go through here. Injected into pipeline and tasks."""

    def __init__(self, db: Session):
        self.db = db

    # ─── Users ───────────────────────────────────────────────────────────────

    def get_or_create_user(self, phone: str) -> User:
        user = self.db.query(User).filter(User.phone == phone).first()
        if not user:
            user = User(phone=phone)
            self.db.add(user)
            self.db.commit()
            self.db.refresh(user)
        else:
            user.last_seen_at = datetime.now(timezone.utc)
            self.db.commit()
        return user

    def update_user_name(self, user_id: str, name: str) -> None:
        user = self.db.query(User).filter(User.id == user_id).first()
        if user:
            user.name = name
            self.db.commit()

    def update_user_timezone(self, user_id: str, timezone: str) -> None:
        user = self.db.query(User).filter(User.id == user_id).first()
        if user:
            user.timezone = timezone
            self.db.commit()

    # ─── Memory ──────────────────────────────────────────────────────────────

    def store_memory(
        self,
        user_id: str,
        memory_type: str,
        key: str,
        value: str,
        confidence: float = 1.0,
    ) -> Memory:
        existing = (
            self.db.query(Memory)
            .filter(Memory.user_id == user_id, Memory.key == key)
            .first()
        )
        if existing:
            existing.value = value
            existing.confidence = confidence
            existing.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            return existing

        memory = Memory(
            user_id=user_id,
            memory_type=memory_type,
            key=key,
            value=value,
            confidence=confidence,
        )
        self.db.add(memory)
        self.db.commit()
        self.db.refresh(memory)
        return memory

    def get_memories(
        self, user_id: str, memory_type: Optional[str] = None
    ) -> list[Memory]:
        q = self.db.query(Memory).filter(Memory.user_id == user_id)
        if memory_type:
            q = q.filter(Memory.memory_type == memory_type)
        return q.order_by(Memory.updated_at.desc()).all()

    def get_context(self, user_id: str) -> dict:
        """
        Assemble the full context packet that gets passed into every worker job.
        Keeps the critical path thin — DB reads only, no LLM.
        """
        recent_messages = (
            self.db.query(Message)
            .filter(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
            .limit(10)
            .all()
        )

        all_memories = self.get_memories(user_id)
        memory_dict = {m.key: m.value for m in all_memories}

        active_tasks = self.get_active_tasks(user_id)

        user = self.db.query(User).filter(User.id == user_id).first()

        return {
            "user": {
                "id": user_id,
                "name": user.name,
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

    def store_task(
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
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_active_tasks(
        self, user_id: str, task_type: Optional[str] = None
    ) -> list[Task]:
        q = self.db.query(Task).filter(
            Task.user_id == user_id, Task.completed == False  # noqa: E712
        )
        if task_type:
            q = q.filter(Task.task_type == task_type)
        return q.order_by(Task.due_at.asc().nullslast()).all()

    def complete_task(self, task_id: str) -> bool:
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if task:
            task.completed = True
            task.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            return True
        return False

    # ─── Messages ────────────────────────────────────────────────────────────

    def store_message(
        self,
        user_id: str,
        direction: str,
        body: str,
        intent: Optional[str] = None,
        state: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> Message:
        message = Message(
            user_id=user_id,
            direction=direction,
            body=body,
            intent=intent,
            state=state,
            job_id=job_id,
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def message_count(self, user_id: str) -> int:
        return (
            self.db.query(Message)
            .filter(Message.user_id == user_id, Message.direction == "inbound")
            .count()
        )
