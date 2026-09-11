"""Combined feeds: set algebra over sources, with a merge rule that refuses to guess.

Two rules govern everything here, and they are deliberately asymmetric.

**A per-source feed never de-duplicates.** It is a faithful reproduction of one upstream
feed, including its own duplicates -- ORFE publishes one talk twice, as two separate Drupal
nodes with different UIDs, URLs and punctuation. Suppressing one would be silently editing a
publisher's data. A consumer reading two of our feeds that happen to collide resolves that
itself.

**A combined feed de-duplicates only when every detail matches.** We are the ones combining,
so the collision is ours to represent -- and the honest representation is one record naming
every source that carried it, in ``sources``. When the grouping key matches but the details
differ, *both* records are emitted and the divergence is counted. Merging them would mean
picking a winner, and picking a winner means guessing.

That conservatism is not belt-and-braces. An earlier draft of this design assumed
``ps_events`` UIDs were globally unique and would have keyed de-duplication on ``guid``
alone; they are per-site sequences, so that key would have grouped ai's "ORFE Colloquium"
with materials' "Materials Institute Symposium". The all-details-match rule is what stood
between that configuration and a merged record of two unrelated events -- which is why a
bare-identifier key is now refused outright.
"""

from __future__ import annotations

import collections
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigFatal
from .model import Event
from .tags import TagVocabulary

DEFAULT_COMBOS = "config/combos.yaml"

#: Fields a grouping key may name. Deliberately no bare identifier: ``guid`` is a per-site
#: sequence and ``id`` already includes the source, so either alone is useless for finding
#: the same event published by two units.
KEY_FIELDS = frozenset({"starts_at", "ends_at", "title", "url", "location", "speaker"})

#: Keys that identify rather than describe. Refused on their own -- see the module
#: docstring for the near miss that made this a hard error rather than a convention.
IDENTITY_ONLY = frozenset({"guid", "id"})

#: Fields compared to decide whether two events are *the same event*.
#:
#: Upstream facts only. Scrape-derived fields are excluded because they depend on when each
#: source's page was fetched -- a rotating banner or an edit between two requests would
#: defeat a merge that should succeed. ``sources`` is excluded because it is the output.
COMPARED = (
    "starts_at",
    "ends_at",
    "title",
    "speaker",
    "series",
    "location",
    "url",
    "content",
)

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"))

#: Predicate operators a `where` clause may use.
_OPS = frozenset(
    {
        "contains",
        "not_contains",
        "equals",
        "not_equals",
        "in",
        "not_in",
        "matches",
        "before",
        "after",
    }
)


def compare_form(value: str) -> str:
    """The form two values are compared in.

    Unicode-normalized, zero-width characters removed, dashes and quotes unified,
    whitespace collapsed, casefolded. Enough that two departments typing the same title
    differently still merge; not so much that two different titles collide.
    """
    text = unicodedata.normalize("NFC", value).translate(_ZERO_WIDTH)
    text = text.replace("–", "-").replace("—", "-")  # noqa: RUF001
    text = text.replace("‘", "'").replace("’", "'")  # noqa: RUF001
    text = text.replace("“", '"').replace("”", '"')
    return " ".join(text.split()).casefold()


def details_digest(event: Event) -> tuple[str, ...]:
    """The comparable facts about an event, in a fixed order."""
    return (
        event.starts_at,
        event.ends_at,
        compare_form(event.title),
        compare_form(event.speaker),
        compare_form(event.series),
        compare_form(f"{event.location.name}|{event.location.detail}"),
        compare_form(event.url),
        compare_form(event.content),
    )


def grouping_key(event: Event, fields: Sequence[str]) -> tuple[str, ...]:
    """The candidate key: events sharing one are considered for merging."""
    out = []
    for name in fields:
        if name == "location":
            out.append(compare_form(f"{event.location.name}|{event.location.detail}"))
        elif name in {"starts_at", "ends_at"}:
            out.append(getattr(event, name))
        else:
            out.append(compare_form(str(getattr(event, name, ""))))
    return tuple(out)


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Combo:
    """One declared combined feed."""

    name: str
    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    where: Mapping[str, Any] = field(default_factory=dict)
    exclude_where: Mapping[str, Any] = field(default_factory=dict)
    key: tuple[str, ...] = ("starts_at", "title")
    enabled: bool = True
    label: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Divergence:
    """A group whose key matched but whose details did not.

    Counted and reported, never merged and never fatal. Both of the real instances are
    legitimate and permanent: cee and mae publish the same talk with different locations,
    and ai and orfe list the same colloquium with different summaries.
    """

    key: tuple[str, ...]
    ids: tuple[str, ...]
    differing: tuple[str, ...]


