"""Named patterns and named predicates: the vocabulary a source's rules select from.

This is the harmonized layer. A shape like ``<person> - <title>`` is the same wherever it
appears, so it is written once here and every source that uses it gets the same behaviour
and the same fix. What varies per source is *which* patterns apply and in what order, and
that lives in the source's own rule chain.

Two things are deliberately not configurable. A pattern's **safety** is checked here rather
than trusted, because a config file is not a place anyone expects to hang a build. And a
**predicate** -- a judgement call like "is this a person's name" -- is Python with its own
tests, selectable by name from config but never definable there. Config picks from a
vocabulary; it does not extend it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import read_mapping
from .errors import ConfigFatal

DEFAULT_PATTERNS = "config/patterns.yaml"

#: A pattern longer than this is unreviewable, and a reviewer who cannot read it cannot
#: catch a mistake in it.
MAX_PATTERN_LENGTH = 400
MAX_NAMED_GROUPS = 12

#: Constructs rejected outright. Backreferences and lookbehind are where catastrophic
#: backtracking hides, and neither is needed by any shape these feeds produce.
_FORBIDDEN = (
    (re.compile(r"\\[1-9]"), "a backreference"),
    (re.compile(r"\(\?<[=!]"), "a lookbehind"),
    (re.compile(r"\(\?\("), "a conditional"),
    (re.compile(r"\(\?R\)|\(\?[0-9]+\)"), "recursion"),
)

#: Nested unbounded quantifiers -- (a+)+ , (a*)* , (.+)+ -- the classic shape that turns a
#: 40-character input into a hang.
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*]\)[+*]")

_NAMED_GROUP_RE = re.compile(r"\(\?P<([A-Za-z_][A-Za-z0-9_]*)>")

#: A capitalised name token: "Guess", "ElKattan", "Gomez-Bombarelli", "O'Neil", "L."
#: Both apostrophes are accepted on purpose -- these feeds carry the typographic one.
_NAME_TOKEN = r"[A-Z][\w.'\u2019-]*"

#: A full name: capitalised tokens, optionally with a Princeton class year ("*22") or a
#: short lowercase particle ("van", "de", "bin").
_PERSON_RE = re.compile(rf"^{_NAME_TOKEN}(?:\s+(?:{_NAME_TOKEN}|\*\d{{2}}|[a-z]{{2,4}}))*$")

#: Punctuation and function words that belong to a sentence, never to a name.
_SENTENCE_RE = re.compile(
    r"[?!:;]|\.\s|\band\b|\bthe\b|\bfor\b|\bof\b|\bin\b|\bto\b|\bon\b|\bwith\b|\bfrom\b",
    re.IGNORECASE,
)

#: A name is two to four tokens. One is ambiguous with a single-word title; five or more is
#: a phrase.
_MIN_NAME_TOKENS = 2
_MAX_NAME_TOKENS = 4


@dataclass(frozen=True)
class NamedPattern:
    """One compiled pattern, with the examples that prove it does what it claims."""

    name: str
    regex: re.Pattern[str]
    groups: tuple[str, ...]
    match: tuple[str, ...] = ()
    no_match: tuple[str, ...] = ()


@dataclass(frozen=True)
class Vocabulary:
    """Everything a source's rules may refer to by name."""

    patterns: Mapping[str, NamedPattern] = field(default_factory=dict)
    #: Words no person is named. See ``person_name_shape``.
    not_a_person: frozenset[str] = frozenset()

    def pattern(self, name: str) -> NamedPattern:
        try:
            return self.patterns[name]
        except KeyError:
            raise ConfigFatal(
                f"unknown pattern {name!r}. A rule naming a pattern that does not exist "
                f"never fires, so the field it was meant to fill stays empty with nothing "
                f"reporting it. Available: {', '.join(sorted(self.patterns))}."
            ) from None


