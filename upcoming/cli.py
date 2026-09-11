"""The single entry point.

Deliberately one command surface over one library. The predecessor has two entry points
that must be kept in step by hand -- its own contributor guide asks humans to do this --
and they have diverged twice: one ignored the transform config entirely for every library
caller, and after that was fixed it still performs no enrichment, so a library caller gets
a feed with no scraped titles or speakers. Both failures were schema-valid and silent.

Exit codes are distinct so CI can act on them:

==  ==================================================================
 0  everything fine
 2  a usage error
 3  the configuration is invalid -- nothing can be trusted, so nothing runs
 4  a source failed to build; its configuration was fine and its data was not
==  ==================================================================
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .build import BuildResult, build_from_file, load_pronunciation, render
from .combine import combine, load_combos
from .errors import ConfigFatal
from .fetch import FetchOutcome, HttpTransport, fetch_feeds
from .model import Event, from_wire
from .publish import (
    STATUS_DISABLED,
    assemble,
    markdown_summary,
    previous_payload,
    utc_now,
    write,
)
from .registry import load_registry
from .scrape import build_cache
from .serialize import wire_format_for
from .verify import FAIL, failures, verify, warnings

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFIG = 3
#: One or more sources failed to build. Distinct from a config error, because the
#: configuration was fine and the data was not -- different person, different fix.
EXIT_SOURCE_FAILED = 4

DEFAULT_REGISTRY = "config/sources.yaml"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="upcoming",
        description="Multi-source campus event feeds.",
    )
    parser.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"source registry path (default: {DEFAULT_REGISTRY})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "sources",
        help="print the resolved registry as a table",
        description=(
            "Print every source with what its SUMMARY field means. Reading this table is "
            "how you notice that two sources disagree and one of them is wrong."
        ),
    )
    sub.add_parser(
        "check",
        help="validate the registry and exit",
        description=(
            "Load and validate every source. Exits 3 on any configuration problem, so it "
            "is the cheapest gate to run first in CI."
        ),
    )

    build = sub.add_parser(
        "build",
        help="build one source's events.json",
        description=(
            "Build a source from a local ICS file. --feed is required, so the ICS never "
            "comes off the network here. --enrich additionally scrapes the source's "
            "declared page targets, which does."
        ),
    )
    build.add_argument("--source", required=True, help="source slug, e.g. orfe")
    build.add_argument(
        "--feed",
        required=True,
        help="path to an ICS file (fetching is not wired yet)",
    )
    build.add_argument(
        "--out",
        help="write here instead of feeds/<source>/events.json; '-' writes to stdout",
    )
    build.add_argument(
        "--enrich",
        action="store_true",
        help=(
            "also scrape the source's declared targets from its event pages. Off by "
            "default so a build reaches no network unless asked; requires the bypass "
            "credential, and fails the source if too few pages can be reached."
        ),
    )
    publish = sub.add_parser(
        "publish",
        help="build every live source and every combined feed into one tree",
        description=(
            "Build all twelve live sources, materialize the declared combined feeds, and "
            "write the tree with a status.json saying what succeeded and what is stale. A "
            "source that fails keeps serving its last good feed and is marked stale -- a "
            "departmental listing going blank is worse than one a few hours old, and an "
            "unannounced stale one is worse than both."
        ),
    )
    publish.add_argument("--out", default="dist", help="output directory (default: dist)")
    publish.add_argument(
        "--feeds",
        default="tests/fixtures/feeds",
        help="directory of <slug>/feed.ics files to build from",
    )
    publish.add_argument("--enrich", action="store_true", help="also scrape event pages")
    publish.add_argument(
        "--summary",
        help="write a markdown run summary to this path (CI writes it to the job page)",
    )
    publish.add_argument(
        "--fetch",
        action="store_true",
        help=(
            "fetch each source's ICS into --feeds first. Without this the run is offline "
            "and reproducible from whatever is already there."
        ),
    )
    publish.add_argument(
        "--allow-large-diff",
        action="store_true",
        help=(
            "skip the guard that refuses a build losing most of a feed. Use after "
            "confirming upstream really did shrink."
        ),
    )
    verify_cmd = sub.add_parser(
        "verify",
        help="check what the published site is actually serving",
        description=(
            "A watchdog that knows nothing about the run that produced the site. It runs "
            "on its own schedule for a reason: the predecessor's pipeline died before its "
            "Pages steps, so those steps were skipped rather than failed, and a skipped "
            "step is green. A check inside that job would have been skipped too."
        ),
    )
    verify_cmd.add_argument("--base-url", required=True, help="origin serving the tree")
    verify_cmd.add_argument(
        "--max-age-minutes",
        type=int,
        default=90,
        help=(
            "how stale status.json may be before it is a failure (default: 90, three "
            "missed runs at the 30-minute cadence)"
        ),
    )
    verify_cmd.add_argument(
        "--warnings-are-failures",
        action="store_true",
        help="exit non-zero on warnings too, for a stricter gate",
    )
    return parser


def _first_sentence(text: str, limit: int = 96) -> str:
    """One readable line from a folded YAML paragraph.

    The registry's reasons are multi-sentence on purpose -- they record an investigation so
    nobody repeats it -- but a listing wants the opening claim, not the whole record.
    """
    flat = " ".join(text.split())
    if not flat:
        return ""
    head, sep, _ = flat.partition(". ")
    candidate = head + ("." if sep else "")
    if len(candidate) <= limit:
        return candidate
    return flat[: limit - 1].rstrip() + "…"


def _cmd_sources(registry_path: str) -> int:
    registry = load_registry(registry_path)
    rows = []
    for source in registry.sources:
        rows.append(
            (
                source.slug,
                source.status,
                source.expectations.summary_role,
                source.cadence,
                ",".join(t.field_name for t in source.enrich) or "-",
                source.feed_url or "-",
            )
        )

    headings = ("SOURCE", "STATUS", "SUMMARY IS", "CADENCE", "ENRICH", "FEED")
    # Size every column to its widest cell, so a long enrichment list cannot shift the
    # feed column out of alignment and make the table unreadable.
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headings)]

    def row_text(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    print(row_text(headings))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(row_text(row))

    live = len(registry.live)
    print()
    print(
        f"{len(registry.sources)} sources, {live} live, "
        f"{len(registry.sources) - live} declared unavailable"
    )
    print(f"registry fingerprint: {registry.fingerprint()}")
    return EXIT_OK


def _cmd_check(registry_path: str) -> int:
    registry = load_registry(registry_path)
    print(f"registry ok: {len(registry.sources)} sources, {len(registry.live)} live")
    for source in registry.sources:
        if not source.is_live:
            print(f"  {source.slug:<16} unavailable  {_first_sentence(source.reason)}")
    return EXIT_OK


def _cmd_build(registry_path: str, args: argparse.Namespace) -> int:
    """Build one source from a local feed file."""
    registry = load_registry(registry_path)
    source = registry.by_slug(args.source)

    if not source.is_live:
        print(
            f"{source.slug} is declared unavailable, so it is never built. Reason: "
            f"{_first_sentence(source.reason, limit=200)}",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    load_pronunciation()
    cache = None
    if args.enrich:
        if not source.enrich:
            print(f"{source.slug} declares no enrichment targets", file=sys.stderr)
        cache = build_cache(source, HttpTransport(retries=source.http.retries))
    result = build_from_file(args.feed, source, cache)
    if not result.ok:
        for problem in result.diagnostics:
            print(f"{source.slug}: {problem}", file=sys.stderr)
        return EXIT_SOURCE_FAILED

    payload = render(result.events, source)
    if args.out == "-":
        print(payload, end="")
        return EXIT_OK

    out_path = Path(args.out) if args.out else Path("feeds") / source.slug / "events.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")

    counts = result.counts
    print(f"wrote {out_path} ({counts['events']} events)")
    if counts["placeholder_titles"]:
        print(
            f"  {counts['placeholder_titles']} title(s) synthesized and flagged titleIsPlaceholder"
        )
    if counts["locations_declined"]:
        print(f"  {counts['locations_declined']} location(s) left empty rather than guessed")
    for key, value in sorted(counts.items()):
        if key.startswith("enriched_") and value:
            print(f"  {value} {key.removeprefix('enriched_')} value(s) scraped from event pages")
        if key.startswith("rejected_") and value:
            print(
                f"  {value} {key.removeprefix('rejected_')} value(s) declined by a reject "
                f"pattern rather than published"
            )
    if cache is not None:
        print(f"  {cache.requests_made} page(s) fetched")
    return EXIT_OK


def _cmd_publish(registry_path: str, args: argparse.Namespace) -> int:
    """Build everything and assemble one publishable tree."""
    registry = load_registry(registry_path)
    tag_vocabulary = registry.sources[0].tags
    combo_list = load_combos(known_sources=[s.slug for s in registry.sources], tags=tag_vocabulary)
    load_pronunciation()

    out_root = Path(args.out)
    feeds_root = Path(args.feeds)

    # Fetching is opt-in so the default run stays offline and reproducible from the
    # committed fixtures. CI passes --fetch; a developer reproducing a published tree
    # locally does not, and gets the same code path over known bytes.
    fetched: dict[str, FetchOutcome] = {}
    if args.fetch:
        feeds_root.mkdir(parents=True, exist_ok=True)
        fetched = fetch_feeds(
            registry.live, feeds_root, HttpTransport(retries=registry.live[0].http.retries)
        )

    results: dict[str, BuildResult] = {}
    by_source: dict[str, tuple[Event, ...]] = {}
    failed: list[str] = []

    for source in registry.live:
        feed = feeds_root / source.slug / "feed.ics"

        # A fetch failure is reported as a failure of this source, not swallowed by
        # falling back to whatever ICS happens to be on disk. Building yesterday's capture
        # and publishing it as current is the silent staleness this design exists to
        # prevent -- the failure path already serves the last good feed, and says so.
        outcome = fetched.get(source.slug)
        if outcome is not None and not outcome.ok:
            detail = outcome.error or f"HTTP {outcome.code}"
            results[source.slug] = BuildResult(
                source.slug, "failed", diagnostics=(f"could not fetch the feed: {detail}",)
            )
            failed.append(source.slug)
            print(f"{source.slug}: could not fetch the feed: {detail}", file=sys.stderr)
            restore = previous_payload(
                out_root,
                f"feeds/{source.slug}/events.json",
                wire_format_for(source.escape_fields),
            )
            if restore:
                by_source[source.slug] = tuple(from_wire(r) for r in restore)
            continue

        cache = (
            build_cache(source, HttpTransport(retries=source.http.retries))
            if args.enrich and source.enrich
            else None
        )
        result = build_from_file(
            feed,
            source,
            cache,
            previous=previous_payload(out_root, f"feeds/{source.slug}/events.json"),
            allow_large_diff=args.allow_large_diff,
        )
        results[source.slug] = result
        if result.ok:
            by_source[source.slug] = result.events
        else:
            failed.append(source.slug)
            for problem in result.diagnostics:
                print(f"{source.slug}: {problem}", file=sys.stderr)
            # Feed the combined feeds this source's last good copy rather than nothing.
            # Omitting it would quietly shrink every combo that includes it, which is the
            # silent partial publish this whole layer exists to prevent.
            restore = previous_payload(
                out_root,
                f"feeds/{source.slug}/events.json",
                wire_format_for(source.escape_fields),
            )
            if restore:
                by_source[source.slug] = tuple(from_wire(r) for r in restore)

    # Combined feeds are built from whatever succeeded. A combo whose input failed is
    # built from that source's last good copy, and status.json names the substitution --
    # never silently dropped, which would quietly shrink a feed a consumer relies on.
    combos_built = {combo.name: combine(combo, by_source) for combo in combo_list if combo.enabled}

    tree_stamp = utc_now()
    tree = assemble(
        registry,
        results,
        combos_built,
        {c.name: c for c in combo_list},
        root=out_root,
        generated_at=tree_stamp,
    )
    written = write(tree, out_root)
    if args.summary:
        Path(args.summary).write_text(
            markdown_summary(tree, generated_at=tree_stamp), encoding="utf-8"
        )

    print(f"wrote {len(written)} file(s) to {out_root}")
    for record in sorted(tree.feeds, key=lambda f: f.path):
        if record.status == STATUS_DISABLED:
            continue
        mark = "stale" if record.stale else record.status
        print(f"  {record.path:42} {mark:8} {record.events:4} events")

    if failed:
        print(
            f"\n{len(failed)} source(s) failed and are serving their last good feed: "
            f"{', '.join(failed)}",
            file=sys.stderr,
        )
        # Published first, then red. Both halves matter: a consumer keeps a working feed,
        # and the run still reports a problem rather than passing quietly.
        return EXIT_SOURCE_FAILED
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    """Report on what the live origin serves. No registry, no config, no build."""
    findings, document = verify(
        args.base_url,
        HttpTransport(),
        now=datetime.now(UTC),
        max_age_minutes=args.max_age_minutes,
    )

    if document is not None:
        summary = document.get("summary")
        print(f"{args.base_url}  generated {document.get('generatedAt')}  {summary}")

    for finding in findings:
        stream = sys.stderr if finding.level == FAIL else sys.stdout
        print(f"{finding.level:4} {finding.path:42} {finding.message}", file=stream)

    bad = failures(findings)
    soft = warnings(findings)
    if not findings:
        print("the site is serving everything it promises")
    if bad or (soft and args.warnings_are_failures):
        print(
            f"\n{len(bad)} failure(s), {len(soft)} warning(s)",
            file=sys.stderr,
        )
        return EXIT_SOURCE_FAILED
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        if args.command == "build":
            return _cmd_build(args.registry, args)
        if args.command == "publish":
            return _cmd_publish(args.registry, args)
        if args.command == "verify":
            return _cmd_verify(args)
        handlers = {"sources": _cmd_sources, "check": _cmd_check}
        handler = handlers.get(args.command)
        if handler is None:  # pragma: no cover - argparse enforces the choice
            parser.error(f"unknown command {args.command!r}")
            return EXIT_USAGE
        return handler(args.registry)
    except ConfigFatal as exc:
        # Printed rather than raised: a traceback here would bury the message, and the
        # message is the actionable part -- it names the source and what it failed to
        # declare.
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
