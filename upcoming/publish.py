"""Assembling everything a run produced into one publishable tree.

Three artifacts, and the third is the one that makes a partial publish honest:

* ``feeds/<source>/events.json`` -- one per source, faithful to its upstream
* ``combos/<name>/events.json`` -- the declared combinations
* ``status.json`` -- what succeeded, what failed, and how stale anything is

A source that fails keeps serving its **last good feed**, because a departmental listing
going blank is worse than being a few hours old, and one bad feed must not block the other
eleven. But a silent partial publish is worse than either, so the staleness is stated in
``status.json`` and in the per-source ``status.json`` beside the feed. The predecessor has
no equivalent: a consumer polling its feed cannot distinguish fresh from frozen, and its own
handover names that as the worst shape a failure can take.

``status.json`` is deliberately the only file carrying a wall clock. Keeping timestamps out
of the feeds is what lets the published bytes be compared byte-for-byte to detect drift.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .build import BuildResult, render
from .clock import now as clock_now
from .clock import parse as parse_instant
from .clock import stamp
from .combine import Combo, ComboResult
from .registry import Registry, SourceConfig
from .serialize import WireFormat, dump_feed, load_feed, wire_format_for

#: Status values a source can end a run with. ``empty`` is first-class rather than a kind
#: of failure: kellercenter serves a well-formed calendar with no events, and the payload
#: is identical to a broken one -- only the source's declaration separates them.
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_DISABLED = "disabled"

#: Tolerance when comparing elapsed time against a cadence. Without it a 60-minute cadence
#: checked by a 20-minute schedule waits 80 minutes, because the tick at 60 lands a few
#: seconds early and the next one is 20 minutes later. Half a tick is enough to absorb
#: that without ever letting a source drift past its next scheduled slot.
_CADENCE_SLACK_MINUTES = 10


@dataclass(frozen=True)
class PublishedFeed:
    """One file in the tree, and what a consumer needs to know about it."""

    path: str
    status: str
    events: int
    stale: bool = False
    detail: str = ""
    sources: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: When this feed's content was last built from a live fetch. Carried forward across
    #: failures, so ``stale`` stops being a yes/no and becomes a duration: the watchdog can
    #: then tell a twenty-minute blip from a week-long outage, which the two need, because
    #: one is noise and the other is an outage nobody has noticed.
    last_success: str = ""


@dataclass
class Tree:
    """The assembled output, in memory until written."""

    files: dict[str, str] = field(default_factory=dict)
    feeds: list[PublishedFeed] = field(default_factory=list)

    def add(self, path: str, body: str, record: PublishedFeed) -> None:
        self.files[path] = body
        self.feeds.append(record)


def _last_good(root: Path, path: str) -> str | None:
    """The previously published bytes for a path, if any.

    What a failed source keeps serving. Read from the tree rather than from a cache, so
    "what is live" and "what we would republish" are the same thing by construction.
    """
    candidate = root / path
    return candidate.read_text(encoding="utf-8") if candidate.is_file() else None


def previous_payload(
    root: Path, path: str, wire_format: WireFormat | None = None
) -> list[Mapping[str, Any]] | None:
    """The previously published feed, parsed.

    With a ``wire_format``, the source's escaping is reversed so the records are
    model-shaped and can be rebuilt into events. Without one the records are returned as
    published, which is what the diff gate wants -- it compares identity, and reversing
    escaping to count ids would be wasted work.
    """
    body = _last_good(root, path)
    if body is None:
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    records: list[Mapping[str, Any]] = list(parsed)
    if wire_format is None:
        return records
    return list(load_feed(records, wire_format))


def previous_status(root: Path) -> dict[str, dict[str, Any]]:
    """The last published ``status.json``, indexed by feed path.

    Read so ``lastSuccessAt`` survives a failure. Without carrying it forward, the first
    failed run would erase the only record of when the feed was actually current, and every
    subsequent run would report the outage as if it had just started.
    """
    body = _last_good(root, "status.json")
    if body is None:
        return {}
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        return {}
    feeds = document.get("feeds") if isinstance(document, dict) else None
    if not isinstance(feeds, list):
        return {}
    return {
        str(record["path"]): record
        for record in feeds
        if isinstance(record, dict) and "path" in record
    }


def due(
    source: SourceConfig, was: Mapping[str, Mapping[str, Any]], *, now: datetime
) -> tuple[bool, str]:
    """Is this source due for a refetch, per its own declared cadence?

    The cadence has been in ``config/sources.yaml`` since the first commit and nothing read
    it, which made it a promise the code did not keep. Honouring it is politeness with
    teeth: `cee` published one event this term, and refetching it every twenty minutes is
    seventy-two requests a day to a departmental server to be told nothing changed.

    A source that is not due is **current**, not stale -- it is serving exactly what its own
    configuration asked for. Conflating the two would report eight of twelve departments as
    degraded on most ticks.
    """
    record = was.get(f"feeds/{source.slug}/events.json")
    if record is None:
        return True, "never published"
    raw = record.get("lastSuccessAt")
    last = parse_instant(raw) if isinstance(raw, str) else None
    if last is None:
        return True, "no recorded success to measure from"
    waited = (now - last).total_seconds() / 60
    if waited < 0:
        # A stamp in the future. Refetch rather than trust it: a clock this wrong would
        # otherwise park the source indefinitely.
        return True, "last success is dated in the future"
    if waited + _CADENCE_SLACK_MINUTES < source.cadence_minutes:
        return False, f"fetched {waited:.0f}m ago, cadence is {source.cadence_minutes}m"
    return True, f"fetched {waited:.0f}m ago, cadence is {source.cadence_minutes}m"


def assemble(
    registry: Registry,
    results: Mapping[str, BuildResult],
    combos: Mapping[str, ComboResult],
    combo_config: Mapping[str, Combo],
    *,
    root: Path,
    generated_at: str,
) -> Tree:
    """Build the tree from what this run produced plus what is already published.

    ``generated_at`` is passed in rather than read from the clock here, so a caller can
    make a run reproducible and so nothing in this module reaches for the time.
    """
    tree = Tree()
    was = previous_status(root)
    #: slug -> why a combined feed built from it is not fully current. Recorded during the
    #: per-source pass and read during the combo pass, so a combo never has to guess at the
    #: state of its inputs by inspecting sibling records.
    degraded: dict[str, str] = {}

    for source in registry.sources:
        path = f"feeds/{source.slug}/events.json"

        if not source.is_live:
            tree.feeds.append(
                PublishedFeed(
                    path=path,
                    status=STATUS_DISABLED,
                    events=0,
                    detail=" ".join(source.reason.split())[:300],
                    sources=(source.slug,),
                )
            )
            continue

        result = results.get(source.slug)
        if result is not None and result.ok:
            body = render(result.events, source)
            status = STATUS_EMPTY if not result.events else STATUS_OK
            tree.add(
                path,
                body,
                PublishedFeed(
                    path=path,
                    status=status,
                    events=len(result.events),
                    sources=(source.slug,),
                    notes=result.notes,
                    last_success=generated_at,
                ),
            )
            continue

        # Failed. Keep serving the last good bytes, and say so -- a blank departmental
        # listing is worse than a stale one, and an unannounced stale one is worse than
        # both.
        detail = "; ".join(result.diagnostics) if result else "not built in this run"
        previous = _last_good(root, path)
        if previous is None:
            tree.feeds.append(
                PublishedFeed(
                    path=path,
                    status=STATUS_FAILED,
                    events=0,
                    detail=f"{detail}. Never published, so nothing is served at this path.",
                    sources=(source.slug,),
                )
            )
            degraded[source.slug] = f"{source.slug} failed and has never been published"
            continue

        tree.add(
            path,
            previous,
            PublishedFeed(
                path=path,
                status=STATUS_FAILED,
                events=len(json.loads(previous)),
                stale=True,
                detail=detail,
                sources=(source.slug,),
                last_success=str(was.get(path, {}).get("lastSuccessAt", "")),
            ),
        )
        degraded[source.slug] = f"the last good copy of {source.slug}"

    for name, combo in combo_config.items():
        path = f"combos/{name}/events.json"
        if not combo.enabled:
            tree.feeds.append(
                PublishedFeed(
                    path=path,
                    status=STATUS_DISABLED,
                    events=0,
                    detail=" ".join(combo.reason.split())[:300],
                )
            )
            continue

        built = combos.get(name)
        if built is None:
            continue

        # A combined feed is only as current as its least current input. Read from the
        # per-source pass rather than inferred from sibling records, so a combo cannot
        # pick up the staleness of another combo that happens to share a slug.
        reasons = tuple(sorted({degraded[s] for s in built.inputs if s in degraded}))

        notes: list[str] = []
        if built.merged:
            notes.append(
                f"{built.merged} event(s) were published by more than one source with "
                f"every compared detail matching, and were merged into one record naming "
                f"all of them"
            )
        if built.divergences:
            notes.append(
                f"{len(built.divergences)} group(s) matched on {', '.join(combo.key)} "
                f"but differed in detail, so every record was kept rather than picking a "
                f"winner"
            )

        tree.add(
            path,
            dump_feed(built.events, wire_format_for(())),
            PublishedFeed(
                path=path,
                status=STATUS_OK,
                events=len(built.events),
                stale=bool(reasons),
                sources=built.inputs,
                # A combined feed is exactly as current as its oldest input, so the
                # earliest stamp is the honest one. Using `generated_at` here would report
                # a feed built from a week-old copy as fresh.
                last_success=min(
                    (
                        f.last_success
                        for f in tree.feeds
                        if f.last_success and f.sources and f.sources[0] in built.inputs
                    ),
                    default=generated_at,
                ),
                detail=f"built from {', '.join(reasons)}" if reasons else "",
                notes=tuple(notes),
            ),
        )

    # Written before status.json so a static file can never overwrite the manifest, and
    # after the feeds so it can never overwrite one either.
    for path, body in site_files().items():
        tree.files.setdefault(path, body)
    for path, body in site_files(SCHEMA_ROOT).items():
        tree.files.setdefault(f"schema/{path}", body)

    tree.files["status.json"] = status_document(tree, generated_at=generated_at)
    return tree


def status_document(tree: Tree, *, generated_at: str) -> str:
    """The health manifest, and the published contract for "is this feed current".

    A consumer fetching only ``events.json`` cannot tell fresh from frozen -- GitHub Pages
    sets no header we control, and the feeds deliberately carry no timestamp. This file is
    the answer, and the README says so rather than leaving it a debugging artifact.
    """
    summary: dict[str, int] = {}
    for feed in tree.feeds:
        summary[feed.status] = summary.get(feed.status, 0) + 1

    document = {
        "generatedAt": generated_at,
        "summary": {
            **summary,
            "events": sum(f.events for f in tree.feeds if f.path.startswith("feeds/")),
            "stale": sum(1 for f in tree.feeds if f.stale),
        },
        "feeds": [
            {
                "path": feed.path,
                "status": feed.status,
                "events": feed.events,
                **({"stale": True} if feed.stale else {}),
                **({"detail": feed.detail} if feed.detail else {}),
                **({"lastSuccessAt": feed.last_success} if feed.last_success else {}),
                **({"sources": list(feed.sources)} if feed.sources else {}),
                **({"notes": list(feed.notes)} if feed.notes else {}),
            }
            for feed in sorted(tree.feeds, key=lambda f: f.path)
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


#: Static files copied into the tree verbatim. The landing page reads ``status.json`` at
#: load time rather than being generated here, for two reasons: it cannot drift out of step
#: with the feeds, and it can be edited without running the pipeline -- which is why the
#: predecessor ended up with a second workflow just to republish its landing page.
SITE_ROOT = Path("site")

#: The schemas, published under ``schema/`` so a consumer can ``$ref`` them over HTTPS.
#: Shipping them in the repository only is half a contract: a validator cannot resolve a
#: path in somebody else's git tree, and ``events.schema.json`` ``$ref``s its sibling, so
#: both must be served or neither resolves.
SCHEMA_ROOT = Path("schema")


def site_files(root: Path = SITE_ROOT) -> dict[str, str]:
    """The static files to serve alongside the feeds, keyed by published path.

    Absent is not an error: a build that only produces data is a valid build, and the
    watchdog reports a missing index separately rather than failing the publish.
    """
    if not root.is_dir():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.startswith(".")
    }


def markdown_summary(tree: Tree, *, generated_at: str) -> str:
    """A run summary for a CI job page.

    Lives here rather than in a workflow step so it is covered by tests and so a developer
    can see exactly what a run will report without triggering one. The predecessor's
    equivalent is a shell heredoc inside YAML, which can only be exercised by pushing.
    """
    counts: dict[str, int] = {}
    for feed in tree.feeds:
        counts[feed.status] = counts.get(feed.status, 0) + 1
    headline = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    stale = sum(1 for f in tree.feeds if f.stale)

    lines = [
        f"### Published {generated_at}",
        "",
        f"{headline}" + (f" — **{stale} stale**" if stale else ""),
        "",
        "| feed | status | events | note |",
        "| --- | --- | ---: | --- |",
    ]
    for feed in sorted(tree.feeds, key=lambda f: f.path):
        mark = "**stale**" if feed.stale else feed.status
        note = " ".join((feed.detail, *feed.notes)).strip()
        lines.append(f"| `{feed.path}` | {mark} | {feed.events} | {_cell(note)} |")
    return "\n".join(lines) + "\n"


def _cell(text: str, limit: int = 140) -> str:
    """One table cell: no pipes, no newlines, and short enough to read."""
    flat = " ".join(text.split()).replace("|", "\\|")
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def write(tree: Tree, root: str | os.PathLike[str]) -> list[str]:
    """Write the tree, returning the paths written, sorted."""
    base = Path(root)
    for path, body in sorted(tree.files.items()):
        target = base / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return sorted(tree.files)


def utc_now() -> str:
    """The current instant, spelled the way everything published spells it."""
    return stamp(clock_now())


__all__ = [
    "SCHEMA_ROOT",
    "STATUS_DISABLED",
    "STATUS_EMPTY",
    "STATUS_FAILED",
    "STATUS_OK",
    "PublishedFeed",
    "Tree",
    "assemble",
    "due",
    "markdown_summary",
    "previous_payload",
    "previous_status",
    "site_files",
    "status_document",
    "utc_now",
    "write",
]
