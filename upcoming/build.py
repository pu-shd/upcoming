"""Building one source's feed.

This module owns the **only** place in the package that catches a bare ``Exception``. One
source's failure must be attributable to that source and must not take the other fifteen
down with it, but everywhere else a swallowed exception is how a total failure comes to
look like a clean run.

The pipeline is one function, not two. The predecessor has a CLI path and a library path
that must be kept in step by hand -- its own contributor guide asks humans to do this --
and they have diverged twice: one ignored the transform config entirely for every library
caller, and after that was fixed it still performs no enrichment, so a library caller gets
a feed with no scraped titles or speakers. Both failures were schema-valid and silent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import fallback
from .errors import SourceFatal
from .model import Event
from .parse import parse_ics, prodid
from .registry import SourceConfig
from .serialize import dump_feed, wire_format_for
from .transform import transform

DEFAULT_PRONUNCIATION = "config/pronunciation.yaml"


@dataclass(frozen=True)
class BuildResult:
    """What one source produced, and everything worth reporting about how."""

    source: str
    status: str
    events: tuple[Event, ...] = ()
    diagnostics: tuple[str, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def load_pronunciation(path: str | Path = DEFAULT_PRONUNCIATION) -> None:
    """Install the article vocabulary, if a config file is present.

    Absent is fine: the module carries a working default, and a source whose acronyms are
    all spelled out needs nothing here.
    """
    config = Path(path)
    if not config.is_file():
        return
    data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    fallback.set_word_acronyms(data.get("word_acronyms") or ())


def build_events(text: str, source: SourceConfig) -> tuple[Event, ...]:
    """ICS text to finished events for one source.

    Deliberately pure: text in, events out, no I/O. That is what lets the differential
    tests run the real pipeline against committed fixtures with the network blocked.
    """
    found = prodid(text)
    if source.platform == "princeton-site-builder" and found and found != source.platform:
        raise SourceFatal(
            source.slug,
            f"feed reports PRODID {found!r} but the source declares platform "
            f"{source.platform!r}. A platform swap under a stable URL is exactly "
            f"kellercenter's situation, and it changes what every field means.",
        )

    raw_events = parse_ics(text)
    events = transform(raw_events, source)
    events, _ = fill_titles_for(events, source)
    return events


def fill_titles_for(events: Sequence[Event], source: SourceConfig) -> tuple[tuple[Event, ...], int]:
    """Synthesize any missing title, per the source's declared template."""
    return fallback.fill_titles(
        events,
        source.title_template,
        include_speaker=source.fallback_include_speaker,
    )


def render(events: Sequence[Event], source: SourceConfig) -> str:
    """The published bytes for one source."""
    return dump_feed(events, wire_format_for(source.escape_fields))


def build_from_text(text: str, source: SourceConfig) -> BuildResult:
    """Build one source, attributing any failure to it rather than raising onward.

    The single bare-``Exception`` boundary. A source that fails leaves the others alone,
    and the reason travels with the result instead of being printed and lost.
    """
    try:
        events = build_events(text, source)
    except SourceFatal as exc:
        return BuildResult(source=source.slug, status="failed", diagnostics=(exc.message,))
    except Exception as exc:
        return BuildResult(
            source=source.slug,
            status="failed",
            diagnostics=(f"unexpected {type(exc).__name__}: {exc}",),
        )

    placeholders = sum(1 for e in events if e.title_is_placeholder)
    declined = sum(1 for e in events if e.location.is_empty)
    return BuildResult(
        source=source.slug,
        status="ok",
        events=events,
        counts={
            "events": len(events),
            "placeholder_titles": placeholders,
            "locations_declined": declined,
        },
    )


def build_from_file(path: str | Path, source: SourceConfig) -> BuildResult:
    """Build one source from an ICS file on disk."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return BuildResult(
            source=source.slug, status="failed", diagnostics=(f"cannot read {path}: {exc}",)
        )
    return build_from_text(text, source)


__all__: list[str] = [
    "BuildResult",
    "build_events",
    "build_from_file",
    "build_from_text",
    "load_pronunciation",
    "render",
]
