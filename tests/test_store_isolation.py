"""
Integration tests for MemoryStore user isolation.

Verifies that:
  1. Tasks from user A are never returned when querying user B.
  2. complete_task() requires the correct user_id — cross-user completion fails.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.memory.models import Base
from app.memory.store import MemoryStore


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncSession() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_cross_user_task_isolation(db_session):
    store = MemoryStore(db_session)
    user_a = await store.get_or_create_user("+10000000001")
    user_b = await store.get_or_create_user("+10000000002")

    await store.store_task(user_id=user_a.id, task_type="reminder", title="User A task")
    await store.store_task(user_id=user_b.id, task_type="reminder", title="User B task")

    tasks_a = await store.get_active_tasks(user_a.id)
    tasks_b = await store.get_active_tasks(user_b.id)

    assert len(tasks_a) == 1
    assert tasks_a[0].title == "User A task"
    assert len(tasks_b) == 1
    assert tasks_b[0].title == "User B task"


@pytest.mark.asyncio
async def test_complete_task_requires_correct_user(db_session):
    store = MemoryStore(db_session)
    user_a = await store.get_or_create_user("+10000000003")
    user_b = await store.get_or_create_user("+10000000004")
    task = await store.store_task(user_id=user_a.id, task_type="reminder", title="A's task")

    # User B cannot complete User A's task
    result = await store.complete_task(task_id=task.id, user_id=user_b.id)
    assert result is False

    # User A can complete their own task
    result = await store.complete_task(task_id=task.id, user_id=user_a.id)
    assert result is True
