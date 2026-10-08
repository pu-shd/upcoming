"""The drupal-events-rss platform: Computer Science's RSS read as faithfully as ICS.

The fixture is the department's real feed, captured 2026-10-08. It is RSS, stored under
the same capture name every source uses (`feed.ics`), because that name means "the bytes
the upstream served" rather than a format.

Three properties matter, and each has a test group below:

* **Dates are read exactly or not at all.** `events:date` is wall-clock text with no zone,
  and the fixture spans the 1 November DST change, so a wrong reading is off by an hour on
  half the events and schema-valid on all of them.
* **The feed is capped at ten items and says nothing about it.** The published notes have to
  carry the horizon, or a consumer reads "no CS events after 10 November" as a fact.
* **Most of the feed belongs to other sources.** CITP and DaIS events are declined here,
  and the count is published, so the union neither double-lists them nor hides them.
"""

from __future__ import annotations

import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support import FIXTURES, TEMPLATES_YAML, TEST_ENV
from upcoming.build import build_from_text, load_pronunciation
from upcoming.errors import ConfigFatal, SourceFatal
from upcoming.registry import load_registry
from upcoming.rss import EVENTS_NS, parse_event_dates, parse_rss

FEED = (FIXTURES / "feeds" / "cs" / "feed.ics").read_text(encoding="utf-8")

CITP = "Center for Information Technology Policy"
DAIS = "Data and Intelligent Systems (DaIS)"


@pytest.fixture
def cs(registry):  # type: ignore[no-untyped-def]
    load_pronunciation()
    return registry.by_slug("cs")


def rss(*items: str) -> str:
    """A minimal feed in the department's shape, around the given ``<item>`` bodies."""
    body = "".join(f"<item>{item}</item>" for item in items)
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<rss version="2.0" xmlns:events="{EVENTS_NS}"><channel>'
        f"<title>Upcoming Department Events</title>{body}</channel></rss>"
    )


def item(
    title: str = "A Talk",
    date: str = "Tuesday, October 13, 2026 - 12:15 - Tuesday, October 13, 2026 - 13:15",
    *,
    link: str = "https://www.cs.princeton.edu/events/a-talk",
    extra: str = "",
) -> str:
    return f"<title>{title}</title><link>{link}</link><events:date>{date}</events:date>{extra}"


# --------------------------------------------------------------------------------------
# Faithfulness
# --------------------------------------------------------------------------------------


def test_every_item_survives_in_feed_order() -> None:
    events = parse_rss(FEED, "cs")
    assert len(events) == 10
    assert [e.ordinal for e in events] == list(range(10))
    assert events[0].summary == "Undergraduate & Faculty Mixer"  # XML entity decoded once
    assert events[-1].summary.startswith("CITP Seminar - Toward Social-Ecosystem")


def test_the_link_is_the_uid_because_drupal_emits_no_guid() -> None:
    events = parse_rss(FEED, "cs")
    assert "<guid>" not in FEED
    assert all(
        e.uid == e.url and e.uid.startswith("https://www.cs.princeton.edu/events/") for e in events
    )
    assert len({e.uid for e in events}) == 10


def test_a_guid_wins_over_the_link_when_one_is_present() -> None:
    (event,) = parse_rss(rss(item(extra="<guid>node/42</guid>")), "cs")
    assert event.uid == "node/42"
    assert event.url == "https://www.cs.princeton.edu/events/a-talk"


def test_the_namespace_is_matched_by_uri_not_by_prefix() -> None:
    """`events:` is only a local alias; a feed that renames it means the same thing."""
    renamed = (
        FEED.replace("xmlns:events=", "xmlns:ev=")
        .replace("<events:", "<ev:")
        .replace("</events:", "</ev:")
    )
    assert parse_rss(renamed, "cs") == parse_rss(FEED, "cs")


def test_every_events_field_is_kept_in_properties() -> None:
    """Nothing the feed said is lost, even the fields nothing maps yet."""
    first = parse_rss(FEED, "cs")[0]
    assert first.properties["EVENTS:TYPE"] == "Undergrad Event"
    assert first.properties["EVENTS:HOST"] == "Corina Hernandez"
    assert first.properties["EVENTS:HOSTORG"] == "Climate & Inclusion"


