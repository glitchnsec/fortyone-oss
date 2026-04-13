"""
Fix snowballed tool metadata in message history.

The tool-metadata-snowball bug caused each outbound message to store ALL
prior tool_calls (not just current turn's). This script deduplicates the
metadata_json for affected messages: keeps only tool_calls with IDs not
seen in earlier messages.

Run inside Docker: docker compose exec api python3 scripts/fix_snowball_metadata.py
"""
import asyncio
import json
import logging
from app.database import AsyncSessionLocal
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def fix():
    async with AsyncSessionLocal() as db:
        # Get all messages with tool metadata, ordered by time
        result = await db.execute(text(
            "SELECT id, user_id, created_at, metadata_json "
            "FROM messages "
            "WHERE metadata_json IS NOT NULL "
            "AND metadata_json != '{}' "
            "AND metadata_json LIKE '%tool_calls%' "
            "ORDER BY user_id, created_at"
        ))
        rows = list(result)
        logger.info("Found %d messages with tool metadata", len(rows))

        # Process per user
        current_user = None
        seen_ids: set[str] = set()
        fixed = 0

        for row in rows:
            msg_id, user_id, created_at, meta_raw = row

            # Reset seen IDs when user changes
            if user_id != current_user:
                current_user = user_id
                seen_ids = set()

            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
            tool_calls = meta.get("tool_calls", [])
            tool_results = meta.get("tool_results", [])

            if not tool_calls:
                continue

            # Deduplicate: keep only tool_calls not seen before
            unique_calls = []
            for tc in tool_calls:
                tc_id = tc.get("id", "")
                if tc_id not in seen_ids:
                    seen_ids.add(tc_id)
                    unique_calls.append(tc)

            if len(unique_calls) == len(tool_calls):
                # No duplicates in this message
                continue

            # Filter results to match unique calls only
            unique_ids = {tc["id"] for tc in unique_calls}
            unique_results = [tr for tr in tool_results if tr.get("tool_call_id") in unique_ids]

            # Build cleaned metadata
            if unique_calls:
                cleaned = {"tool_calls": unique_calls, "tool_results": unique_results}
            else:
                cleaned = None

            cleaned_json = json.dumps(cleaned) if cleaned else None

            logger.info(
                "FIX user=%s ts=%s: %d tool_calls -> %d (removed %d dupes)",
                user_id, created_at,
                len(tool_calls), len(unique_calls),
                len(tool_calls) - len(unique_calls),
            )

            await db.execute(
                text("UPDATE messages SET metadata_json = :meta WHERE id = :id"),
                {"meta": cleaned_json, "id": msg_id},
            )
            fixed += 1

        await db.commit()
        logger.info("Fixed %d messages", fixed)


asyncio.run(fix())
