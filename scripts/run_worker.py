#!/usr/bin/env python3
"""
Entry point for the async worker process.

Run:
    python scripts/run_worker.py

The worker connects to Redis, pulls jobs from the queue, processes them with
LLM calls and DB writes, then publishes results back via pub/sub for the
FastAPI service to pick up and deliver to the user.
"""
import asyncio
import logging
import os
import sys

# Make sure the project root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Initialise DB tables before any task handler runs
from app.database import init_db  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    logger.info("Worker DB initialised")

    from app.queue.worker import Worker
    worker = Worker()
    await worker.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped")
