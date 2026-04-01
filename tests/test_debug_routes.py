"""
Tests for debug route gating behind environment check.

Verifies that debug endpoints are only registered in development
and are absent (404) in production/staging environments.
"""
import importlib
import os
import sys
import pytest
from unittest.mock import patch, MagicMock


def _reload_main_with_env(environment: str):
    """
    Reload app.main with a patched environment setting.

    Since app.main calls get_settings() at module level (and get_settings uses
    @lru_cache), we patch the already-imported settings object's environment
    attribute and force a reload to re-evaluate the conditional registration blocks.
    """
    # Clear lru_cache so settings reloads with fresh values
    from app.config import get_settings
    get_settings.cache_clear()

    # Patch environment variable for reload
    with patch.dict(os.environ, {"ENVIRONMENT": environment, "SLACK_SIGNING_SECRET": ""}, clear=False):
        # Remove cached module to force full reload
        for mod_name in list(sys.modules.keys()):
            if mod_name in ("app.main", "app.config"):
                del sys.modules[mod_name]

        import app.main as main_module
        return main_module


def test_debug_routes_not_registered_in_production():
    """Debug routes must NOT be present in non-development environments."""
    main_module = _reload_main_with_env("production")
    routes = [r.path for r in main_module.app.routes]
    debug_routes = [r for r in routes if "/debug/" in r or r == "/debug"]
    assert len(debug_routes) == 0, f"Debug routes found in production: {debug_routes}"


def test_debug_routes_not_registered_in_staging():
    """Debug routes must NOT be present in staging."""
    main_module = _reload_main_with_env("staging")
    routes = [r.path for r in main_module.app.routes]
    debug_routes = [r for r in routes if "/debug/" in r or r == "/debug"]
    assert len(debug_routes) == 0, f"Debug routes found in staging: {debug_routes}"


def test_debug_routes_registered_in_development():
    """Debug routes MUST be present when ENVIRONMENT=development."""
    main_module = _reload_main_with_env("development")
    routes = [r.path for r in main_module.app.routes]
    debug_routes = [r for r in routes if "/debug/" in r]
    assert len(debug_routes) > 0, "Debug routes should be registered in development"


def test_debug_router_exports_router():
    """app/routes/debug.py must export a router object."""
    from app.routes.debug import router
    from fastapi import APIRouter
    assert isinstance(router, APIRouter), "debug.router must be an APIRouter instance"