# --------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------


def test_a_range_becomes_two_floating_ics_values() -> None:
    assert parse_event_dates(
        "Thursday, October 8, 2026 - 16:15\n - Thursday, October 8, 2026 - 17:15\n",
        where="x",
        slug="cs",
    ) == ("20261008T161500", "20261008T171500")


def test_a_single_endpoint_has_no_end() -> None:
    assert parse_event_dates("Thursday, October 8, 2026 - 16:15", where="x", slug="cs") == (
        "20261008T161500",
        "",
    )


def test_an_all_day_event_is_a_date_with_no_time() -> None:
    assert parse_event_dates(
        "Monday, October 19, 2026 - Tuesday, October 20, 2026", where="x", slug="cs"
    ) == ("20261019", "20261020")


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        # The day name disagrees with the date: one half is wrong and nothing says which.
        ("Friday, October 8, 2026 - 16:15", "is a Thursday"),
        ("Thursday, October 8, 2026 - 4:15pm", "cannot read"),
        ("2026-10-08 16:15", "cannot read"),
        ("Monday, February 30, 2026 - 10:00", "not a calendar date"),
        ("Thursday, Octember 8, 2026 - 16:15", "not a month name"),
        ("Thursday, October 8, 2026 - 25:00", "not a time of day"),
        ("Thursday, October 8, 2026 - 16:15 - sometime later", "cannot read"),
    ],
)
def test_an_unreadable_date_fails_the_source_rather_than_the_event(
    text: str, complaint: str
) -> None:
    """Dropping one event quietly is how a feed comes to be short with nothing reporting it."""
    with pytest.raises(SourceFatal, match=complaint):
        parse_event_dates(text, where="item 3 (A Talk)", slug="cs")


def test_an_item_with_no_date_fails_and_is_named() -> None:
    with pytest.raises(SourceFatal, match=r"item 0 \(Undated\): has no events:date"):
        parse_rss(rss("<title>Undated</title><link>https://x/e</link>"), "cs")


def test_wall_times_are_local_on_both_sides_of_the_dst_change(cs) -> None:  # type: ignore[no-untyped-def]
    """The fixture spans 1 November. 16:15 is EDT in October and 12:00 is EST in November.

    Reading either in the wrong offset would publish a schema-valid event an hour off.
    """
    result = build_from_text(FEED, cs)
    assert result.ok, result.diagnostics
    by_title = {e.title: e for e in result.events}
    mixer = by_title["Undergraduate & Faculty Mixer"]
    assert (mixer.start_time, mixer.starts_at) == ("2026-10-08T16:15:00", "2026-10-08T20:15:00Z")
    psai = by_title["Princeton Societal AI (PSAI)"]
    assert (psai.start_time, psai.starts_at) == ("2026-11-02T12:00:00", "2026-11-02T17:00:00Z")
    assert (psai.end_time, psai.ends_at) == ("2026-11-02T18:30:00", "2026-11-02T23:30:00Z")


# --------------------------------------------------------------------------------------
# Not RSS at all
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "complaint"),
    [
        ("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", "not well-formed XML"),
        ("<html><body>Please log in</body></html>", "not RSS"),
        ('<feed xmlns="http://www.w3.org/2005/Atom"></feed>', "not RSS"),
    ],
)
def test_a_payload_that_is_not_rss_fails_the_source(cs, payload: str, complaint: str) -> None:  # type: ignore[no-untyped-def]
    """A login page served with 200 must not build as an empty feed.

    cs is declared allowed-empty, so an empty result would pass every gate.
    """
    with pytest.raises(SourceFatal, match=complaint):
        parse_rss(payload, "cs")
    result = build_from_text(payload, cs)
    assert result.status == "failed"
    assert complaint in result.diagnostics[0]


def test_an_ics_source_is_not_read_as_rss(registry) -> None:  # type: ignore[no-untyped-def]
    """The platform picks the parser; the payload's shape does not."""
    result = build_from_text(FEED, registry.by_slug("mae"))
    # Read as ICS, RSS has no VEVENTs, and mae expects events.
    assert result.status == "failed"


