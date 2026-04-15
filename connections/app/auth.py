"""Service-to-service authentication for the connections service.

All routes (except /health) require a valid X-Service-Token header.
The token is a shared secret between the API/worker and connections service,
configured via the SERVICE_AUTH_TOKEN environment variable.
"""
from fastapi import Header, HTTPException
from app.config import get_settings


async def verify_service_token(
    x_service_token: str = Header(...),
) -> None:
    """Validate the X-Service-Token header against the configured secret.

    Raises 503 if SERVICE_AUTH_TOKEN is not configured (misconfigured deployment).
    Raises 401 if the token does not match.
    """
    settings = get_settings()
    if not settings.service_auth_token:
        raise HTTPException(503, detail="SERVICE_AUTH_TOKEN not configured")
    if x_service_token != settings.service_auth_token:
        raise HTTPException(401, detail="Invalid service token")
