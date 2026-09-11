"""Canonical tags: one name for a thing several departments spell differently.

A final public oral examination reaches these feeds as ``FPO``, ``Final Public Oral Exam``,
``Final Public Oral Examinations`` and ``Final Public Orals`` -- four spellings for one
concept. Until they agree on a name, a combined feed cannot say "exclude FPOs", and the
predecessor demonstrates the consequence: its workflow excludes the literal string ``FPO``,
which matches nothing in MAE's feed, so the "filtered" variant it publishes is identical to
the unfiltered one. Nothing reports that.

Matching is casefold plus whitespace collapse, then an **exact** alias lookup. No stemming
and no fuzzy matching, because a tag that silently becomes a different tag is the same
class of error as a transposed title, and would be equally invisible.

An unmapped category is never dropped. It stays on the event in ``raw_categories`` and is
reported as unmapped, so a department inventing a new series shows up as a number.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .config import read_mapping
from .errors import ConfigFatal

DEFAULT_TAGS = "config/tags.yaml"

_SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")

#: A trailing academic term, e.g. "PMI/PCCM Seminar Series Fall 2026". The series is the
#: same series next term, so the suffix is dropped before the alias lookup -- otherwise the
#: vocabulary would need a new entry every year and would silently stop matching in between.
_TERM_SUFFIX = re.compile(r"\s+(spring|summer|fall|autumn|winter)\s+\d{4}$", re.IGNORECASE)


def _key(value: str) -> str:
    """The lookup form of a category: casefolded, whitespace collapsed, term suffix dropped."""
    collapsed = " ".join(value.split())
    return _TERM_SUFFIX.sub("", collapsed).casefold()


@dataclass(frozen=True)
class TagVocabulary:
    """Canonical tags and the upstream spellings that reach them."""

    #: Lookup key -> canonical tag.
    by_alias: Mapping[str, str] = field(default_factory=dict)
    #: Canonical tag -> human label.
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def canonical(self) -> frozenset[str]:
        return frozenset(self.labels)

    def normalize(self, categories: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Canonical tags for ``categories``, and the spellings nothing recognised.

        Tags come back sorted and deduplicated, so two aliases of one concept on the same
        event -- ece publishes ``Final Public Oral Examinations`` and ``Final Public
        Orals`` together -- yield one tag rather than two.
        """
        tags: set[str] = set()
        unmapped: list[str] = []
        for category in categories:
            canonical = self.by_alias.get(_key(category))
            if canonical:
                tags.add(canonical)
            elif category.strip():
                unmapped.append(category.strip())
        return tuple(sorted(tags)), tuple(unmapped)

    def known(self, tag: str) -> bool:
        return tag in self.labels


def load_tags(path: str | os.PathLike[str] = DEFAULT_TAGS) -> TagVocabulary:
    """Load the tag vocabulary, refusing anything ambiguous.

    An alias mapping to two different canonical tags is a hard error: whichever won would
    be an accident of file order, and the loser would silently never appear.
    """
    config = Path(path)
    document = read_mapping(config, what="tag vocabulary", allow={"tags"})

    by_alias: dict[str, str] = {}
    labels: dict[str, str] = {}

    for tag, entry in (document.get("tags") or {}).items():
        if not set(tag) <= _SLUG_CHARS:
            raise ConfigFatal(
                f"tag {tag!r} is not a lowercase slug. Canonical tags are published and "
                f"matched on by combo predicates, so they need a stable spelling."
            )
        if not isinstance(entry, dict):
            raise ConfigFatal(f"tag {tag!r} must be a mapping")

        labels[tag] = str(entry.get("label") or tag)

        # The canonical name is always its own alias, so config need not repeat it.
        for alias in [tag, *(entry.get("aliases") or [])]:
            key = _key(str(alias))
            if not key:
                continue
            if (existing := by_alias.get(key)) and existing != tag:
                raise ConfigFatal(
                    f"alias {alias!r} maps to both {existing!r} and {tag!r}. Whichever won "
                    f"would be an accident of file order, and the other would silently "
                    f"never appear."
                )
            by_alias[key] = tag

    if not labels:
        raise ConfigFatal(f"{config} declares no tags")

    return TagVocabulary(by_alias=by_alias, labels=labels)


__all__ = ["DEFAULT_TAGS", "TagVocabulary", "load_tags"]
