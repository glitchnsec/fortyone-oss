"""
Lightweight LLM helper shared across task handlers.

If OPENAI_API_KEY is not set the system falls back to mock responses so the
full pipeline works locally without any credentials.
"""
import json
import logging
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


async def llm_json(prompt: str, mock_payload: dict) -> dict:
    """Call the LLM and parse JSON.  Returns mock_payload when no key is set."""
    settings = get_settings()

    if not settings.has_llm:
        logger.debug("LLM mock active — returning mock payload")
        return mock_payload

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=300,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        logger.error("LLM JSON call failed: %s", exc)
        return mock_payload


async def llm_text(system: str, messages: list[dict], mock_text: str) -> str:
    """Call the LLM for a free-form text response."""
    settings = get_settings()

    if not settings.has_llm:
        return mock_text

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "system", "content": system}] + messages,
            temperature=0.7,
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("LLM text call failed: %s", exc)
        return mock_text