@dataclass(frozen=True)
class ComboResult:
    events: tuple[Event, ...]
    merged: int = 0
    divergences: tuple[Divergence, ...] = ()
    inputs: tuple[str, ...] = ()


def _validate_predicate(node: Any, tags: TagVocabulary, *, where: str) -> None:
    if not isinstance(node, dict):
        raise ConfigFatal(f"{where}: a predicate must be a mapping")
    for field_name, test in node.items():
        if not isinstance(test, dict):
            raise ConfigFatal(f"{where}.{field_name}: expected a mapping of operator to value")
        for op, value in test.items():
            if op not in _OPS:
                raise ConfigFatal(
                    f"{where}.{field_name}: unknown operator {op!r}. "
                    f"Available: {', '.join(sorted(_OPS))}."
                )
            # A predicate naming a tag nothing produces would filter to nothing, silently.
            if (
                field_name == "tags"
                and op in {"contains", "not_contains"}
                and not tags.known(str(value))
            ):
                raise ConfigFatal(
                    f"{where}.tags: {value!r} is not a canonical tag. A predicate on a "
                    f"tag nothing produces filters to nothing and reports success. "
                    f"Available: {', '.join(sorted(tags.canonical))}."
                )


def load_combos(
    path: str | os.PathLike[str] = DEFAULT_COMBOS,
    *,
    known_sources: Sequence[str],
    tags: TagVocabulary,
) -> tuple[Combo, ...]:
    """Load and validate every declared combined feed."""
    config = Path(path)
    if not config.is_file():
        raise ConfigFatal(f"no combo configuration at {config}")

    try:
        document = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigFatal(f"{config} is not valid YAML: {exc}") from exc
    if unknown := set(document) - {"combos"}:
        raise ConfigFatal(f"{config}: unknown top-level keys {sorted(unknown)}")

    combos: list[Combo] = []
    seen: set[str] = set()
    for index, entry in enumerate(document.get("combos") or []):
        where = f"combos[{index}]"
        if not isinstance(entry, dict):
            raise ConfigFatal(f"{where} must be a mapping")

        name = str(entry.get("name") or "")
        if not name:
            raise ConfigFatal(f"{where} has no name")
        if name in seen:
            raise ConfigFatal(f"duplicate combo name {name!r}")
        seen.add(name)

        include = tuple(entry.get("include") or ())
        if not include:
            raise ConfigFatal(f"{name} includes no sources")
        for slug in [*include, *(entry.get("exclude") or ())]:
            if slug != "*" and slug not in known_sources:
                raise ConfigFatal(
                    f"{name} names unknown source {slug!r}. A combo over a source that "
                    f"does not exist silently produces fewer events than intended."
                )

        key = tuple(entry.get("key") or ("starts_at", "title"))
        if set(key) & IDENTITY_ONLY:
            raise ConfigFatal(
                f"{name} keys de-duplication on {sorted(set(key) & IDENTITY_ONLY)}. "
                f"ps_events UIDs are per-site sequences, so the same value names unrelated "
                f"events in different feeds -- measured: ps_events:4056:delta:0 is ai's "
                f"ORFE Colloquium and materials' Materials Institute Symposium. Key on "
                f"content instead."
            )
        if unknown_fields := set(key) - KEY_FIELDS:
            raise ConfigFatal(
                f"{name} keys on unknown field(s) {sorted(unknown_fields)}. "
                f"Available: {', '.join(sorted(KEY_FIELDS))}."
            )

        clauses = (
            ("where", entry.get("where")),
            ("exclude_where", entry.get("exclude_where")),
        )
        for clause, node in clauses:
            if node:
                _validate_predicate(node, tags, where=f"{name}.{clause}")

        enabled = bool(entry.get("enabled", True))
        if not enabled and not str(entry.get("reason") or "").strip():
            raise ConfigFatal(
                f"{name} is disabled but records no reason. The reason is the point: it "
                f"stops the next person re-deriving why."
            )

        combos.append(
            Combo(
                name=name,
                include=include,
                exclude=tuple(entry.get("exclude") or ()),
                where=entry.get("where") or {},
                exclude_where=entry.get("exclude_where") or {},
                key=key,
                enabled=enabled,
                label=str(entry.get("label") or name),
                reason=str(entry.get("reason") or ""),
            )
        )
    return tuple(combos)


