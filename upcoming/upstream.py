"""What we remember about each upstream between runs.

Deliberately separate from ``status.json``. That file is a published contract a consumer
reads to answer "is this feed current"; this one is bookkeeping — cache validators and the
last time we scraped a source's event pages. Mixing them would mean either putting HTTP
plumbing into a consumer-facing document or putting a consumer contract somewhere it can be
changed without thought.

It is published rather than cached, for the same reason the feeds are: a GitHub Actions
cache expires after a week of disuse, and losing this file silently would restore the
behaviour it exists to prevent — a full refetch and a full rescrape on every run — with
nothing reporting the regression. Nothing here is secret; ETags are public response headers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .clock import parse as parse_instant
from .clock import stamp
from .fetch import FetchOutcome, Validators
from .registry import SourceConfig

#: Where the document lives inside the published tree.
UPSTREAM_PATH = "state/upstream.json"


@dataclass(frozen=True)
class SourceState:
    """One source's memory: how to ask "changed?", and when we last read its pages."""

    etag: str = ""
    last_modified: str = ""
    last_enriched: str = ""

    @property
    def validators(self) -> Validators:
        return Validators(etag=self.etag, last_modified=self.last_modified)


def read(root: Path) -> dict[str, SourceState]:
    """The remembered state, or empty if this is a first run or the file is unreadable.

    Unreadable is treated as absent rather than fatal: the cost is one wasteful run, and
    refusing to publish over a corrupt bookkeeping file would turn a cache miss into an
    outage.
    """
    path = root / UPSTREAM_PATH
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    sources = document.get("sources") if isinstance(document, dict) else None
    if not isinstance(sources, dict):
        return {}
    return {
        str(slug): SourceState(
            etag=str(entry.get("etag", "")),
            last_modified=str(entry.get("lastModified", "")),
            last_enriched=str(entry.get("lastEnrichedAt", "")),
        )
        for slug, entry in sources.items()
        if isinstance(entry, dict)
    }


def document(state: Mapping[str, SourceState], *, generated_at: str) -> str:
    """Serialize the state, sorted so the file is byte-stable run to run."""
    payload = {
        "note": (
            "Internal bookkeeping, not a published contract. Cache validators for "
            "conditional requests, and when each source's event pages were last scraped. "
            "See status.json for feed health."
        ),
        "generatedAt": generated_at,
        "sources": {
            slug: {
                **({"etag": entry.etag} if entry.etag else {}),
                **({"lastModified": entry.last_modified} if entry.last_modified else {}),
                **({"lastEnrichedAt": entry.last_enriched} if entry.last_enriched else {}),
            }
            for slug, entry in sorted(state.items())
            if entry.etag or entry.last_modified or entry.last_enriched
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def due_for_enrichment(
    source: SourceConfig, state: Mapping[str, SourceState], *, now: datetime
) -> tuple[bool, str]:
    """Should this run re-scrape this source's event pages?

    ``rebuild_after_hours`` has been in ``config/sources.yaml`` since the first commit --
    24 by default, overridden to 6 for exactly the two sources with a 30-minute cadence --
    and nothing read it. This is the field finally doing its job.

    The saving is the whole point. Every event page was being refetched on every run:
    measured against live data, about 4,100 requests a day to departmental web servers to
    be handed back markup that had not changed. Honouring a 6-hour window on ORFE turns 960
    requests a day into roughly 80.

    The cost is equally plain, and belongs in config rather than in code: a title edited on
    an event page takes up to ``rebuild_after_hours`` to appear.
    """
    if not source.enrich:
        return False, "declares no enrichment targets"
    entry = state.get(source.slug)
    last = parse_instant(entry.last_enriched) if entry and entry.last_enriched else None
    if last is None:
        return True, "never scraped"
    elapsed = (now - last).total_seconds() / 3600
    if elapsed < 0:
        # A stamp in the future would otherwise park this source's enrichment forever.
        return True, "last scrape is dated in the future"
    if elapsed < source.rebuild_after_hours:
        return False, f"scraped {elapsed:.1f}h ago, window is {source.rebuild_after_hours}h"
    return True, f"scraped {elapsed:.1f}h ago, window is {source.rebuild_after_hours}h"


def advance(
    previous: Mapping[str, SourceState],
    slug: str,
    *,
    outcome: FetchOutcome | None = None,
    enriched_at: str | None = None,
) -> SourceState:
    """The new state for one source, carrying forward whatever this run did not learn.

    A 304 carries no body but does carry validators, and a run that skipped enrichment
    must keep the old scrape stamp rather than resetting the window -- otherwise a window
    would never elapse and the pages would never be re-read.
    """
    held = previous.get(slug, SourceState())
    etag, modified = held.etag, held.last_modified
    if outcome is not None and (outcome.ok or outcome.unchanged):
        # Only overwrite with something: a 200 from a server that sends no ETag must not
        # erase a Last-Modified we still hold.
        etag = outcome.etag or etag
        modified = outcome.last_modified or modified
    return SourceState(
        etag=etag,
        last_modified=modified,
        last_enriched=held.last_enriched if enriched_at is None else enriched_at,
    )


def stamp_now(moment: datetime) -> str:
    """A scrape stamp, in the one format everything published uses."""
    return stamp(moment)


__all__ = [
    "UPSTREAM_PATH",
    "SourceState",
    "advance",
    "document",
    "due_for_enrichment",
    "read",
    "stamp_now",
]
