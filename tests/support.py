"""Shared test constants.

Separate from conftest so test modules can import it plainly; pytest treats conftest as
plugin machinery rather than a library.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: A working credential for tests. The secret's body is a whole header line, "Name: value",
#: so the header name lives in the secret rather than in this repository. Never a real
#: value: these tests assert that config *loads*, not that anything authenticates.
TEST_HEADER_SECRET = "x-test-bypass: test-value"
TEST_ENV = {"BOT_BYPASS_HEADER": TEST_HEADER_SECRET}
