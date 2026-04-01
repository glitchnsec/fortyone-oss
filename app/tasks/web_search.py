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
    message = payload.get("message", "")

    s = get_settings()

    if not s.brave_api_key:
        logger.warning("BRAVE_API_KEY not set — returning mock search result")
        return {
            "job_id": job_id,
            "phone": phone,
            "response": f"I searched for '{message}' but web search isn't configured yet. Set BRAVE_API_KEY to enable it.",
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
                "job_id": job_id,
                "phone": phone,
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
        return {"job_id": job_id, "phone": phone, "response": response_text}

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.warning("Brave Search rate limit hit")
            return {
                "job_id": job_id,
                "phone": phone,
                "response": "I hit a search rate limit. Try again in a moment.",
            }
        logger.error("Brave Search API error status=%s", e.response.status_code, exc_info=True)
        return {
            "job_id": job_id,
            "phone": phone,
            "response": "I ran into an issue with web search. Try again shortly.",
        }
    except Exception as e:
        logger.error("web_search handler error: %s", e, exc_info=True)
        return {
            "job_id": job_id,
            "phone": phone,
            "response": "Web search failed. I'll try again if you ask.",
        }
