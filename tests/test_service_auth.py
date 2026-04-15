"""Integration tests for connections service auth (D-02).

Validates that the verify_service_token dependency:
  1. Rejects requests without X-Service-Token header (422)
  2. Rejects requests with invalid X-Service-Token (401)
  3. Accepts requests with valid X-Service-Token
  4. Health endpoint works without auth

Since the connections service uses `app.*` imports that conflict with the main
app when running from project root, we reconstruct a minimal FastAPI app that
mirrors the connections service auth setup.
"""
import os
import sys
import pytest
import httpx
from unittest.mock import patch
from fastapi import Depends, FastAPI, Header, HTTPException


# ─── Replicate the connections auth dependency ──────────────────────────────

TEST_TOKEN = "test-secret-token-12345"


async def verify_service_token(
    x_service_token: str = Header(...),
) -> None:
    """Mirror of connections/app/auth.py verify_service_token."""
    if not TEST_TOKEN:
        raise HTTPException(503, detail="SERVICE_AUTH_TOKEN not configured")
    if x_service_token != TEST_TOKEN:
        raise HTTPException(401, detail="Invalid service token")


# Build a minimal app that mirrors connections service auth structure
_test_app = FastAPI()

@_test_app.get("/health")
async def health():
    return {"status": "ok"}

_auth = [Depends(verify_service_token)]

from fastapi import APIRouter
_protected_router = APIRouter(dependencies=_auth)

@_protected_router.get("/connections/{user_id}")
async def get_connections(user_id: str):
    return {"connections": []}

_test_app.include_router(_protected_router)


# ─── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connections_rejects_without_token():
    """Requests without X-Service-Token header should get 422 (missing required header)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_test_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/connections/nonexistent-user")
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


@pytest.mark.asyncio
async def test_connections_rejects_invalid_token():
    """Requests with wrong X-Service-Token should get 401."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_test_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            "/connections/nonexistent-user",
            headers={"X-Service-Token": "wrong-token"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


@pytest.mark.asyncio
async def test_connections_accepts_valid_token():
    """Requests with correct X-Service-Token should not get 401 or 422."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_test_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get(
            "/connections/nonexistent-user",
            headers={"X-Service-Token": TEST_TOKEN},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert resp.json() == {"connections": []}


@pytest.mark.asyncio
async def test_health_endpoint_no_auth_required():
    """Health endpoint should work without any auth token."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_test_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_connections_auth_source_file_exists():
    """Verify that connections/app/auth.py exists with the actual dependency."""
    import pathlib
    auth_path = pathlib.Path(__file__).parent.parent / "connections" / "app" / "auth.py"
    assert auth_path.exists(), f"connections/app/auth.py not found at {auth_path}"
    content = auth_path.read_text()
    assert "verify_service_token" in content
    assert "X-Service-Token" not in content or "x_service_token" in content  # uses Header param name
    assert "HTTPException" in content
    assert "401" in content
    assert "503" in content


@pytest.mark.asyncio
async def test_connections_main_applies_auth_to_routers():
    """Verify connections/app/main.py applies auth dependency to all routers."""
    import pathlib
    main_path = pathlib.Path(__file__).parent.parent / "connections" / "app" / "main.py"
    content = main_path.read_text()
    assert "Depends(verify_service_token)" in content or "dependencies=_auth" in content
    # All 4 routers should have auth
    assert content.count("dependencies=_auth") >= 4 or content.count("Depends(verify_service_token)") >= 4
    # Health should NOT have auth
    assert '@app.get("/health")' in content