# --------------------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------------------


def test_the_host_organisation_is_the_series_not_a_category() -> None:
    """Group names are not topics. As categories they would surface as unmapped tags on
    every build, and a warning that always fires is a warning nobody reads."""
    events = parse_rss(FEED, "cs")
    assert events[0].series == "Climate & Inclusion"
    assert all(e.categories == () for e in events)


def test_the_published_feed_carries_no_unmapped_tags(cs) -> None:  # type: ignore[no-untyped-def]
    result = build_from_text(FEED, cs)
    assert all(e.series and not e.unmapped_tags for e in result.events)
    assert not any(n.startswith("unmapped_tags") for n in result.notes)


def test_structured_speakers_are_paired_with_their_organisations() -> None:
    by_title = {e.summary: e for e in parse_rss(FEED, "cs")}
    talk = by_title["CITP Seminar - Mathematical Communities in the Age of AI Generated Proof"]
    assert talk.speakers == (("Eamon Duede", "Purdue University"),)
    # A speaker with no organisation keeps an empty affiliation rather than borrowing one.
    care = next(e for e in by_title.values() if e.summary.startswith("CITP Seminar - From Code"))
    assert care.speakers == (("Shira Zilberstein", ""),)


def test_unpaired_organisations_are_dropped_rather_than_misattributed() -> None:
    extra = (
        "<events:speaker>Ada</events:speaker><events:speaker>Grace</events:speaker>"
        "<events:speakerOrg>Somewhere</events:speakerOrg>"
    )
    (event,) = parse_rss(rss(item(extra=extra)), "cs")
    assert event.speakers == (("Ada", ""), ("Grace", ""))


def test_structured_speakers_reach_the_event_when_the_summary_is_the_title(cs) -> None:  # type: ignore[no-untyped-def]
    extra = (
        "<events:speaker>Ada Lovelace</events:speaker>"
        "<events:speakerOrg>Analytical Engines</events:speakerOrg>"
    )
    result = build_from_text(rss(item("On Engines", extra=extra)), cs)
    assert result.ok, result.diagnostics
    (event,) = result.events
    assert event.title == "On Engines"
    assert [(s.name, s.affiliation) for s in event.speakers] == [
        ("Ada Lovelace", "Analytical Engines")
    ]
    # The scalar the campus ingest reads is derived from that pair, in its usual form.
    assert (event.speaker, event.affiliation) == (
        "Ada Lovelace, Analytical Engines",
        "Analytical Engines",
    )


# --------------------------------------------------------------------------------------
# Selection: what other sources already publish
# --------------------------------------------------------------------------------------


def test_citp_and_dais_events_are_declined_and_counted(cs) -> None:  # type: ignore[no-untyped-def]
    """6 of the 10 items are CITP's and one is DaIS's; both units publish them already.

    They are not merged instead, because they differ in detail -- CITP's own titles drop
    the "CITP Seminar - " prefix, and DaIS lists the symposium as all-day where CS gives
    09:00 -- so the union would keep both copies of each.
    """
    raw = parse_rss(FEED, "cs")
    assert sum(e.series == CITP for e in raw) == 6
    assert sum(e.series == DAIS for e in raw) == 1

    result = build_from_text(FEED, cs)
    assert result.ok, result.diagnostics
    assert result.counts["events"] == 3
    assert result.counts["declined"] == 7
    assert not {e.series for e in result.events} & {CITP, DAIS}
    declined = next(n for n in result.notes if n.startswith("declined:"))
    assert "7 event(s)" in declined and CITP in declined and DAIS in declined


# --------------------------------------------------------------------------------------
# The ten-item cap
# --------------------------------------------------------------------------------------


def test_a_full_page_publishes_its_horizon(cs) -> None:  # type: ignore[no-untyped-def]
    """Ten items back means there may be an eleventh. On capture day there was: a CITP
    seminar on 2026-11-17, listed on /events and absent from the feed."""
    result = build_from_text(FEED, cs)
    (horizon,) = [n for n in result.notes if n.startswith("horizon:")]
    assert "at most 10 events and returned 10" in horizon
    assert "(2026-11-10 12:15 local)" in horizon


