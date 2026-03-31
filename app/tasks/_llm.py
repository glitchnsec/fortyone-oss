"""
LLM helper — backed by NVIDIA NIM (OpenAI-compatible API).

NVIDIA NIM base URL: https://integrate.api.nvidia.com/v1
The openai Python SDK works unchanged; we just pass base_url + api_key.

Two helpers:
  llm_json  — structured extraction, returns a dict
  llm_text  — free-form response, returns a string

Both fall back to mock values when NVIDIA_API_KEY is not set so the full
pipeline runs locally without any credentials.

JSON extraction note:
  meta/llama-3.1-8b-instruct supports response_format=json_object natively.
  If you switch to a model that does not, set NIM_JSON_EXTRACT_FALLBACK=true
  and we will parse JSON out of the markdown code-block the model returns.
"""
import json
import logging
import re

from app.config import get_settings

logger = logging.getLogger(__name__)


def _nim_client(settings=None):
    """Return an AsyncOpenAI client pointed at NVIDIA NIM."""
    from openai import AsyncOpenAI
    if settings is None:
        settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nim_base_url,
    )


def _extract_json_from_text(text: str) -> dict:
    """
    Fallback: pull JSON out of a model response that wrapped it in a
    markdown code block (```json … ```) or returned it inline.
    """
    # Try bare parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences
    match = re.search(r"```(?:json)?\s*(\{.*?})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Last resort: find the first { … } block
    match = re.search(r"(\{.*})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from response: {text[:200]}")


async def llm_json(prompt: str, mock_payload: dict) -> dict:
    """
    Call NIM with a JSON-extraction prompt.
    Returns mock_payload when NVIDIA_API_KEY is not set.
    """
    settings = get_settings()

    if not settings.has_llm:
        logger.debug("NIM mock active — returning mock payload")
        return mock_payload

    client = _nim_client(settings)

    try:
        resp = await client.chat.completions.create(
            model=settings.nim_model_fast,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content
        return json.loads(raw)
    except json.JSONDecodeError:
        # Model returned text instead of strict JSON — try to extract it
        logger.warning("NIM JSON parse failed, attempting text extraction")
        try:
            return _extract_json_from_text(resp.choices[0].message.content)
        except (ValueError, UnboundLocalError) as exc:
            logger.error("NIM JSON extraction failed: %s", exc)
            return mock_payload
    except Exception as exc:
        logger.error("NIM llm_json call failed: %s", exc)
        return mock_payload


async def llm_text(system: str, messages: list[dict], mock_text: str) -> str:
    """
    Call NIM for a free-form text response.
    Uses the more capable model for better conversational quality.
    Returns mock_text when NVIDIA_API_KEY is not set.
    """
    settings = get_settings()

    if not settings.has_llm:
        return mock_text

    client = _nim_client(settings)

    try:
        resp = await client.chat.completions.create(
            model=settings.nim_model_capable,
            messages=[{"role": "system", "content": system}] + messages,
            temperature=0.7,
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("NIM llm_text call failed: %s", exc)
        return mock_text
