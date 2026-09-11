"""Where a field's value came from, and whether it is real.

Ported from ``pu-orfe/upcoming``'s ``src/placeholders.py``, which is the best-factored
module in the predecessor: it has no intra-package dependencies and it reasons correctly
about what counts as a real title. Two changes here.

First, ``TBD`` is treated as absence. The measured data makes this load-bearing rather than
defensive: ``quantum`` carries a ``TBD`` title in 14 of its 18 events, and ``mae`` has one
event whose entire ``SUMMARY`` is the literal string ``TBD``. Publishing ``"TBD"`` as a
title satisfies the schema's ``minLength: 1`` and is wrong -- the same silent-success shape
as a transposed field, which is why it needs a sentinel rather than a length check.

Second, provenance is recorded per field rather than for the title alone, but it is only
*stamped* when the field being written is the field the flag describes. Scraping a value
into ``speaker`` says nothing about where the title came from, and marking it ``enriched``
would report a still-unknown title as real. That rule comes from ``mae-upcoming``'s
retargeting commit and it is the reason ``FieldSource`` is keyed by field name.
"""

from __future__ import annotations

from enum import StrEnum

#: Values a feed uses to mean "not yet decided". Compared casefolded and stripped.
#: Deliberately small: a value that silently becomes "no title" is as bad as a wrong title,
#: so this grows only with evidence from a real feed.
MISSING_TITLE_SENTINELS = frozenset({"tbd", "t.b.d.", "tba", "to be announced", "to be determined"})


class TitleSource(StrEnum):
    """How an event's title was arrived at.

    Published so a consumer can always distinguish a title the department wrote from one
    this pipeline synthesized. That distinction is the whole point: a synthesized title is
    useful for display and must never be mistaken for editorial content.
    """

    #: The feed's own SUMMARY carried a real title.
    ICS = "ics"
    #: Scraped from the event page.
    ENRICHED = "enriched"
    #: Synthesized from the speaker name.
    FALLBACK_SPEAKER = "fallback-speaker"
    #: Synthesized from the source's configured template.
    FALLBACK_TEMPLATE = "fallback-template"
    #: Last resort, synthesized from the series alone.
    FALLBACK_SERIES = "fallback-series"


#: Sources that mean "a human wrote this title".
REAL_TITLE_SOURCES = frozenset({TitleSource.ICS, TitleSource.ENRICHED})

#: Everything else is a placeholder this pipeline invented.
PLACEHOLDER_TITLE_SOURCES = frozenset(TitleSource) - REAL_TITLE_SOURCES


def is_missing(value: str | None) -> bool:
    """True when a feed supplied nothing usable for a field.

    Empty, whitespace-only, and the ``TBD`` family all count as missing. This is the check
    that keeps ``"TBD"`` out of the published title.
    """
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    return stripped.casefold() in MISSING_TITLE_SENTINELS


def is_placeholder_source(source: str | None) -> bool:
    """True when the title came from this pipeline rather than from the department."""
    if source is None:
        return True
    try:
        return TitleSource(source) not in REAL_TITLE_SOURCES
    except ValueError:
        # An unrecognized provenance value is not evidence the title is real.
        return True
