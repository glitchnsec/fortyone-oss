"""Worker startup test — verifies SQLAlchemy mapper resolution in worker's import chain.

The worker process (scripts/run_worker.py) has a minimal import path that doesn't
include auth routes. If User.sessions references 'UserSession' as a string,
UserSession must be imported before mappers configure.

This test caught the bug where the worker crashed with:
  'expression UserSession failed to locate a name'
because app/models/auth.py was never imported in the worker process.
"""
import pytest
import subprocess
import sys


def test_worker_module_imports_without_mapper_error():
    """Importing the worker's dependency chain must not crash on mapper config.

    Runs in a subprocess to simulate the worker's isolated import environment —
    pytest's main process has everything imported already and would mask the bug.
    """
    result = subprocess.run(
        [
            sys.executable, "-c",
            "from app.database import init_db; "
            "from app.memory.models import User; "
            "from sqlalchemy import inspect; "
            "inspect(User);  "  # Forces mapper configuration
            "print('OK')"
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            "PYTHONPATH": ".",
            "DATABASE_URL": "sqlite:///./test.db",
        },
    )
    assert result.returncode == 0, (
        f"Worker import chain failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout
