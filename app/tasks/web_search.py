"""Web search task handler — uses Brave Search API.

No OAuth required. Available to all users from day one.
Rate limit: 1 req/sec on free tier; handled by httpx timeout + graceful fallback.
"""
import logging
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


async def handle_web_search(payload: dict) -> dict:
    """Search the web via Brave Search API and return formatted results."""
    job_id = payload.get("job_id", "")
    phone = payload.get("phone", "")
    address = payload.get("address", phone)
    channel = payload.get("channel", "sms")
    p_user_id = payload.get("user_id", "")
    message = payload.get("body", "") or payload.get("message", "")

    # Common identity fields included in every return dict so the
    # ResponseListener can route delivery and attribute the user correctly.
    _identity = {
        "job_id": job_id,
        "phone": phone,
        "address": address,
        "channel": channel,
        "user_id": p_user_id,
    }

    s = get_settings()

    if not s.brave_api_key:
        logger.warning("BRAVE_API_KEY not set — returning mock search result")
        return {
            **_identity,
            "response": "Web search is not available right now.",
            "degraded": True,
            "admin_reason": "BRAVE_API_KEY not set — configure it to enable web search",
            "user_reason": "Web search isn't available right now",
        }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                BRAVE_SEARCH_URL,
                params={"q": message, "count": 5},
                headers={
                    "X-Subscription-Token": s.brave_api_key,
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("web", {}).get("results", [])
        if not results:
            return {
                **_identity,
                "response": f"I searched for '{message}' but found no results.",
            }

        # Format top 3 results
        lines = [f"Here's what I found for '{message}':\n"]
        for i, r in enumerate(results[:3], 1):
            title = r.get("title", "")
            description = r.get("description", "")
            url = r.get("url", "")
            lines.append(f"{i}. {title}\n   {description}\n   {url}")
        response_text = "\n".join(lines)
        return {**_identity, "response": response_text}

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.warning("Brave Search rate limit hit")
            return {
                **_identity,
                "response": "I hit a search rate limit. Try again in a moment.",
                "degraded": True,
                "user_reason": "Search rate limit reached — try again in a moment",
            }
        logger.error("Brave Search API error status=%s", e.response.status_code, exc_info=True)
        return {
            **_identity,
            "response": "I ran into an issue with web search. Try again shortly.",
            "degraded": True,
            "user_reason": "Web search encountered an error",
        }
    except Exception as e:
        logger.error("web_search handler error: %s", e, exc_info=True)
        return {
            **_identity,
            "response": "Web search failed. I'll try again if you ask.",
            "degraded": True,
            "user_reason": "Web search failed unexpectedly",
        }
