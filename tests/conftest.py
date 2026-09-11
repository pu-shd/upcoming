"""Test fixtures and the two global guards.

Both guards exist because of specific failures in the predecessor repos.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator

import pytest

from tests.support import REPO_ROOT, TEST_ENV

#: Every variable that can steer the pipeline. A value exported in a developer's shell
#: would otherwise leak into assertions and make a failure look like a code regression --
#: the predecessor hit this and had to scrub nine names by hand.
_ISOLATED_PREFIXES = ("UPCOMING_",)
_ISOLATED_NAMES = ("BOT_BYPASS_TOKEN",)


class NetworkAccessInTests(RuntimeError):
    """Raised when a test tries to reach the network."""


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear pipeline-steering variables before every test."""
    for name in list(os.environ):
        if name.startswith(_ISOLATED_PREFIXES) or name in _ISOLATED_NAMES:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[None]:
    """Make any real network call raise, unless the test is marked ``live``.

    The predecessor patches its fetch helper per-test, which only protects the tests that
    remember to. Blocking at the socket layer means a new test that accidentally reaches
    out fails loudly instead of being slow, flaky, and dependent on someone's VPN.
    """
    if request.node.get_closest_marker("live"):
        yield
        return

    def blocked(*args: object, **kwargs: object) -> None:
        raise NetworkAccessInTests(
            "this test tried to reach the network; use a fixture or mark it @pytest.mark.live"
        )

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    yield


@pytest.fixture
def registry():  # type: ignore[no-untyped-def]
    """The real registry, loaded with a test credential."""
    from upcoming.registry import load_registry

    return load_registry(REPO_ROOT / "config" / "sources.yaml", env=TEST_ENV)
