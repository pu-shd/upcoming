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
==  ==================================================================
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .errors import ConfigFatal
from .registry import load_registry

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFIG = 3

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

    def render(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    print(render(headings))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(render(row))

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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    handlers = {"sources": _cmd_sources, "check": _cmd_check}
    handler = handlers.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces the choice
        parser.error(f"unknown command {args.command!r}")
        return EXIT_USAGE

    try:
        return handler(args.registry)
    except ConfigFatal as exc:
        # Printed rather than raised: a traceback here would bury the message, and the
        # message is the actionable part -- it names the source and what it failed to
        # declare.
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