# --------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------


def _field_value(event: Event, name: str) -> Any:
    if name == "tags":
        return event.tags
    if name == "location.name":
        return event.location.name
    if name == "location.detail":
        return event.location.detail
    if name == "sources":
        return event.sources
    return getattr(event, name, "")


def matches(event: Event, predicate: Mapping[str, Any]) -> bool:
    """Whether one event satisfies a `where` clause. Every field must hold."""
    for field_name, tests in predicate.items():
        value = _field_value(event, field_name)
        for op, operand in tests.items():
            if op == "contains":
                if operand not in value:
                    return False
            elif op == "not_contains":
                if operand in value:
                    return False
            elif op == "equals":
                if compare_form(str(value)) != compare_form(str(operand)):
                    return False
            elif op == "not_equals":
                if compare_form(str(value)) == compare_form(str(operand)):
                    return False
            elif op == "in":
                if not set(value if isinstance(value, tuple) else [value]) & set(operand):
                    return False
            elif op == "not_in":
                if set(value if isinstance(value, tuple) else [value]) & set(operand):
                    return False
            elif op == "matches":
                if not re.search(str(operand), str(value), re.IGNORECASE):
                    return False
            elif op == "before":
                if not (str(value) and str(value) < str(operand)):
                    return False
            elif op == "after" and not (str(value) and str(value) > str(operand)):
                return False
    return True


def combine(combo: Combo, by_source: Mapping[str, Sequence[Event]]) -> ComboResult:
    """Build one combined feed.

    Order: resolve includes, subtract excludes, apply predicates, group, merge only where
    every detail matches, then sort by ``(starts_at, id)`` so the output is byte-stable.
    """
    included = (
        list(by_source) if "*" in combo.include else [s for s in combo.include if s in by_source]
    )
    slugs = [s for s in included if s not in combo.exclude]

    candidates: list[Event] = []
    for slug in slugs:
        for event in by_source[slug]:
            if combo.where and not matches(event, combo.where):
                continue
            if combo.exclude_where and matches(event, combo.exclude_where):
                continue
            candidates.append(event)

    groups: dict[tuple[str, ...], list[Event]] = collections.defaultdict(list)
    for event in candidates:
        groups[grouping_key(event, combo.key)].append(event)

    out: list[Event] = []
    merged = 0
    divergences: list[Divergence] = []

    for key, members in groups.items():
        if len(members) == 1:
            out.append(members[0])
            continue

        by_digest: dict[tuple[str, ...], list[Event]] = collections.defaultdict(list)
        for event in members:
            by_digest[details_digest(event)].append(event)

        if len(by_digest) > 1:
            # The key matched but the details did not. Both survive: merging would mean
            # picking a winner, and picking a winner means guessing.
            divergences.append(
                Divergence(
                    key=key,
                    ids=tuple(sorted(e.id for e in members)),
                    differing=_differing_fields(members),
                )
            )

        for identical in by_digest.values():
            if len(identical) == 1:
                out.append(identical[0])
                continue
            # Every detail agrees, so merging is safe by construction -- there is nothing
            # to choose between. The collision becomes a fact the record carries.
            sources = tuple(sorted({s for e in identical for s in e.sources}))
            out.append(replace(identical[0], sources=sources))
            merged += len(identical) - 1

    return ComboResult(
        events=tuple(sorted(out, key=Event.sort_key)),
        merged=merged,
        divergences=tuple(divergences),
        inputs=tuple(slugs),
    )


def _differing_fields(events: Sequence[Event]) -> tuple[str, ...]:
    """Which compared fields actually disagree, for the report."""
    digests = [details_digest(e) for e in events]
    return tuple(COMPARED[i] for i in range(len(COMPARED)) if len({d[i] for d in digests}) > 1)


__all__ = [
    "COMPARED",
    "Combo",
    "ComboResult",
    "Divergence",
    "combine",
    "compare_form",
    "details_digest",
    "grouping_key",
    "load_combos",
    "matches",
]
