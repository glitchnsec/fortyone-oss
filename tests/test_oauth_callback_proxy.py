"""Regression tests for browser-facing OAuth callback proxy.

Phase 11 made the connections service internal-only and service-token protected.
OAuth providers must therefore redirect to the public API, which proxies the
callback to the internal connections service.
"""
import httpx
import pytest
from fastapi import FastAPI

from app.routes import dashboard as dashboard_routes
from app.routes.dashboard import _connections_client, public_router


@pytest.mark.asyncio
async def test_public_oauth_callback_proxies_to_connections_and_redirects(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = str(request.url.query, "utf-8")
        return httpx.Response(
            307,
            headers={"location": "http://dashboard.local/connections?connected=google&persona_id=p1"},
        )

    async def fake_connections_client():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://connections:8001",
        ) as client:
            yield client

    class Settings:
        dashboard_url = "http://dashboard.local"

    monkeypatch.setattr(dashboard_routes, "get_settings", lambda: Settings())

    app = FastAPI()
    app.dependency_overrides[_connections_client] = fake_connections_client
    app.include_router(public_router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        resp = await client.get("/oauth/callback/google?code=abc&state=xyz")

    assert resp.status_code == 307
    assert resp.headers["location"] == "http://dashboard.local/connections?connected=google&persona_id=p1"
    assert seen["path"] == "/oauth/callback/google"
    assert "code=abc" in seen["query"]
    assert "state=xyz" in seen["query"]


@pytest.mark.asyncio
async def test_public_oauth_callback_turns_connections_error_into_dashboard_redirect(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "Invalid OAuth state"})

    async def fake_connections_client():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://connections:8001",
        ) as client:
            yield client

    class Settings:
        dashboard_url = "http://dashboard.local"

    monkeypatch.setattr(dashboard_routes, "get_settings", lambda: Settings())

    app = FastAPI()
    app.dependency_overrides[_connections_client] = fake_connections_client
    app.include_router(public_router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        resp = await client.get("/oauth/callback/google?code=abc&state=bad")

    assert resp.status_code == 307
    assert resp.headers["location"] == "http://dashboard.local/connections?error=invalid_oauth_state"
