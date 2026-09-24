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

#: Every registry must declare the export layouts, so a synthetic one under test needs
#: them too. Prepended by the helpers below rather than written into thirty fixtures: the
#: requirement is real -- an undeclared layout is a switcher the page cannot populate --
#: but it is not what any of those tests are about.
TEMPLATES_YAML = """
templates:
  day-grouped:
    label: Engineering newsletter
    default: true
  inline-date:
    label: DAIS newsletter
"""
