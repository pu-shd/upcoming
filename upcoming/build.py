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

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import fallback
from .config import read_mapping
from .enrich import breaches, enrich
from .errors import SourceFatal
from .fetch import PageCache
from .model import Event
from .parse import parse_ics, prodid
from .predicate import matches
from .registry import SourceConfig
from .scrape import ScrapeStats
from .serialize import dump_feed, wire_format_for
from .transform import transform
from .validate import GateResult, failures, run_gates, schema_errors, warnings

DEFAULT_PRONUNCIATION = "config/pronunciation.yaml"


@dataclass(frozen=True)
class BuildResult:
    """What one source produced, and everything worth reporting about how."""

    source: str
    status: str
    events: tuple[Event, ...] = ()
    diagnostics: tuple[str, ...] = ()
    #: Non-fatal findings. A feed still publishes with these; they exist so a signal that
    #: is not yet a problem is visible rather than absent.
    notes: tuple[str, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    gates: tuple[GateResult, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def load_pronunciation(path: str | Path = DEFAULT_PRONUNCIATION) -> None:
    """Install the article vocabulary, if a config file is present.

    Absent is fine: the module carries a working default, and a source whose acronyms are
    all spelled out needs nothing here.
    """
    data = read_mapping(
        path, what="pronunciation vocabulary", allow={"word_acronyms"}, required=False
    )
    fallback.set_word_acronyms(data.get("word_acronyms") or ())


def build_events(
    text: str, source: SourceConfig, cache: PageCache | None = None
) -> tuple[Event, ...]:
    """ICS text to finished events for one source.

    The only I/O is enrichment, and only when a ``cache`` is supplied -- so the differential
    tests run the real pipeline against committed fixtures with nothing to stub.

    Stage order is fixed here rather than left to a caller. Enrichment runs **after**
    mapping and **before** the fallback, so a title scraped off the page wins over a
    synthesized one and the fallback only fills what is still missing. The predecessor
    leaves this ordering implicit across two entry points that have twice drifted apart.
    """
    events, _stats, _declined = build_events_with_stats(text, source, cache)
    return events


def check_platform(text: str, source: SourceConfig) -> None:
    """Refuse a feed that is not the platform its source declares.

    A platform swap under a stable URL is exactly kellercenter's situation -- a Drupal iCal
    feed served from a path that looks like every Site Builder one -- and it changes what
    every field means.
    """
    found = prodid(text)
    if source.platform == "princeton-site-builder" and found and found != source.platform:
        raise SourceFatal(
            source.slug,
            f"feed reports PRODID {found!r} but the source declares platform {source.platform!r}.",
        )


def select(events: Sequence[Event], source: SourceConfig) -> tuple[tuple[Event, ...], int]:
    """Apply the source's own publish predicates, returning what survives and what did not.

    Placed after mapping and **before** enrichment, deliberately, for two reasons. Tags and
    series only exist once mapping has run, so a predicate on either has nothing to read
    before it. And an event we are not going to publish should not cost a page fetch --
    ORFE drops four final public orals, which is four fewer requests to their web server
    every time the feed is built.

    The dropped count comes back rather than being discarded, because a filtered feed and
    a feed whose upstream went quiet are indistinguishable from the outside. Publishing the
    number is what keeps "we chose not to" from reading like "there was nothing there".
    """
    if source.publishes_everything:
        return tuple(events), 0

    kept = [
        event
        for event in events
        if (source.publish_where is None or matches(event, source.publish_where))
        and (source.publish_unless is None or not matches(event, source.publish_unless))
    ]
    return tuple(kept), len(events) - len(kept)


def build_events_with_stats(
    text: str,
    source: SourceConfig,
    cache: PageCache | None = None,
    held: Mapping[str, Event] | None = None,
) -> tuple[tuple[Event, ...], dict[str, ScrapeStats], int]:
    """The pipeline, what enrichment did, and how many events the source declined to publish.

    One implementation, used by every caller.
    """
    check_platform(text, source)

    events = transform(parse_ics(text), source)
    events, declined = select(events, source)
    stats: dict[str, ScrapeStats] = {}
    if cache is not None:
        events, stats = enrich(events, source, cache, held)
    events, _ = fill_titles_for(events, source)
    return events, stats, declined


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


def build_from_text(
    text: str,
    source: SourceConfig,
    cache: PageCache | None = None,
    *,
    previous: Sequence[Mapping[str, Any]] | None = None,
    allow_large_diff: bool = False,
    schema_dir: str | Path = "schema",
    held: Mapping[str, Event] | None = None,
) -> BuildResult:
    """Build one source, attributing any failure to it rather than raising onward.

    The single bare-``Exception`` boundary. A source that fails leaves the others alone,
    and the reason travels with the result instead of being printed and lost.
    """
    enrich_stats: dict[str, ScrapeStats] = {}
    try:
        events, enrich_stats, declined = build_events_with_stats(text, source, cache, held)
    except SourceFatal as exc:
        return BuildResult(source=source.slug, status="failed", diagnostics=(exc.message,))
    except Exception as exc:
        return BuildResult(
            source=source.slug,
            status="failed",
            diagnostics=(f"unexpected {type(exc).__name__}: {exc}",),
        )

    # A scrape that reached almost no pages is the failure the predecessor cannot see,
    # so it fails the source rather than publishing a feed with everything unenriched.
    if problems := breaches(enrich_stats, source):
        return BuildResult(source=source.slug, status="failed", diagnostics=problems)

    # Gates run on every build, not only when something looks wrong: the failures they
    # catch are all shapes that satisfy the schema completely.
    gates = run_gates(events, source, previous=previous, allow_large_diff=allow_large_diff)
    if problems := failures(gates):
        return BuildResult(source=source.slug, status="failed", diagnostics=problems, gates=gates)

    if schema_problems := schema_errors(json.loads(render(events, source)), schema_dir=schema_dir):
        return BuildResult(
            source=source.slug,
            status="failed",
            diagnostics=tuple(f"schema: {p}" for p in schema_problems),
            gates=gates,
        )

    counts = {
        "events": len(events),
        "placeholder_titles": sum(1 for e in events if e.title_is_placeholder),
        "locations_declined": sum(1 for e in events if e.location.is_empty),
    }
    if declined:
        counts["declined"] = declined
    for field_name, stat in enrich_stats.items():
        counts[f"enriched_{field_name}"] = stat.filled
        if stat.rejected:
            counts[f"rejected_{field_name}"] = stat.rejected
        # Reuse is reported, not assumed. A run that fetched nothing and a run that
        # fetched everything and found nothing produce the same `enriched_` count, and
        # only one of them is working as designed.
        if stat.carried:
            counts[f"carried_{field_name}"] = stat.carried
    return BuildResult(
        source=source.slug,
        status="ok",
        events=events,
        counts=counts,
        notes=(*_declined_note(declined, source), *warnings(gates)),
        gates=gates,
    )


def _declined_note(declined: int, source: SourceConfig) -> tuple[str, ...]:
    """Say what the source chose not to publish, in the note a consumer can read.

    A filtered feed and a feed whose upstream went quiet look identical from outside. This
    is the line that separates them, and it names the predicate so the answer to "where did
    those events go" is in the manifest rather than in this repository's git history.
    """
    if not declined:
        return ()
    clause = "publish_unless" if source.publish_unless else "publish_where"
    predicate = source.publish_unless or source.publish_where or {}
    return (
        f"declined: {declined} event(s) present in the upstream feed are deliberately not "
        f"published here, per this source's {clause} ({_terse(predicate)}).",
    )


def _terse(predicate: Mapping[str, Any]) -> str:
    """A predicate as one readable clause."""
    return "; ".join(
        f"{field} {op} {value!r}"
        for field, tests in predicate.items()
        for op, value in tests.items()
    )


def build_from_file(
    path: str | Path,
    source: SourceConfig,
    cache: PageCache | None = None,
    *,
    previous: Sequence[Mapping[str, Any]] | None = None,
    allow_large_diff: bool = False,
    schema_dir: str | Path = "schema",
    held: Mapping[str, Event] | None = None,
) -> BuildResult:
    """Build one source from an ICS file on disk."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return BuildResult(
            source=source.slug, status="failed", diagnostics=(f"cannot read {path}: {exc}",)
        )
    return build_from_text(
        text,
        source,
        cache,
        previous=previous,
        allow_large_diff=allow_large_diff,
        schema_dir=schema_dir,
        held=held,
    )


__all__: list[str] = [
    "BuildResult",
    "build_events",
    "build_from_file",
    "build_from_text",
    "load_pronunciation",
    "render",
]
