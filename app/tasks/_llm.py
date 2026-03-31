"""
LLM helper — backed by NVIDIA NIM (OpenAI-compatible API).

Every call is timed and logged at INFO level so you can see exactly what
went in, what came back, and how long it took.

Log format:
  LLM call=json  model=meta/llama-3.1-8b-instruct  latency_ms=312  tokens=87  response={"task":...}
  LLM call=text  model=meta/llama-3.3-70b-instruct  latency_ms=891  tokens=54  response=On it, Kc...
  LLM call=mock  reason=no_key
  LLM call=error latency_ms=21043  error=Connection timeout
"""
import asyncio
import json
import logging
import re
import time

from app.config import get_settings

logger = logging.getLogger(__name__)


def _nim_client(settings=None):
    from openai import AsyncOpenAI
    if settings is None:
        settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nim_base_url,
    )


def _extract_json_from_text(text: str) -> dict:
    """Pull JSON from a response that wrapped it in markdown fences."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"(\{.*})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON: {text[:200]}")


async def llm_json(
    prompt: str,
    mock_payload: dict,
    timeout_s: float = 10.0,
) -> dict:
    """
    Call NIM expecting a JSON response.
    Falls back to mock_payload when no key is set or on any error/timeout.
    """
    settings = get_settings()

    if not settings.has_llm:
        logger.info("LLM call=mock reason=no_key")
        return mock_payload

    client = _nim_client(settings)
    t0 = time.monotonic()

    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.nim_model_fast,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=400,
            ),
            timeout=timeout_s,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        raw = resp.choices[0].message.content
        tokens = getattr(resp.usage, "completion_tokens", "?")
        result = json.loads(raw)
        logger.info(
            "LLM call=json  model=%s  latency_ms=%d  tokens=%s  response=%s",
            settings.nim_model_fast, latency_ms, tokens,
            json.dumps(result)[:120],
        )
        return result

    except asyncio.TimeoutError:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("LLM call=timeout  latency_ms=%d  falling back to mock", latency_ms)
        return mock_payload

    except json.JSONDecodeError:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("LLM call=json_parse_error  latency_ms=%d  attempting extraction", latency_ms)
        try:
            result = _extract_json_from_text(resp.choices[0].message.content)
            logger.info("LLM json_extracted=%s", json.dumps(result)[:120])
            return result
        except (ValueError, UnboundLocalError) as exc:
            logger.error("LLM json_extraction_failed: %s", exc)
            return mock_payload

    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error("LLM call=error  latency_ms=%d  error=%s", latency_ms, exc)
        return mock_payload


async def llm_text(
    system: str,
    messages: list[dict],
    mock_text: str,
    timeout_s: float = 10.0,
) -> str:
    """
    Call NIM for a free-form text response.
    Falls back to mock_text when no key is set or on any error/timeout.
    """
    settings = get_settings()

    if not settings.has_llm:
        logger.info("LLM call=mock reason=no_key")
        return mock_text

    client = _nim_client(settings)
    t0 = time.monotonic()

    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.nim_model_capable,
                messages=[{"role": "system", "content": system}] + messages,
                temperature=0.7,
                max_tokens=200,
            ),
            timeout=timeout_s,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        result = resp.choices[0].message.content.strip()
        tokens = getattr(resp.usage, "completion_tokens", "?")
        logger.info(
            "LLM call=text  model=%s  latency_ms=%d  tokens=%s  response=%r",
            settings.nim_model_capable, latency_ms, tokens,
            result[:100],
        )
        return result

    except asyncio.TimeoutError:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("LLM call=timeout  latency_ms=%d  falling back to mock", latency_ms)
        return mock_text

    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error("LLM call=error  latency_ms=%d  error=%s", latency_ms, exc)
        return mock_text
