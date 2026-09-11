"""ICS decoding: faithfulness, order, and the escapes.

The theme here is that the parse layer must not lose anything. Every test below defends a
specific way the predecessor's parse path does.
"""

from __future__ import annotations

import pytest

from tests.support import FIXTURES
from upcoming.parse import parse_ics, prodid, split_list, unescape_text, unfold


def feed(slug: str) -> str:
    return (FIXTURES / "feeds" / slug / "feed.ics").read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# Faithfulness
# --------------------------------------------------------------------------------------


def test_every_vevent_survives() -> None:
    """The counts are the point.

    ORFE's feed carries 14 VEVENTs and the predecessor's golden output has 13 -- the
    missing one is real, present, and parses fine. Suppressing an event here would
    silently edit a publisher's data.
    """
    assert len(parse_ics(feed("orfe"))) == 14
    assert len(parse_ics(feed("mae"))) == 9


def test_a_real_duplicate_listing_survives_intact() -> None:
    """ORFE's feed carries the same talk twice, as two separate Drupal nodes.

    `ps_events:11931` and `ps_events:11941` are the same speaker at the same instant, with
    the second a slightly sloppier copy -- "North Carolina Chapel Hill" without the comma,
    and the location typo "101 - Sherrerd Hal". Distinct UIDs, distinct URLs.

    Both belong in a faithful per-source feed: suppressing one would silently edit a
    publisher's data, and the two differ in ways no rule could safely reconcile.
    De-duplicating is the consumer's decision, and combined feeds make it explicitly.
    """
    events = {e.uid: e for e in parse_ics(feed("orfe"))}
    first = events["ps_events:11931:delta:0"]
    second = events["ps_events:11941:delta:0"]

    # The same event, by any human reading.
    assert first.dtstart == second.dtstart
    assert first.summary.startswith("Benjamin Zhang")
    assert second.summary.startswith("Benjamin Zhang")

    # And yet nothing the pipeline could key on agrees.
    assert first.uid != second.uid
    assert first.url != second.url
    assert first.summary != second.summary
    assert first.location == "101 - Sherrerd Hall"
    assert second.location == "101 - Sherrerd Hal"


def test_feed_order_is_preserved_as_an_ordinal() -> None:
    """A stable tiebreaker that depends on no field's value.

    The predecessor reads events out of a `set` and recovers an order by sorting on start
    time alone, so the pair above is free to swap between runs -- which churns the
    published file and defeats byte-for-byte verification of the served feed.
    """
    events = parse_ics(feed("orfe"))
    assert [e.ordinal for e in events] == list(range(14))
    # And the ordinals follow the file, not the clock.
    raw = feed("orfe")
    positions = [raw.index(e.uid) for e in events]
    assert positions == sorted(positions)


def test_parsing_is_repeatable() -> None:
    first = parse_ics(feed("orfe"))
    second = parse_ics(feed("orfe"))
    assert [e.uid for e in first] == [e.uid for e in second]


# --------------------------------------------------------------------------------------
# Folding and escapes
# --------------------------------------------------------------------------------------


def test_folded_lines_are_unfolded_before_anything_else() -> None:
    """Fail to unfold and every long DESCRIPTION truncates mid-word.

    Per RFC 5545 3.1 a fold is CRLF followed by **one** linear whitespace character, and
    unfolding removes both. So the whitespace is not a word separator -- a publisher
    folding mid-word gets the word back intact, and one folding between words has already
    put a space before the fold.
    """
    assert unfold("DESCRIPTION:one\r\n two") == "DESCRIPTION:onetwo"
    assert unfold("DESCRIPTION:one\n\ttwo") == "DESCRIPTION:onetwo"
    # A bare newline with no leading whitespace is a real line break, not a fold.
    assert unfold("A:one\r\nB:two") == "A:one\r\nB:two"


def test_a_long_description_arrives_whole() -> None:
    """ORFE's abstracts are folded across many lines in the real feed."""
    events = {e.uid: e for e in parse_ics(feed("orfe"))}
    description = events["ps_events:11876:delta:0"].description
    assert len(description) > 400, "the abstract should not be truncated at a fold"
    assert "\n " not in description, "fold markers should be gone"


