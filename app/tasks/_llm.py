"""
LLM helper — backed by OpenRouter (openrouter.ai).

OpenRouter is an OpenAI-compatible gateway that routes to any model:
  openai/gpt-4o-mini, anthropic/claude-3.5-sonnet, google/gemini-flash-1.5,
  meta-llama/llama-3.3-70b-instruct:free, mistralai/mistral-nemo, …

Switch models with no code changes — just update LLM_MODEL_FAST /
LLM_MODEL_CAPABLE in your .env.

Log format (every call emits one INFO line):
  LLM call=json  model=...  latency_ms=NNN  tokens=N  response={...}
  LLM call=text  model=...  latency_ms=NNN  tokens=N  response="..."
  LLM call=mock  reason=no_key
  LLM call=timeout  latency_ms=NNN
  LLM call=error  latency_ms=NNN  error=...
"""
import asyncio
import json
import logging
import re
import time

from app.config import get_settings

logger = logging.getLogger(__name__)


def _client(settings=None):
    """Return an AsyncOpenAI client pointed at OpenRouter."""
    from openai import AsyncOpenAI
    if settings is None:
        settings = get_settings()

    # OpenRouter asks for these headers for dashboard attribution (optional)
    default_headers = {}
    if settings.openrouter_site_url:
        default_headers["HTTP-Referer"] = settings.openrouter_site_url
    if settings.openrouter_site_name:
        default_headers["X-Title"] = settings.openrouter_site_name

    return AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_headers=default_headers,
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
    Call the fast model expecting a JSON response.
    Falls back to mock_payload on no key / timeout / error.
    """
    settings = get_settings()

    if not settings.has_llm:
        logger.info("LLM call=mock reason=no_key")
        return mock_payload

    t0 = time.monotonic()
    try:
        resp = await asyncio.wait_for(
            _client(settings).chat.completions.create(
                model=settings.llm_model_fast,
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
            settings.llm_model_fast, latency_ms, tokens, json.dumps(result)[:120],
        )
        return result

    except asyncio.TimeoutError:
        logger.warning("LLM call=timeout  latency_ms=%d  falling back to mock",
                       int((time.monotonic() - t0) * 1000))
        return mock_payload

    except json.JSONDecodeError:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("LLM call=json_parse_error  latency_ms=%d  attempting extraction", latency_ms)
        try:
            # Some models wrap JSON in markdown fences despite response_format
            result = _extract_json_from_text(resp.choices[0].message.content)
            logger.info("LLM json_extracted=%s", json.dumps(result)[:120])
            return result
        except (ValueError, UnboundLocalError) as exc:
            logger.error("LLM json_extraction_failed: %s", exc)
            return mock_payload

    except Exception as exc:
        logger.error("LLM call=error  latency_ms=%d  error=%s",
                     int((time.monotonic() - t0) * 1000), exc)
        return mock_payload


async def llm_messages_json(
    messages: list[dict],
    mock_payload: dict,
    timeout_s: float = 10.0,
) -> dict:
    """
    Call the fast model with a pre-built messages array, expecting a JSON response.

    Unlike llm_json (which wraps a single prompt string into a role:user message),
    this helper accepts a pre-built messages list so callers can separate system
    instructions (role:system) from user content (role:user).

    Falls back to mock_payload on no key / timeout / error.
    """
    settings = get_settings()

    if not settings.has_llm:
        logger.info("LLM call=mock reason=no_key")
        return mock_payload

    t0 = time.monotonic()
    try:
        resp = await asyncio.wait_for(
            _client(settings).chat.completions.create(
                model=settings.llm_model_fast,
                messages=messages,
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
            "LLM call=json_messages  model=%s  latency_ms=%d  tokens=%s  response=%s",
            settings.llm_model_fast, latency_ms, tokens, json.dumps(result)[:120],
        )
        return result

    except asyncio.TimeoutError:
        logger.warning("LLM call=timeout  latency_ms=%d  falling back to mock",
                       int((time.monotonic() - t0) * 1000))
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
        logger.error("LLM call=error  latency_ms=%d  error=%s",
                     int((time.monotonic() - t0) * 1000), exc)
        return mock_payload


async def llm_text(
    system: str,
    messages: list[dict],
    mock_text: str,
    timeout_s: float = 10.0,
) -> str:
    """
    Call the capable model for a free-form text response.
    Falls back to mock_text on no key / timeout / error.
    """
    settings = get_settings()

    if not settings.has_llm:
        logger.info("LLM call=mock reason=no_key")
        return mock_text

    t0 = time.monotonic()
    try:
        resp = await asyncio.wait_for(
            _client(settings).chat.completions.create(
                model=settings.llm_model_capable,
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
            settings.llm_model_capable, latency_ms, tokens, result[:100],
        )
        return result

    except asyncio.TimeoutError:
        logger.warning("LLM call=timeout  latency_ms=%d  falling back to mock",
                       int((time.monotonic() - t0) * 1000))
        return mock_text

    except Exception as exc:
        logger.error("LLM call=error  latency_ms=%d  error=%s",
                     int((time.monotonic() - t0) * 1000), exc)
        return mock_text
