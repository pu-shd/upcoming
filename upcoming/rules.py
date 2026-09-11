"""The mapping rule engine: an ordered chain per source, first match wins.

Four of these feeds pack more than one field into ``SUMMARY``, or mean different things by
it on different events of the same feed. citp publishes a bare speaker name, a talk title,
and ``speaker - title``, event by event. quantum packs series, title, speaker and
affiliation into one string. Neither can be expressed by a single per-source mapping, which
is what the two simple roles give.

The engine is deliberately small, and the ceiling is the point. No arithmetic, no computed
values, no cross-event state, no user-supplied code, and predicate nesting stops at depth
two. Anything needing more gets a **name** -- a pattern in ``config/patterns.yaml`` or a
predicate in ``patterns.py`` -- which config then selects. Without that ceiling this
becomes a second programming language living in a YAML file, reviewed by nobody and tested
by nothing.

Every rule carries an ``id``, and the id of the rule that fired lands on the event. That is
what makes a misclassification visible: a reordering that silently moves four events from
one rule to another still produces schema-valid output, so only the recorded distribution
catches it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .errors import ConfigFatal, SourceFatal
from .patterns import PREDICATES, Vocabulary

#: Fields a rule may write. Closed, so a typo is a config error rather than an assignment
#: that goes nowhere.
ASSIGNABLE = frozenset({"title", "speakers", "affiliation", "series_extra"})

#: What to do when no rule matches. There is deliberately no silent option: either the
#: source fails, or it declares that an unmatched summary is a title and accepts that.
ON_NO_MATCH = frozenset({"fail", "title"})

#: Predicate operators, closed set.
_LEAF_OPS = frozenset(
    {
        "matches",
        "predicate",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
        "equals",
        "in",
        "always",
    }
)
_COMBINATORS = frozenset({"all", "any", "not"})

#: Predicate nesting depth. Two is enough for "matches X and not Y"; three is a sign the
#: shape wants a named pattern instead.
MAX_DEPTH = 2


@dataclass(frozen=True)
class Outcome:
    """What a rule decided about one summary."""

    rule_id: str
    #: Field -> value, for fields the rule resolved from the feed.
    assigned: Mapping[str, str] = field(default_factory=dict)
    #: Fields the feed does not carry, so the fallback chain owns them.
    deferred: tuple[str, ...] = ()
    #: Text the rule would not guess at. Counted and published, never silently dropped.
    unresolved: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rule:
    """One rule: an id, a condition, and what to do when it holds."""

    id: str
    when: Mapping[str, Any]
    assign: Mapping[str, str] = field(default_factory=dict)
    capture: Mapping[str, str] = field(default_factory=dict)
    tag: tuple[str, ...] = ()
    defer: tuple[str, ...] = ()
    unresolved_from: str = ""


@dataclass(frozen=True)
class RuleChain:
    """A source's ordered rules, and what happens when none match."""

    rules: tuple[Rule, ...]
    on_no_match: str = "fail"


# --------------------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------------------


