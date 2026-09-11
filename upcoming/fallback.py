"""Synthesizing a title when the feed carries none.

Not an edge case. Measured across the live feeds: ORFE carries no title on **any** of its
23 events, because its ``SUMMARY`` is the speaker; quantum carries a ``TBD`` title on 14 of
18; MAE has one event whose entire ``SUMMARY`` is the string ``TBD``. Since the schema
requires a non-empty title, synthesis is a precondition for publishing at all.

The contract that makes it honest is provenance. A synthesized title is published with
``titleSource`` naming which branch produced it and ``titleIsPlaceholder: true``, so a
consumer can always tell an invented title from one a department wrote. The predecessor
publishes MAE's literal ``"TBD"`` with **neither** field set, so a consumer cannot tell at
all.

The ``{a_an}`` article is chosen by **sound**, not spelling: "an S. S. Wilks seminar"
because the letter S is read "ess", but "a university" because it is read "yoo". The
algorithm is ported; the vocabulary it consults moves to ``config/pronunciation.yaml``,
because the predecessor hardcodes ``frozenset({"ORFE"})`` and its README instructs editing
that set for each new acronym -- which is a per-department code edit, exactly what this
project exists to remove.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .model import SPEAKER_JOIN, Event
from .provenance import TitleSource, is_missing

#: Placeholder for the article, substituted after the template is rendered so the choice
#: can see the word that actually follows it. NUL bytes cannot occur in feed text and
#: contain no braces, so the marker survives ``str.format_map`` untouched.
A_AN_MARKER = "\x00A_AN\x00"

#: Letters whose *names* begin with a vowel sound, so an initialism read letter by letter
#: takes "an": "an S. S. Wilks seminar" ("ess"), "an FPO" ("ef"). The consonant-sound
#: names are B C D G J K P Q T V W Y Z, plus U ("yoo").
VOWEL_SOUND_LETTERS = frozenset("AEFHILMNORSX")

#: Vowel letter, consonant sound: "a university", "a European tour", "a one-off".
CONSONANT_SOUND_PREFIXES = (
    "eu",
    "ewe",
    "once",
    "one",
    "ubiq",
    "uk",
    "ukr",
    "uni",
    "ura",
    "urol",
    "usa",
    "use",
    "usu",
    "utili",
    "utop",
)

#: Consonant letter, vowel sound: "an hour", "an honest broker", "an heir".
VOWEL_SOUND_PREFIXES = ("heir", "honest", "honor", "honour", "hour")

#: A rendered prefix longer than this is dropped rather than published. A template that
#: runs away should degrade to the speaker's name, not ship a paragraph as a title.
MAX_PREFIX_LEN = 128

_BRACE_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class _Blank(dict[str, str]):
    """Missing template keys render empty rather than raising."""

    def __missing__(self, key: str) -> str:
        return ""


def choose_article(following: str) -> str:
    """ "A" or "An" for the word that follows, judged by sound rather than spelling.

    Spelling alone is wrong in both directions: "S. S. Wilks" reads "ess" and takes "an",
    while "university" reads "yoo" and takes "a".
    """
    stripped = following.strip()
    token = stripped.split(" ", 1)[0] if stripped else ""
    letters = "".join(ch for ch in token if ch.isalpha())
    if not letters:
        return "A"

    # A spelled-out initialism is judged on the name of its first letter. A lone letter
    # always qualifies; so does an all-caps run, unless it is read as a word.
    spelled_out = len(letters) == 1 or (letters.isupper() and letters not in WORD_ACRONYMS)
    if spelled_out:
        return "An" if letters[0].upper() in VOWEL_SOUND_LETTERS else "A"

    lowered = letters.lower()
    if lowered.startswith(VOWEL_SOUND_PREFIXES):
        return "An"
    if lowered.startswith(CONSONANT_SOUND_PREFIXES):
        return "A"
    return "An" if lowered[0] in "aeiou" else "A"


#: All-caps tokens pronounced as words rather than spelled out, so the ordinary word rule
#: applies: ORFE reads "or-fee", hence "an ORFE colloquium". Replaced at load from
#: ``config/pronunciation.yaml``; this default keeps the module usable standalone.
WORD_ACRONYMS: frozenset[str] = frozenset({"ORFE"})


def set_word_acronyms(acronyms: Sequence[str]) -> None:
    """Install the acronym vocabulary read from config.

    Module-level because ``choose_article`` is a pure text function consulted from several
    places; the alternative is threading a vocabulary through every call for a value that
    is constant for a whole run.
    """
    global WORD_ACRONYMS
    WORD_ACRONYMS = frozenset(acronyms)


def resolve_articles(text: str) -> str:
    """Replace every article marker with "A" or "An" based on what follows it."""
    return re.sub(re.escape(A_AN_MARKER), lambda m: choose_article(text[m.end() :]), text)


def render_prefix(template: str, event: Event) -> str:
    """Render a source's title template against one event.

    Only a closed set of placeholders is accepted, and an unknown one is an error rather
    than a blank. The predecessor formats over the whole event dict and swallows any
    failure by falling back to the raw template, so a typo like ``{unclosed`` ships literal
    braces into a published title.
    """
    if not template:
        return ""

    fields: Mapping[str, str] = {
        "series": event.series.split(",")[0].strip(),
        "speaker": event.speaker,
        "source": event.sources[0] if event.sources else "",
        "a_an": A_AN_MARKER,
    }
    unknown = {name for name in _BRACE_RE.findall(template)} - set(fields)
    if unknown:
        raise ValueError(
            f"title template names unknown placeholder(s) {sorted(unknown)}; "
            f"available: {', '.join(sorted(fields))}"
        )

    rendered = template.format_map(_Blank(fields))
    rendered = " ".join(rendered.split())
    return resolve_articles(rendered)


def synthesize_title(
    event: Event, template: str = "", *, include_speaker: bool = True
) -> tuple[str, TitleSource]:
    """A title for an event that carries none, and the branch that produced it.

    Three branches, in order, so the most informative available answer wins:

    1. the speaker, optionally behind the rendered template
    2. the template alone, with a dangling "by" trimmed since no name follows
    3. the series, as a last resort -- "An Optimization Seminar Talk"

    A title is always produced. Leaving it empty would fail the schema, and the predecessor
    had to duplicate this call into both of its entry points to avoid exactly that.
    """
    prefix = render_prefix(template, event)
    use_prefix = bool(prefix) and len(prefix) < MAX_PREFIX_LEN
    speaker = event.speaker if include_speaker else ""

    if speaker:
        return (f"{prefix} {speaker}" if use_prefix else speaker), TitleSource.FALLBACK_SPEAKER

    if use_prefix:
        title = prefix.rstrip()
        if title.lower().endswith(" by"):
            title = title[:-3].rstrip()
        return title, TitleSource.FALLBACK_TEMPLATE

    series = event.series.split(",")[0].strip()
    text = f"{A_AN_MARKER} {series} Talk" if series else "A Seminar Talk"
    return resolve_articles(" ".join(text.split())), TitleSource.FALLBACK_SERIES


def fill_titles(
    events: Sequence[Event], template: str = "", *, include_speaker: bool = True
) -> tuple[tuple[Event, ...], int]:
    """Return the events with every missing title synthesized, and how many were filled.

    Events are immutable, so this returns new ones rather than mutating in place. The
    predecessor mutates, which is why its stage ordering is load-bearing and undocumented.
    """
    from dataclasses import replace

    out: list[Event] = []
    filled = 0
    for event in events:
        if not is_missing(event.title):
            out.append(event)
            continue
        title, source = synthesize_title(event, template, include_speaker=include_speaker)
        out.append(
            replace(
                event,
                title=title,
                title_source=source.value,
                title_is_placeholder=True,
            )
        )
        filled += 1
    return tuple(out), filled


__all__ = [
    "A_AN_MARKER",
    "MAX_PREFIX_LEN",
    "SPEAKER_JOIN",
    "choose_article",
    "fill_titles",
    "render_prefix",
    "resolve_articles",
    "set_word_acronyms",
    "synthesize_title",
]