def test_a_short_page_is_complete_and_says_nothing(cs) -> None:  # type: ignore[no-untyped-def]
    result = build_from_text(rss(item()), cs)
    assert result.ok, result.diagnostics
    assert not any(n.startswith(("horizon:", "upstream:")) for n in result.notes)


def test_the_horizon_is_the_last_start_not_the_last_listed(cs) -> None:  # type: ignore[no-untyped-def]
    """Feed order is the publisher's; the horizon is the latest start anywhere in it."""
    small = replace(cs, expectations=replace(cs.expectations, page_size=2))
    feed = rss(
        item("Later", "Friday, November 13, 2026 - 09:30", link="https://x/later"),
        item("Earlier", "Tuesday, October 13, 2026 - 12:15", link="https://x/earlier"),
    )
    (horizon,) = [n for n in build_from_text(feed, small).notes if n.startswith("horizon:")]
    assert "(2026-11-13 09:30 local)" in horizon


def test_an_empty_upstream_is_allowed_but_never_silent(cs) -> None:  # type: ignore[no-untyped-def]
    """cs may publish nothing after declining, but "the feed listed nothing" must say so:
    the two publish the same empty array."""
    result = build_from_text(rss(), cs)
    assert result.ok
    assert result.counts["events"] == 0
    assert "upstream: the feed listed no events at all." in result.notes


def test_an_upstream_fully_declined_is_empty_without_the_upstream_note(cs) -> None:  # type: ignore[no-untyped-def]
    citp_only = rss(item(extra=f"<events:hostOrg>{CITP}</events:hostOrg>"))
    result = build_from_text(citp_only, cs)
    assert result.ok and result.counts["events"] == 0
    assert not any(n.startswith("upstream:") for n in result.notes)
    assert any(n.startswith("declined: 1 event(s)") for n in result.notes)


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


def _registry_with(tmp_path: Path, source_yaml: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(
        TEMPLATES_YAML
        + textwrap.dedent(
            """
            defaults:
              location_rules: [whole]
            sources:
            """
        )
        + textwrap.indent(textwrap.dedent(source_yaml), "  "),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("value", [0, -1, "10", True, 2.5])
def test_page_size_must_be_a_positive_integer(tmp_path: Path, value: object) -> None:
    path = _registry_with(
        tmp_path,
        f"""
        - slug: alpha
          platform: drupal-events-rss
          feed_url: https://alpha.example.edu/feed.rss
          expectations:
            summary_role: title
            page_size: {value!r}
        """,
    )
    with pytest.raises(ConfigFatal, match="page_size must be a positive integer"):
        load_registry(path, env=TEST_ENV)


def test_an_unknown_platform_is_refused(tmp_path: Path) -> None:
    """The platform chooses the parser, so a typo would read RSS as ICS -- or the reverse."""
    path = _registry_with(
        tmp_path,
        """
        - slug: alpha
          platform: drupal-event-rss
          feed_url: https://alpha.example.edu/feed.rss
          expectations:
            summary_role: title
        """,
    )
    with pytest.raises(ConfigFatal, match="platform 'drupal-event-rss' is not one of"):
        load_registry(path, env=TEST_ENV)


def test_the_committed_cs_source_is_live_on_the_rss_platform(cs) -> None:  # type: ignore[no-untyped-def]
    assert cs.is_live
    assert cs.platform == "drupal-events-rss"
    assert cs.feed_url == "https://www.cs.princeton.edu/feeds/events.rss"
    assert cs.expectations.page_size == 10
    assert cs.uid_pattern is None


def test_cs_is_never_sent_the_site_builder_credential(cs) -> None:  # type: ignore[no-untyped-def]
    """The bypass header is for Site Builder event pages. cs scrapes nothing, and its RSS
    answers a plain request, so the credential has no business reaching that server."""
    secret_name = TEST_ENV["BOT_BYPASS_HEADER"].split(":", 1)[0]
    assert not cs.enrichment_enabled
    assert secret_name.lower() not in {h.lower() for h in cs.http.headers}