def _validate_predicate(node: Any, vocabulary: Vocabulary, *, where: str, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ConfigFatal(
            f"{where}: predicate nests deeper than {MAX_DEPTH}. A condition this involved "
            f"wants a named pattern, where it can carry its own examples."
        )
    if not isinstance(node, dict) or not node:
        raise ConfigFatal(f"{where}: a predicate must be a non-empty mapping")

    for key, value in node.items():
        if key in _COMBINATORS:
            children = value if isinstance(value, list) else [value]
            for index, child in enumerate(children):
                _validate_predicate(
                    child, vocabulary, where=f"{where}.{key}[{index}]", depth=depth + 1
                )
        elif key == "matches":
            vocabulary.pattern(str(value))
        elif key == "predicate":
            if str(value) not in PREDICATES:
                raise ConfigFatal(
                    f"{where}: unknown predicate {value!r}. Predicates are code with their "
                    f"own tests; config selects one but cannot define one. Available: "
                    f"{', '.join(sorted(PREDICATES))}."
                )
        elif key not in _LEAF_OPS:
            raise ConfigFatal(
                f"{where}: unknown operator {key!r}. Available: "
                f"{', '.join(sorted(_LEAF_OPS | _COMBINATORS))}."
            )


def load_chain(raw: Mapping[str, Any], vocabulary: Vocabulary, *, slug: str) -> RuleChain:
    """Build and validate one source's chain."""
    on_no_match = str(raw.get("on_no_match", "fail"))
    if on_no_match not in ON_NO_MATCH:
        raise ConfigFatal(
            f"{slug}: summary.on_no_match {on_no_match!r} is not one of "
            f"{', '.join(sorted(ON_NO_MATCH))}. There is no silent option on purpose."
        )

    entries = raw.get("rules") or []
    if not entries:
        raise ConfigFatal(
            f"{slug} declares summary_role 'rules' but lists none. The role exists to make "
            f"someone state what SUMMARY means; an empty chain states nothing."
        )

    rules: list[Rule] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"{slug}.summary.rules[{index}]"
        if not isinstance(entry, dict):
            raise ConfigFatal(f"{where} must be a mapping")

        rule_id = entry.get("id")
        if not rule_id:
            raise ConfigFatal(f"{where} has no id. The id is published on every event it maps.")
        if rule_id in seen:
            raise ConfigFatal(f"{slug}: duplicate rule id {rule_id!r}")
        seen.add(str(rule_id))

        when = entry.get("when")
        if when is None:
            raise ConfigFatal(f"{where} ({rule_id}) has no `when`")
        _validate_predicate(when, vocabulary, where=f"{where}.when")

        then = entry.get("then") or {}
        if not isinstance(then, dict):
            raise ConfigFatal(f"{where}.then must be a mapping")
        if unknown := set(then) - {"assign", "capture", "tag", "defer", "unresolved"}:
            raise ConfigFatal(f"{where}.then: unknown action(s) {sorted(unknown)}")

        assign = dict(then.get("assign") or {})
        capture = dict(then.get("capture") or {})
        defer = tuple(then.get("defer") or ())

        for target in list(assign) + list(capture) + list(defer):
            if target not in ASSIGNABLE:
                raise ConfigFatal(
                    f"{where} ({rule_id}) writes unknown field {target!r}. "
                    f"Available: {', '.join(sorted(ASSIGNABLE))}."
                )

        # Every named group a rule's pattern defines must be bound, or silently dropped.
        if capture:
            pattern_name = _pattern_name_of(when)
            if pattern_name is None:
                raise ConfigFatal(
                    f"{where} ({rule_id}) captures groups but its `when` names no pattern"
                )
            available = set(vocabulary.pattern(pattern_name).groups)
            if missing := set(capture.values()) - available:
                raise ConfigFatal(
                    f"{where} ({rule_id}) captures group(s) {sorted(missing)} that pattern "
                    f"{pattern_name!r} does not define. Available: {sorted(available)}."
                )
            if unbound := available - set(capture.values()):
                raise ConfigFatal(
                    f"{where} ({rule_id}) leaves group(s) {sorted(unbound)} of pattern "
                    f"{pattern_name!r} unbound. An unused group is a typo, and a typo here "
                    f"drops a field with nothing reporting it."
                )

        rules.append(
            Rule(
                id=str(rule_id),
                when=when,
                assign=assign,
                capture=capture,
                tag=tuple(then.get("tag") or ()),
                defer=defer,
                unresolved_from=str(then.get("unresolved") or ""),
            )
        )

    return RuleChain(rules=tuple(rules), on_no_match=on_no_match)


def _pattern_name_of(node: Mapping[str, Any]) -> str | None:
    """The pattern a predicate matches on, if it names exactly one."""
    if "matches" in node:
        return str(node["matches"])
    for key in _COMBINATORS:
        if key in node:
            children = node[key] if isinstance(node[key], list) else [node[key]]
            for child in children:
                if isinstance(child, dict) and (found := _pattern_name_of(child)):
                    return found
    return None


# --------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------


def _evaluate(
    node: Mapping[str, Any], value: str, vocabulary: Vocabulary
) -> tuple[bool, re.Match[str] | None]:
    """Whether the predicate holds, and the match object if one produced it."""
    match: re.Match[str] | None = None

    for key, operand in node.items():
        if key == "always":
            if not operand:
                return False, None
        elif key == "matches":
            match = vocabulary.pattern(str(operand)).regex.match(value)
            if match is None:
                return False, None
        elif key == "predicate":
            if not PREDICATES[str(operand)](value, vocabulary):
                return False, None
        elif key == "contains":
            if str(operand).casefold() not in value.casefold():
                return False, None
        elif key == "not_contains":
            if str(operand).casefold() in value.casefold():
                return False, None
        elif key == "starts_with":
            if not value.casefold().startswith(str(operand).casefold()):
                return False, None
        elif key == "ends_with":
            if not value.casefold().endswith(str(operand).casefold()):
                return False, None
        elif key == "equals":
            if value.strip().casefold() != str(operand).strip().casefold():
                return False, None
        elif key == "in":
            if value.strip().casefold() not in {str(v).strip().casefold() for v in operand}:
                return False, None
        elif key == "all":
            for child in operand:
                held, child_match = _evaluate(child, value, vocabulary)
                if not held:
                    return False, None
                match = match or child_match
        elif key == "any":
            for child in operand:
                held, child_match = _evaluate(child, value, vocabulary)
                if held:
                    match = match or child_match
                    break
            else:
                return False, None
        elif key == "not":
            children = operand if isinstance(operand, list) else [operand]
            for child in children:
                held, _ = _evaluate(child, value, vocabulary)
                if held:
                    return False, None

    return True, match


def apply_chain(summary: str, chain: RuleChain, vocabulary: Vocabulary, *, slug: str) -> Outcome:
    """Run ``summary`` through the chain and return what the first matching rule decided."""
    value = " ".join(summary.split())

    for rule in chain.rules:
        held, match = _evaluate(rule.when, value, vocabulary)
        if not held:
            continue

        assigned: dict[str, str] = {}
        for target, source_field in rule.assign.items():
            assigned[target] = value if source_field == "summary" else source_field
        if match is not None:
            for target, group in rule.capture.items():
                captured = (match.group(group) or "").strip()
                if captured:
                    assigned[target] = captured

        unresolved = ""
        if rule.unresolved_from:
            unresolved = (
                value
                if rule.unresolved_from == "summary"
                else (match.group(rule.unresolved_from) or "").strip()
                if match is not None
                else ""
            )

        return Outcome(
            rule_id=f"{slug}:{rule.id}",
            assigned=assigned,
            deferred=rule.defer,
            unresolved=unresolved,
            tags=rule.tag,
        )

    if chain.on_no_match == "fail":
        raise SourceFatal(
            slug,
            f"no rule matched SUMMARY {value[:80]!r}. The chain declares on_no_match: "
            f"fail, so this stops rather than guessing which field the text belongs to.",
        )
    return Outcome(rule_id=f"{slug}:no-match-title", assigned={"title": value})


def rule_ids(chain: RuleChain, slug: str) -> tuple[str, ...]:
    """Every rule id a chain can produce, for the dead-rule check."""
    return tuple(f"{slug}:{rule.id}" for rule in chain.rules)


__all__: Sequence[str] = [
    "ASSIGNABLE",
    "MAX_DEPTH",
    "ON_NO_MATCH",
    "Outcome",
    "Rule",
    "RuleChain",
    "apply_chain",
    "load_chain",
    "rule_ids",
]
