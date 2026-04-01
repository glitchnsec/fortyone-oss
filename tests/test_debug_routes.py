"""
Tests for debug route gating behind environment check.

Verifies that debug endpoints are only registered in development
and are absent (404) in production/staging environments.
"""
import importlib
import pytest
from unittest.mock import patch, MagicMock


def _make_settings(**overrides):
    """Create a minimal mock Settings object."""
    defaults = dict(
        environment="production",
        redis_url="redis://localhost:6379",
        queue_name="jobs",
        response_channel="job_completed",
        slack_bot_token="",
        slack_signing_secret="",
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def test_debug_routes_not_registered_in_production():
    """Debug routes must NOT be present in non-development environments."""
    with patch("app.config.get_settings") as mock_get_settings:
        mock_get_settings.return_value = _make_settings(environment="production")
        import app.main as main_module
        importlib.reload(main_module)
        routes = [r.path for r in main_module.app.routes]
        debug_routes = [r for r in routes if "/debug/" in r or r == "/debug"]
        assert len(debug_routes) == 0, f"Debug routes found in production: {debug_routes}"


def test_debug_routes_not_registered_in_staging():
    """Debug routes must NOT be present in staging."""
    with patch("app.config.get_settings") as mock_get_settings:
        mock_get_settings.return_value = _make_settings(environment="staging")
        import app.main as main_module
        importlib.reload(main_module)
        routes = [r.path for r in main_module.app.routes]
        debug_routes = [r for r in routes if "/debug/" in r or r == "/debug"]
        assert len(debug_routes) == 0, f"Debug routes found in staging: {debug_routes}"


def test_debug_routes_registered_in_development():
    """Debug routes MUST be present when ENVIRONMENT=development."""
    with patch("app.config.get_settings") as mock_get_settings:
        mock_get_settings.return_value = _make_settings(environment="development")
        import app.main as main_module
        importlib.reload(main_module)
        routes = [r.path for r in main_module.app.routes]
        debug_routes = [r for r in routes if "/debug/" in r]
        assert len(debug_routes) > 0, "Debug routes should be registered in development"


def test_debug_router_exports_router():
    """app/routes/debug.py must export a router object."""
    from app.routes.debug import router
    from fastapi import APIRouter
    assert isinstance(router, APIRouter), "debug.router must be an APIRouter instance"