def lint_pattern(name: str, source: str) -> None:
    """Reject a pattern that is unsafe or unreviewable, at load rather than at runtime."""
    if len(source) > MAX_PATTERN_LENGTH:
        raise ConfigFatal(
            f"pattern {name!r} is {len(source)} characters, over the {MAX_PATTERN_LENGTH} "
            f"limit. A pattern nobody can read is a pattern nobody can check."
        )
    for probe, what in _FORBIDDEN:
        if probe.search(source):
            raise ConfigFatal(f"pattern {name!r} uses {what}, which is not allowed here")
    if _NESTED_QUANTIFIER.search(source):
        raise ConfigFatal(
            f"pattern {name!r} nests unbounded quantifiers, the shape that turns a short "
            f"input into a hang. Bound the inner repetition."
        )
    groups = _NAMED_GROUP_RE.findall(source)
    if len(groups) > MAX_NAMED_GROUPS:
        raise ConfigFatal(
            f"pattern {name!r} has {len(groups)} named groups, over {MAX_NAMED_GROUPS}"
        )
    if len(groups) != len(set(groups)):
        raise ConfigFatal(f"pattern {name!r} reuses a group name")


def load_vocabulary(path: str | os.PathLike[str] = DEFAULT_PATTERNS) -> Vocabulary:
    """Load, lint and compile every named pattern.

    A pattern with no negative examples is refused. The risk with these shapes is never
    failing to match -- it is matching something confidently and wrongly, and only a
    ``no_match`` example can catch that.
    """
    config = Path(path)
    document = read_mapping(config, what="pattern vocabulary", allow={"patterns", "not_a_person"})

    patterns: dict[str, NamedPattern] = {}
    for name, entry in (document.get("patterns") or {}).items():
        if not isinstance(entry, dict) or "regex" not in entry:
            raise ConfigFatal(f"pattern {name!r} has no regex")

        source = str(entry["regex"])
        lint_pattern(name, source)
        try:
            compiled = re.compile(source)
        except re.error as exc:
            raise ConfigFatal(f"pattern {name!r} does not compile: {exc}") from exc

        positives = tuple(entry.get("match") or ())
        negatives = tuple(entry.get("no_match") or ())
        if not positives:
            raise ConfigFatal(f"pattern {name!r} has no `match` examples")
        if not negatives:
            raise ConfigFatal(
                f"pattern {name!r} has no `no_match` examples. The failure mode here is a "
                f"confident wrong match, and only a negative example catches it."
            )

        patterns[name] = NamedPattern(
            name=name,
            regex=compiled,
            groups=tuple(_NAMED_GROUP_RE.findall(source)),
            match=positives,
            no_match=negatives,
        )

    return Vocabulary(
        patterns=patterns,
        not_a_person=frozenset(
            str(word).casefold() for word in (document.get("not_a_person") or ())
        ),
    )


def person_name_shape(value: str, vocabulary: Vocabulary) -> bool:
    """Whether ``value`` reads as a person's name rather than a title.

    The one real judgement call in the mapping. citp publishes five events whose entire
    ``SUMMARY`` is a bare name (``Andy Guess``) alongside thirteen whose ``SUMMARY`` is a
    title (``Bias in AI Reading Group``), and both are title-case word sequences -- shape
    alone cannot separate them.

    Three tests, all of which must pass:

    1. two to four tokens -- one is ambiguous with a single-word title, five is a phrase
    2. no sentence punctuation or function words
    3. no word from ``not_a_person`` -- nobody is named ``Symposium``

    The third is load-bearing rather than belt-and-braces: without it three real titles
    pass as names. It is data in ``config/patterns.yaml`` precisely so adding a word is not
    a code change.

    This is never trusted silently. Every event records which rule fired, and each source's
    test pins the expected distribution, so a reclassification fails a test even though the
    output stays schema-valid.
    """
    text = " ".join(value.split())
    if not text or _SENTENCE_RE.search(text):
        return False

    tokens = text.split()
    if not _MIN_NAME_TOKENS <= len(tokens) <= _MAX_NAME_TOKENS:
        return False

    for token in tokens:
        bare = re.sub(r"[^\w]", "", token, flags=re.UNICODE).casefold()
        if bare in vocabulary.not_a_person:
            return False

    return bool(_PERSON_RE.match(text))


#: Predicates a rule may select by name. Config picks from this; it cannot extend it.
PREDICATES: Mapping[str, Any] = {
    "person_name_shape": person_name_shape,
}


def unknown_predicates(names: Sequence[str]) -> tuple[str, ...]:
    return tuple(name for name in names if name not in PREDICATES)
