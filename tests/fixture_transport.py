"""A transport backed by committed page fixtures.

Injectable so the suite runs offline. The manifest maps a real event URL to the trimmed
capture of that page, so tests exercise the actual selectors against the actual markup.

It also models the failure the pipeline most needs to detect: any URL the manifest does not
name answers with whatever ``missing`` says, which defaults to a 403 -- the shape of a bot
challenge, and the case the predecessor reports as a clean run that found nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from tests.support import FIXTURES
from upcoming.fetch import FetchOutcome

PAGES = FIXTURES / "pages"


def load_manifest() -> dict[str, Path]:
    raw = json.loads((PAGES / "manifest.json").read_text(encoding="utf-8"))
    return {url: PAGES / relative for url, relative in raw.items()}


#: A page that exists and carries nothing enrichment wants. Not an error -- plenty of real
#: event pages have no abstract and no speaker field, and a selector finding nothing there
#: is an ordinary outcome that must stay distinguishable from a blocked request.
EMPTY_PAGE = "<!doctype html><html><head><title>Event</title></head><body></body></html>"


@dataclass
class FixtureTransport:
    """Answers from disk; anything unmapped answers with ``missing``.

    The captured pages cover the markup shapes that matter. An unmapped URL therefore
    models "a page with nothing relevant on it" rather than a failure -- which is the
    default, because treating it as an error would make every success-path test fight the
    enrichment health gate for no reason. ``BlockedTransport`` is how a test asks for the
    failure instead.
    """

    pages: Mapping[str, Path] = field(default_factory=load_manifest)
    #: What an unmapped URL returns: ``ok`` (an empty page), ``http_error`` or
    #: ``network_error``.
    missing: str = "ok"
    missing_code: int = 403
    #: Every URL asked for, in order, so a test can prove the cache works.
    calls: list[str] = field(default_factory=list)

    def __call__(
        self, url: str, *, headers: Mapping[str, str], timeout: tuple[float, float]
    ) -> FetchOutcome:
        self.calls.append(url)
        path = self.pages.get(url)
        if path is not None:
            return FetchOutcome("ok", url, code=200, body=path.read_text(encoding="utf-8"))
        if self.missing == "network_error":
            return FetchOutcome("network_error", url, error="fixture: no such page")
        if self.missing == "http_error":
            return FetchOutcome(
                "http_error", url, code=self.missing_code, error=f"HTTP {self.missing_code}"
            )
        return FetchOutcome("ok", url, code=200, body=EMPTY_PAGE)


@dataclass
class BlockedTransport:
    """Every request 403s, as a host does when the bypass credential is wrong."""

    calls: list[str] = field(default_factory=list)

    def __call__(
        self, url: str, *, headers: Mapping[str, str], timeout: tuple[float, float]
    ) -> FetchOutcome:
        self.calls.append(url)
        return FetchOutcome("http_error", url, code=403, error="HTTP 403")
