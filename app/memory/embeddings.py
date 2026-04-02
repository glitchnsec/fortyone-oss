"""
Embedding helper for semantic memory retrieval.

Calls OpenRouter /v1/embeddings (OpenAI-compatible endpoint) using the
OpenAI SDK already in requirements.

Model: openai/text-embedding-3-small → 1536 dimensions
  - 1536 dims matches VECTOR(1536) column in memories table
  - Confirmed active on OpenRouter as of April 2026
  - ~100-200ms typical latency via OpenRouter

Design decisions:
  - Returns [] when no LLM key configured → callers fall back to recency order
  - 5s timeout via asyncio.wait_for → never blocks the pipeline
  - Input truncated at 8000 chars → fits within 8192 token context window
  - No retry — embedding calls are best-effort; missed embeddings leave
    null column values (legacy rows fall back to recency retrieval)
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBEDDING_DIMS = 1536
EMBEDDING_TIMEOUT_S = 5.0
MAX_INPUT_CHARS = 8000


async def embed_text(text: str) -> list[float]:
    """
    Embed text via OpenRouter. Returns 1536-dim vector or [] on failure/no key.

    Callers MUST handle the [] case — it means semantic search is unavailable
    and retrieval should fall back to recency ordering.
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.has_llm:
        logger.debug("embed_text=skip reason=no_key")
        return []

    truncated = text[:MAX_INPUT_CHARS]
    from app.tasks._llm import _client

    try:
        resp = await asyncio.wait_for(
            _client(settings).embeddings.create(
                model=EMBEDDING_MODEL,
                input=truncated,
            ),
            timeout=EMBEDDING_TIMEOUT_S,
        )
        vector = resp.data[0].embedding
        logger.debug("embed_text=ok dims=%d", len(vector))
        return vector

    except asyncio.TimeoutError:
        logger.warning("embed_text=timeout input_len=%d", len(truncated))
        return []

    except Exception as exc:
        logger.warning("embed_text=error err=%s", exc)
        return []