@pytest.mark.parametrize(
    "encoded,decoded",
    [
        (r"Elynn Chen\, New York University", "Elynn Chen, New York University"),
        (r"Winds\, Waves\, and Wakes", "Winds, Waves, and Wakes"),
        (r"a\;b", "a;b"),
        (r"line\nbreak", "line\nbreak"),
        (r"line\Nbreak", "line\nbreak"),
        # A literal backslash followed by a comma: the backslash is data.
        (r"path\\, then", "path\\, then"),
        ("nothing to do", "nothing to do"),
        ("", ""),
    ],
)
def test_text_escapes_decode(encoded: str, decoded: str) -> None:
    assert unescape_text(encoded) == decoded


def test_an_unknown_escape_keeps_its_backslash() -> None:
    """An unrecognized escape is data, not a directive.

    Dropping the character would quietly discard something the publisher sent.
    """
    assert unescape_text(r"50\% done") == r"50\% done"


def test_the_model_carries_clean_text_not_ics_escaping() -> None:
    """Escaping is a wire-format concern, applied when writing.

    Keeping it in the model is why the predecessor needed one per-source boolean governing
    both a speaker name (where re-escaping is right) and a talk title (where it mangles
    "Winds, Waves, and Wakes").
    """
    summaries = [e.summary for e in parse_ics(feed("orfe"))]
    assert any("," in s for s in summaries), "the fixture should carry commas"
    assert not any("\\," in s for s in summaries)


# --------------------------------------------------------------------------------------
# List values
# --------------------------------------------------------------------------------------


def test_categories_split_on_unescaped_commas_only() -> None:
    """ECE really does publish two categories on one line."""
    assert split_list("Final Public Oral Examinations,Final Public Orals") == (
        "Final Public Oral Examinations",
        "Final Public Orals",
    )


def test_an_escaped_comma_does_not_split_a_category() -> None:
    """A plain str.split(',') cannot tell these apart."""
    assert split_list(r"S. S. Wilks\, Memorial") == ("S. S. Wilks, Memorial",)


def test_empty_and_blank_list_values_yield_nothing() -> None:
    assert split_list("") == ()
    assert split_list("  ,  ") == ()


def test_real_categories_parse_as_one_value_each() -> None:
    for slug in ("orfe", "mae"):
        for event in parse_ics(feed(slug)):
            for category in event.categories:
                assert category == category.strip()
                assert category, "a blank category should have been dropped"


# --------------------------------------------------------------------------------------
# Platform identity
# --------------------------------------------------------------------------------------


def test_prodid_is_readable() -> None:
    """Checked rather than assumed: kellercenter serves a Drupal iCal feed from a URL
    that looks like every other one."""
    assert prodid(feed("orfe")) == "princeton-site-builder"
    assert prodid(feed("mae")) == "princeton-site-builder"


def test_a_feed_without_prodid_reports_empty() -> None:
    assert prodid("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n") == ""


# --------------------------------------------------------------------------------------
# Degenerate input
# --------------------------------------------------------------------------------------


def test_an_empty_calendar_parses_to_no_events() -> None:
    """kellercenter's live feed is exactly this: well-formed, zero events.

    It must parse cleanly; whether zero events is acceptable is the source's declared
    `allow_empty`, decided in config rather than inferred here.
    """
    assert (
        parse_ics(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Drupal iCal API//EN\r\nEND:VCALENDAR\r\n"
        )
        == ()
    )


def test_an_event_missing_dtstart_parses_without_raising() -> None:
    """The predecessor crashes here.

    Its sort key is `e.begin or ""`, which compares an Arrow to a str and raises
    TypeError. A malformed event should be attributable, not fatal to the whole source.
    """
    events = parse_ics(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nSUMMARY:No times\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    assert len(events) == 1
    assert events[0].has_times is False
    assert events[0].summary == "No times"


def test_dtstart_parameters_are_retained() -> None:
    """A TZID parameter is the only thing distinguishing a local time from UTC."""
    events = parse_ics(
        "BEGIN:VEVENT\r\nUID:x\r\nDTSTART;TZID=America/New_York:20260915T163000\r\nEND:VEVENT\r\n"
    )
    assert events[0].dtstart == "20260915T163000"
    assert "TZID=America/New_York" in events[0].dtstart_params


def test_unknown_properties_are_kept_for_sources_that_map_them() -> None:
    events = parse_ics("BEGIN:VEVENT\r\nUID:x\r\nX-CUSTOM-THING:kept\r\nEND:VEVENT\r\n")
    assert events[0].properties["X-CUSTOM-THING"] == "kept"


def test_property_names_are_case_insensitive() -> None:
    events = parse_ics("BEGIN:VEVENT\r\nuid:x\r\nsummary:Lower\r\nEND:VEVENT\r\n")
    assert events[0].uid == "x"
    assert events[0].summary == "Lower"
