"""Placeholder detection: keeping ``TBD`` out of the published title.

Measured incidence, which is why this is core rather than defensive:

* ``quantum`` -- 14 of 18 events carry a ``TBD`` title
  (``Princeton Quantum Colloquium: TBD\\, Francesca Ferlaino (University of Innsbruck)``)
* ``mae`` -- one event's entire ``SUMMARY`` is the literal string ``TBD``, and one has
  ``LOCATION:TBD``
* ``orfe`` -- no event carries a title at all, since its ``SUMMARY`` is the speaker
"""

from __future__ import annotations

import pytest

from upcoming.provenance import (
    PLACEHOLDER_TITLE_SOURCES,
    REAL_TITLE_SOURCES,
    TitleSource,
    is_missing,
    is_placeholder_source,
)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "\n\t ",
        None,
        "TBD",
        "tbd",
        " TBD ",
        "T.B.D.",
        "TBA",
        "To Be Announced",
        "to be determined",
    ],
)
def test_absent_and_sentinel_values_are_missing(value: str | None) -> None:
    """``TBD`` is absence, not a title.

    Publishing the string ``"TBD"`` satisfies the schema's ``minLength: 1`` and is wrong --
    the same silent-success shape as a transposed field, which is why this needs a sentinel
    rather than a length check.
    """
    assert is_missing(value)


@pytest.mark.parametrize(
    "value",
    [
        "Winds, Waves, and Wakes",
        "AI Agents and the Augmentation Agenda",
        # A real title that merely mentions the sentinel must survive.
        "Deciding What Is TBD in Federated Systems",
        "TBD Revisited: A Retrospective",
        "0",
    ],
)
def test_real_titles_are_not_missing(value: str) -> None:
    """The sentinel matches the whole value, never a substring.

    A substring match would silently delete the title of any talk whose subject happens to
    include the word.
    """
    assert not is_missing(value)


def test_the_sentinel_set_stays_small() -> None:
    """It grows only with evidence from a real feed.

    A value that silently becomes "no title" is as damaging as a wrong title, so this is
    deliberately not a generous list.
    """
    from upcoming.provenance import MISSING_TITLE_SENTINELS

    assert len(MISSING_TITLE_SENTINELS) <= 8


def test_only_the_feed_and_the_page_count_as_real() -> None:
    """Everything else is something this pipeline invented."""
    assert {TitleSource.ICS, TitleSource.ENRICHED} == REAL_TITLE_SOURCES


def test_the_two_source_sets_partition_the_enum() -> None:
    """No value can be neither real nor placeholder, and none can be both.

    A new enum member that fell outside both sets would be treated as real by omission,
    which is the wrong default.
    """
    assert set(TitleSource) == REAL_TITLE_SOURCES | PLACEHOLDER_TITLE_SOURCES
    assert not REAL_TITLE_SOURCES & PLACEHOLDER_TITLE_SOURCES


@pytest.mark.parametrize(
    "source,expected",
    [
        ("ics", False),
        ("enriched", False),
        ("fallback-speaker", True),
        ("fallback-template", True),
        ("fallback-series", True),
    ],
)
def test_placeholder_classification(source: str, expected: bool) -> None:
    assert is_placeholder_source(source) is expected


def test_an_unknown_or_absent_provenance_is_not_evidence_of_a_real_title() -> None:
    """Defaulting the other way would report a synthesized title as editorial content."""
    assert is_placeholder_source(None) is True
    assert is_placeholder_source("something-new") is True


def test_every_title_source_has_a_stable_wire_value() -> None:
    """These strings are published and consumed downstream, so they are a contract."""
    assert {s.value for s in TitleSource} == {
        "ics",
        "enriched",
        "fallback-speaker",
        "fallback-template",
        "fallback-series",
    }
