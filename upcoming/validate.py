"""Schema validation and the semantic gates a schema cannot express.

Two layers. The schema says what a record *is*; the gates say whether a feed is *plausible*.
Both matter, and neither substitutes for the other -- the failure this project exists to
prevent is output that satisfies the schema completely and is wrong.

Every gate here closes a specific way a run can report success while producing nothing
useful. None of them exists in either predecessor, which has: no event-count floor, so an
empty array publishes as an empty feed; no duplicate detection; no check that an event ends
after it starts; no bound on how much of a feed may vanish between runs; and no way to tell
a blocked scrape from a page with nothing on it.

Thresholds are **per source**, because a global constant sits permanently breached for the
sources whose normal is unusual -- kellercenter is legitimately empty, and quantum
legitimately carries a synthesized title on 14 of 18 events -- and a gate that always fires
teaches everyone to ignore it.
"""

from __future__ import annotations

import collections
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .errors import ConfigFatal
from .model import Event
from .registry import SourceConfig

DEFAULT_SCHEMA_DIR = "schema"

Severity = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict, with enough detail to act on without re-running anything."""

    gate: str
    result: Severity
    detail: str
    facts: Mapping[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.result == "fail"


def _registry(schema_dir: str | os.PathLike[str]) -> Any:
    """A resolver over the schema directory, so ``$ref`` works.

    The predecessor builds a bare validator with no resolver, which is why it keeps two
    hand-cloned schemas and says so in its own schema description. ``referencing`` makes
    that unnecessary in about ten lines.
    """
    import json

    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    resources = []
    for path in sorted(Path(schema_dir).glob("*.schema.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if "$id" not in document:
            raise ConfigFatal(f"{path}: every schema needs an $id to be $ref-able")
        resources.append(
            (document["$id"], Resource.from_contents(document, default_specification=DRAFT202012))
        )
    return Registry().with_resources(resources)


def schema_errors(
    payload: Sequence[Mapping[str, Any]],
    *,
    schema_dir: str | os.PathLike[str] = DEFAULT_SCHEMA_DIR,
    limit: int = 25,
) -> list[str]:
    """Every way ``payload`` fails the published schema, most specific first."""
    import json

    from jsonschema import Draft202012Validator, FormatChecker

    feed_schema = json.loads((Path(schema_dir) / "events.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(
        feed_schema,
        registry=_registry(schema_dir),
        # The predecessor passes no format checker, so its `"format": "uri"` on urlRef is
        # decorative and a malformed URL validates clean.
        format_checker=FormatChecker(),
    )

    out = []
    for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in error.path) or "(root)"
        out.append(f"{where}: {error.message}")
        if len(out) >= limit:
            out.append(f"... and more (showing {limit})")
            break
    return out


# --------------------------------------------------------------------------------------
# Semantic gates
# --------------------------------------------------------------------------------------


def gate_count_floor(events: Sequence[Event], source: SourceConfig) -> GateResult:
    """Enough events to be plausible -- but tolerant of feeds that are legitimately tiny.

    ``cee`` and ``robotics`` publish one event each, and kellercenter publishes none at
    all. Empty and broken produce identical payloads, so only the source's declared
    ``allow_empty`` can tell them apart. A floor kellercenter could never satisfy would
    leave it permanently red, which is how people learn to ignore alerts.
    """
    count = len(events)
    expectations = source.expectations

    if count == 0:
        if expectations.allow_empty:
            return GateResult(
                "count_floor",
                "pass",
                "feed is empty, and this source is declared allowed-empty",
                {"events": 0},
            )
        return GateResult(
            "count_floor",
            "fail",
            "feed is empty and this source expects events. An empty array is schema-valid, "
            "so nothing else would catch this.",
            {"events": 0},
        )

    if count < expectations.min_events:
        return GateResult(
            "count_floor",
            "fail",
            f"{count} event(s), below the declared minimum of {expectations.min_events}",
            {"events": count, "minimum": expectations.min_events},
        )
    return GateResult("count_floor", "pass", f"{count} event(s)", {"events": count})


def gate_unique_ids(events: Sequence[Event], source: SourceConfig) -> GateResult:
    """``id`` is unique within a feed.

    ``guid`` deliberately is not: a per-source feed reproduces its upstream faithfully, and
    ORFE publishes the same talk twice as two separate Drupal nodes. Those are distinct
    records, and the namespaced ``id`` is what keeps them distinguishable.
    """
    counts = collections.Counter(e.id for e in events)
    repeated = {i: n for i, n in counts.items() if n > 1}
    if repeated:
        return GateResult(
            "unique_ids",
            "fail",
            f"{len(repeated)} id(s) appear more than once: {sorted(repeated)[:5]}",
            {"repeated": len(repeated)},
        )
    return GateResult("unique_ids", "pass", f"{len(events)} distinct id(s)")


def gate_time_order(events: Sequence[Event], source: SourceConfig) -> GateResult:
    """An event ends no earlier than it starts."""
    backwards = [e.id for e in events if e.ends_at and e.ends_at < e.starts_at]
    if backwards:
        return GateResult(
            "time_order",
            "fail",
            f"{len(backwards)} event(s) end before they start: {backwards[:5]}",
            {"backwards": len(backwards)},
        )
    return GateResult("time_order", "pass", "every event ends no earlier than it starts")


def gate_placeholder_rate(events: Sequence[Event], source: SourceConfig) -> GateResult:
    """How much of a feed's titles this pipeline invented.

    The band is per source and wide where it should be. quantum posts 14 of 18 titles as
    TBD, and ORFE's feed carries no titles at all -- both normal, and both alarming
    anywhere else. What should alert is a *change*, not the level.
    """
    if not events:
        return GateResult("placeholder_rate", "pass", "no events")

    placeholders = sum(1 for e in events if e.title_is_placeholder)
    rate = placeholders / len(events)
    ceiling = source.expectations.max_placeholder_title_rate
    if rate > ceiling:
        return GateResult(
            "placeholder_rate",
            "fail",
            f"{placeholders} of {len(events)} titles synthesized ({rate:.0%}), above the "
            f"declared band of {ceiling:.0%}",
            {"placeholders": placeholders, "rate": round(rate, 3)},
        )
    return GateResult(
        "placeholder_rate",
        "pass",
        f"{placeholders} of {len(events)} titles synthesized",
        {"placeholders": placeholders, "rate": round(rate, 3)},
    )


def gate_location_declined(events: Sequence[Event], source: SourceConfig) -> GateResult:
    """How often the location chain refused to split a value.

    Declining is correct behaviour, not a failure -- but a sudden rise means a department
    changed its convention and the chain no longer fits.
    """
    if not events:
        return GateResult("location_declined", "pass", "no events")

    declined = sum(1 for e in events if e.location.is_empty)
    rate = declined / len(events)
    ceiling = source.expectations.max_location_declined_rate
    if rate > ceiling:
        return GateResult(
            "location_declined",
            "warn",
            f"{declined} of {len(events)} locations left empty ({rate:.0%}), above the "
            f"declared band of {ceiling:.0%}. The chain may no longer fit this source.",
            {"declined": declined, "rate": round(rate, 3)},
        )
    return GateResult("location_declined", "pass", f"{declined} location(s) left empty")


def gate_unmapped_tags(events: Sequence[Event], source: SourceConfig) -> GateResult:
    """Categories no canonical tag recognised.

    A warning, never a failure: a department is free to invent a series, and the feed is
    still correct. But a combined feed filtering on that concept will silently miss it
    until someone adds the alias, so it needs to be visible.
    """
    unmapped = collections.Counter(t for e in events for t in e.unmapped_tags)
    if unmapped:
        top = ", ".join(f"{name!r} ({n})" for name, n in unmapped.most_common(3))
        return GateResult(
            "unmapped_tags",
            "warn",
            f"{len(unmapped)} category spelling(s) have no canonical tag: {top}. A combined "
            f"feed filtering on that concept will miss these until an alias is added.",
            {"distinct": len(unmapped)},
        )
    return GateResult("unmapped_tags", "pass", "every category maps to a canonical tag")


def gate_uid_pattern(events: Sequence[Event], source: SourceConfig) -> GateResult:
    """UIDs look the way this source's platform says they should.

    Asserted rather than assumed. "Equal UID means the same event" is a property of
    Princeton Site Builder, not of this repository, and kellercenter is not Site Builder.
    """
    import re

    if not source.uid_pattern:
        return GateResult("uid_pattern", "pass", "no pattern declared for this platform")

    probe = re.compile(source.uid_pattern)
    odd = [e.guid for e in events if not probe.match(e.guid)]
    if odd:
        return GateResult(
            "uid_pattern",
            "warn",
            f"{len(odd)} UID(s) do not match the declared pattern: {odd[:3]}. The platform "
            f"may have changed how it mints them.",
            {"anomalies": len(odd)},
        )
    return GateResult("uid_pattern", "pass", f"{len(events)} UID(s) match the declared pattern")


def gate_diff_threshold(
    events: Sequence[Event], source: SourceConfig, previous: Sequence[Mapping[str, Any]] | None
) -> GateResult:
    """How much of the previously published feed disappeared.

    The one output-side gate, and the one that catches a truncated upstream response: a
    feed that returns 3 events instead of 23 is schema-valid and passes every other check.
    Skipped when the previous feed was too small for a ratio to mean anything.
    """
    if previous is None:
        return GateResult("diff_threshold", "pass", "no previously published feed to compare")
    if len(previous) <= 3:
        return GateResult("diff_threshold", "pass", "previous feed too small to threshold")

    before = {r.get("id") for r in previous}
    after = {e.id for e in events}
    removed = before - after
    ratio = len(removed) / len(before)
    ceiling = source.expectations.max_removed_ratio

    if ratio > ceiling:
        return GateResult(
            "diff_threshold",
            "fail",
            f"{len(removed)} of {len(before)} events disappeared ({ratio:.0%}, band "
            f"{ceiling:.0%}). Confirm upstream, then rebuild with --allow-large-diff.",
            {"removed": len(removed), "added": len(after - before), "rate": round(ratio, 3)},
        )
    return GateResult(
        "diff_threshold",
        "pass",
        f"+{len(after - before)} -{len(removed)} against the previous feed",
        {"removed": len(removed), "added": len(after - before)},
    )


#: Gates that need only the events. ``diff_threshold`` takes the previous feed too.
GATES = (
    gate_count_floor,
    gate_unique_ids,
    gate_time_order,
    gate_placeholder_rate,
    gate_location_declined,
    gate_unmapped_tags,
    gate_uid_pattern,
)


def run_gates(
    events: Sequence[Event],
    source: SourceConfig,
    *,
    previous: Sequence[Mapping[str, Any]] | None = None,
    allow_large_diff: bool = False,
) -> tuple[GateResult, ...]:
    """Every gate, in a fixed order, all of them run.

    Nothing short-circuits: a run that fails two gates should report both, because fixing
    one and rediscovering the other is a wasted cycle.
    """
    results = [gate(events, source) for gate in GATES]
    if not allow_large_diff:
        results.append(gate_diff_threshold(events, source, previous))
    return tuple(results)


def failures(results: Sequence[GateResult]) -> tuple[str, ...]:
    return tuple(f"{r.gate}: {r.detail}" for r in results if r.result == "fail")


def warnings(results: Sequence[GateResult]) -> tuple[str, ...]:
    return tuple(f"{r.gate}: {r.detail}" for r in results if r.result == "warn")


__all__ = [
    "GATES",
    "GateResult",
    "Severity",
    "failures",
    "run_gates",
    "schema_errors",
    "warnings",
]
