"""Shared test constants.

Separate from conftest so test modules can import it plainly; pytest treats conftest as
plugin machinery rather than a library.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: A working credential value for tests. Never a real token: these tests assert that
#: config *loads*, not that anything authenticates.
TEST_ENV = {"BOT_BYPASS_TOKEN": "test-token"}
