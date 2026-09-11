"""The predicate vocabulary, shared by every config surface that filters events.

Two places now declare filters over events: ``config/combos.yaml``, which composes derived
feeds, and ``config/sources.yaml``, where a source may decline to publish part of its own
upstream feed. One vocabulary for both, defined here, because the alternative is two
dialects that start identical and drift -- and a department reading the combos
documentation to write a source filter would be learning the wrong thing.

The operator set is closed and the field set is resolved by name, so a typo in either is a
load error rather than a filter that silently never matches. That last property is the
whole point: a predicate that matches nothing and a predicate that is misspelled produce
the same empty result, and only one of them is what somebody meant.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from typing import Any

from .errors import ConfigFatal
from .model import Event
from .tags import TagVocabulary

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"))

#: Predicate operators a `where` clause may use.
OPS = frozenset(
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


def validate(
    node: Any,
    tags: TagVocabulary,
    *,
    where: str,
    purposes: Collection[str] = (),
) -> None:
    """Refuse a predicate that could never match.

    The two controlled vocabularies -- canonical tags and declared purposes -- are checked
    by name, because a predicate on a value nothing produces filters to nothing and reports
    success. That is indistinguishable from a filter that is simply strict, which is why it
    has to be a load error rather than something noticed later in a suspiciously short feed.
    """
    if not isinstance(node, dict):
        raise ConfigFatal(f"{where}: a predicate must be a mapping")
    for field_name, test in node.items():
        if not isinstance(test, dict):
            raise ConfigFatal(f"{where}.{field_name}: expected a mapping of operator to value")
        for op, value in test.items():
            if op not in OPS:
                raise ConfigFatal(
                    f"{where}.{field_name}: unknown operator {op!r}. "
                    f"Available: {', '.join(sorted(OPS))}."
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
            if (
                field_name == "purposes"
                and op in {"contains", "not_contains"}
                and str(value) not in purposes
            ):
                raise ConfigFatal(
                    f"{where}.purposes: {value!r} is not a declared purpose. A predicate "
                    f"on a publication nobody defined selects no events and reports "
                    f"success. Declared: "
                    f"{', '.join(sorted(purposes)) or 'none'} (see `purposes:` in "
                    f"config/sources.yaml)."
                )


__all__ = ["OPS", "compare_form", "matches", "validate"]
