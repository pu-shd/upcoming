"""Not doing work twice: conditional requests, and reusing the last scrape.

Every event page was being refetched on every run -- measured against live data, about
4,100 requests a day to departmental web servers to be handed back markup that had not
changed. Both mechanisms here exist to stop that, and both have a cost that has to stay
visible: a conditional request can be answered wrongly by a broken cache, and a reused
scrape means an edited page takes time to appear.

`rebuild_after_hours` had been in config/sources.yaml since the first commit -- 24 by
default, 6 for exactly the two sources with a 30-minute cadence -- and nothing read it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from tests.fixture_transport import FixtureTransport
from tests.support import FIXTURES
from upcoming import upstream
from upcoming.build import build_from_file, load_pronunciation
from upcoming.fetch import FetchOutcome, Validators, fetch_feed, fetch_feeds
from upcoming.model import to_wire
from upcoming.scrape import build_cache

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
ORFE_FEED = FIXTURES / "feeds" / "orfe" / "feed.ics"


def ago(hours: float) -> str:
    return upstream.stamp_now(NOW - timedelta(hours=hours))


@dataclass
class Recorder:
    """Records every request and answers however the test says."""

    answer: object = None
    calls: list = field(default_factory=list)

    def __call__(self, url, *, headers, timeout):  # type: ignore[no-untyped-def]
        self.calls.append((url, dict(headers)))
        if callable(self.answer):
            return self.answer(url, headers)
        return self.answer or FetchOutcome("ok", url, code=200, body="BEGIN:VCALENDAR")


# --------------------------------------------------------------------------------------
# Conditional requests
# --------------------------------------------------------------------------------------


def test_validators_become_the_conditional_headers() -> None:
    held = Validators(etag='"abc"', last_modified="Fri, 11 Sep 2026 17:32:09 GMT")
    assert held.headers() == {
        "If-None-Match": '"abc"',
        "If-Modified-Since": "Fri, 11 Sep 2026 17:32:09 GMT",
    }


def test_a_missing_validator_is_omitted_rather_than_sent_empty() -> None:
    """An empty If-None-Match is not a weaker request, it is a malformed one."""
    assert Validators(etag='"abc"').headers() == {"If-None-Match": '"abc"'}
    assert Validators(last_modified="x").headers() == {"If-Modified-Since": "x"}
    assert Validators().headers() == {}
    assert Validators().empty is True


def test_a_conditional_request_is_only_sent_with_a_capture_to_validate(registry, tmp_path):  # type: ignore[no-untyped-def]
    """Asking "changed since?" with nothing on disk invites a 304 we cannot build from."""
    orfe = next(s for s in registry.live if s.slug == "orfe")
    recorder = Recorder()
    fetch_feeds([orfe], tmp_path, recorder, {"orfe": Validators(etag='"abc"')})
    _url, headers = recorder.calls[0]
    assert "If-None-Match" not in headers  # no capture existed yet

    recorder2 = Recorder()
    fetch_feeds([orfe], tmp_path, recorder2, {"orfe": Validators(etag='"abc"')})
    _url, headers2 = recorder2.calls[0]
    assert headers2["If-None-Match"] == '"abc"'  # now there is one


def test_the_sources_own_headers_win_a_collision(registry, tmp_path):  # type: ignore[no-untyped-def]
    """A hand-set conditional header in config would be deliberate."""
    orfe = next(s for s in registry.live if s.slug == "orfe")
    configured = replace(
        orfe, http=replace(orfe.http, headers={**orfe.http.headers, "If-None-Match": '"mine"'})
    )
    recorder = Recorder()
    fetch_feed(configured, recorder, Validators(etag='"theirs"'))
    assert recorder.calls[0][1]["If-None-Match"] == '"mine"'


def test_a_304_is_neither_ok_nor_failed() -> None:
    """Folding it into either is a bug with a specific consequence each way.

    Into `ok` and we would build a feed from an empty body. Into `failed` and a healthy
    source gets marked stale every time it answers correctly.
    """
    outcome = FetchOutcome("not_modified", "u", code=304)
    assert outcome.unchanged is True
    assert outcome.ok is False
    assert outcome.failed is False


def test_a_304_leaves_the_capture_alone(registry, tmp_path):  # type: ignore[no-untyped-def]
    """The server has just confirmed what is on disk is current."""
    orfe = next(s for s in registry.live if s.slug == "orfe")
    fetch_feeds([orfe], tmp_path, Recorder())
    before = (tmp_path / "orfe" / "feed.ics").read_bytes()

    unchanged = Recorder(answer=FetchOutcome("not_modified", "u", code=304, etag='"abc"'))
    outcomes = fetch_feeds([orfe], tmp_path, unchanged, {"orfe": Validators(etag='"abc"')})
    assert outcomes["orfe"].unchanged
    assert (tmp_path / "orfe" / "feed.ics").read_bytes() == before


def test_a_304_is_never_retried(registry) -> None:  # type: ignore[no-untyped-def]
    """Retrying a conditional request that answered correctly would be absurd."""
    import requests

    from upcoming.fetch import HttpTransport

    attempts = []

    class NotModified:
        status_code = 304
        text = ""
        headers: ClassVar[dict[str, str]] = {"ETag": '"abc"'}

    def fake_get(url, headers, timeout):  # type: ignore[no-untyped-def]
        attempts.append(url)
        return NotModified()

    transport = HttpTransport(retries=3, _sleep=lambda _s: None)
    original = requests.get
    requests.get = fake_get  # type: ignore[assignment]
    try:
        outcome = transport("https://x/f.ics", headers={}, timeout=(1.0, 1.0))
    finally:
        requests.get = original  # type: ignore[assignment]
    assert outcome.unchanged
    assert len(attempts) == 1


# --------------------------------------------------------------------------------------
# Remembering state between runs
# --------------------------------------------------------------------------------------


def test_state_round_trips(tmp_path) -> None:  # type: ignore[no-untyped-def]
    state = {"orfe": upstream.SourceState(etag='"a"', last_modified="x", last_enriched=ago(2))}
    (tmp_path / "state").mkdir()
    (tmp_path / upstream.UPSTREAM_PATH).write_text(
        upstream.document(state, generated_at="2026-09-11T12:00:00Z")
    )
    assert upstream.read(tmp_path) == state


def test_an_unreadable_state_file_is_treated_as_absent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The cost is one wasteful run. Refusing to publish would be an outage."""
    (tmp_path / "state").mkdir()
    (tmp_path / upstream.UPSTREAM_PATH).write_text("{ truncated")
    assert upstream.read(tmp_path) == {}
    assert upstream.read(tmp_path / "nowhere") == {}


def test_the_state_document_is_byte_stable_for_the_same_state(tmp_path) -> None:  # type: ignore[no-untyped-def]
    state = {"b": upstream.SourceState(etag='"b"'), "a": upstream.SourceState(etag='"a"')}
    first = upstream.document(state, generated_at="2026-09-11T12:00:00Z")
    again = upstream.document(
        dict(reversed(list(state.items()))), generated_at="2026-09-11T12:00:00Z"
    )
    assert first == again
    assert list(json.loads(first)["sources"]) == ["a", "b"]


def test_a_200_without_an_etag_does_not_erase_the_one_we_hold() -> None:
    """Some servers send Last-Modified only, or drop a header under load."""
    held = {"orfe": upstream.SourceState(etag='"keep"', last_modified="old")}
    advanced = upstream.advance(
        held, "orfe", outcome=FetchOutcome("ok", "u", code=200, last_modified="new")
    )
    assert advanced.etag == '"keep"'
    assert advanced.last_modified == "new"


def test_a_304_updates_the_validators_it_echoes() -> None:
    held = {"orfe": upstream.SourceState(etag='"old"')}
    advanced = upstream.advance(
        held, "orfe", outcome=FetchOutcome("not_modified", "u", code=304, etag='"new"')
    )
    assert advanced.etag == '"new"'


def test_a_failed_fetch_leaves_the_validators_untouched() -> None:
    """A 503 says nothing about whether the content changed."""
    held = {"orfe": upstream.SourceState(etag='"keep"', last_modified="keep")}
    advanced = upstream.advance(held, "orfe", outcome=FetchOutcome("http_error", "u", code=503))
    assert advanced.etag == '"keep"'
    assert advanced.last_modified == "keep"


def test_a_run_that_skipped_scraping_keeps_the_old_scrape_stamp() -> None:
    """Resetting it would mean the window never elapses and pages are never re-read."""
    held = {"orfe": upstream.SourceState(last_enriched=ago(5))}
    assert upstream.advance(held, "orfe", enriched_at=None).last_enriched == ago(5)
    fresh = upstream.stamp_now(NOW)
    assert upstream.advance(held, "orfe", enriched_at=fresh).last_enriched == fresh


# --------------------------------------------------------------------------------------
# The enrichment window
# --------------------------------------------------------------------------------------


def test_a_source_never_scraped_is_due(registry) -> None:  # type: ignore[no-untyped-def]
    orfe = next(s for s in registry.live if s.slug == "orfe")
    due, why = upstream.due_for_enrichment(orfe, {}, now=NOW)
    assert due is True
    assert why == "never scraped"


@pytest.mark.parametrize("hours", [0, 1, 5.9])
def test_inside_the_window_the_previous_scrape_is_reused(registry, hours) -> None:  # type: ignore[no-untyped-def]
    orfe = next(s for s in registry.live if s.slug == "orfe")
    assert orfe.rebuild_after_hours == 6
    state = {"orfe": upstream.SourceState(last_enriched=ago(hours))}
    assert upstream.due_for_enrichment(orfe, state, now=NOW)[0] is False


@pytest.mark.parametrize("hours", [6, 7, 40])
def test_past_the_window_the_pages_are_read_again(registry, hours) -> None:  # type: ignore[no-untyped-def]
    orfe = next(s for s in registry.live if s.slug == "orfe")
    state = {"orfe": upstream.SourceState(last_enriched=ago(hours))}
    assert upstream.due_for_enrichment(orfe, state, now=NOW)[0] is True


def test_the_window_is_per_source(registry) -> None:  # type: ignore[no-untyped-def]
    """The two 30-minute sources declare 6 hours; everything else inherits 24.

    A single global window would sit wrong for both ends: six hours is needlessly eager for
    cee, which published one event this term, and a day is too slow for the feeds that
    change most.
    """
    windows = {s.slug: s.rebuild_after_hours for s in registry.live}
    assert windows["orfe"] == 6
    assert windows["citp"] == 6
    assert windows["cee"] == 24
    state = {slug: upstream.SourceState(last_enriched=ago(8)) for slug in windows}
    due = {s.slug for s in registry.live if upstream.due_for_enrichment(s, state, now=NOW)[0]}
    assert "orfe" in due and "citp" in due
    assert "cee" not in due


def test_a_source_with_no_targets_is_never_due(registry) -> None:  # type: ignore[no-untyped-def]
    orfe = next(s for s in registry.live if s.slug == "orfe")
    due, why = upstream.due_for_enrichment(replace(orfe, enrich=()), {}, now=NOW)
    assert due is False
    assert "no enrichment targets" in why


def test_a_future_scrape_stamp_means_scrape_rather_than_wait(registry) -> None:  # type: ignore[no-untyped-def]
    """A clock this wrong would otherwise park a source's enrichment indefinitely."""
    orfe = next(s for s in registry.live if s.slug == "orfe")
    state = {"orfe": upstream.SourceState(last_enriched=ago(-48))}
    due, why = upstream.due_for_enrichment(orfe, state, now=NOW)
    assert due is True
    assert "future" in why


# --------------------------------------------------------------------------------------
# Reuse produces the same feed
# --------------------------------------------------------------------------------------


@dataclass
class PageCounter:
    """Counts page requests, answering from the committed captures.

    Backed by the real fixtures rather than a blank page, so enrichment actually writes
    something -- a stub returning empty markup would make every assertion here pass for the
    wrong reason, since carrying nothing forward is trivially identical to scraping nothing.
    """

    calls: list = field(default_factory=list)
    _pages: FixtureTransport = field(default_factory=FixtureTransport)

    def __call__(self, url, *, headers, timeout):  # type: ignore[no-untyped-def]
        self.calls.append(url)
        return self._pages(url, headers=headers, timeout=timeout)


@pytest.fixture
def orfe(registry):  # type: ignore[no-untyped-def]
    load_pronunciation()
    return next(s for s in registry.live if s.slug == "orfe")


def test_a_warm_run_fetches_nothing_and_publishes_the_same_bytes(orfe) -> None:  # type: ignore[no-untyped-def]
    """The property that makes the whole optimization safe.

    If reuse changed the output, drift detection would report a change on every run and
    the saving would have cost the thing it was protecting.
    """
    cold_pages = PageCounter()
    cold = build_from_file(ORFE_FEED, orfe, build_cache(orfe, cold_pages))
    assert len(cold_pages.calls) == len(cold.events)

    warm_pages = PageCounter()
    warm = build_from_file(
        ORFE_FEED, orfe, build_cache(orfe, warm_pages), held={e.guid: e for e in cold.events}
    )
    assert warm_pages.calls == []
    assert [to_wire(e) for e in warm.events] == [to_wire(e) for e in cold.events]


def test_an_event_new_since_the_last_scrape_is_still_fetched(orfe) -> None:  # type: ignore[no-untyped-def]
    """The case that makes an all-or-nothing window wrong.

    A seminar added this morning would otherwise publish untitled until the window turned
    over, which is hours of a real event looking like a placeholder.
    """
    cold = build_from_file(ORFE_FEED, orfe, build_cache(orfe, PageCounter()))
    partial = {e.guid: e for e in cold.events[:-3]}

    pages = PageCounter()
    warm = build_from_file(ORFE_FEED, orfe, build_cache(orfe, pages), held=partial)
    assert len(pages.calls) == 3
    assert [to_wire(e) for e in warm.events] == [to_wire(e) for e in cold.events]


def test_reuse_carries_the_provenance_not_just_the_value(orfe) -> None:  # type: ignore[no-untyped-def]
    """Copying a title without its titleSource would republish it as synthesized.

    And because the feeds are compared byte-for-byte, that disagreement would read as a
    change on every single run.
    """
    from upcoming.provenance import TitleSource

    cold = build_from_file(ORFE_FEED, orfe, build_cache(orfe, PageCounter()))
    enriched = [e for e in cold.events if e.title_source == TitleSource.ENRICHED.value]
    assert enriched, "the fixture must contain at least one enriched title"

    warm = build_from_file(
        ORFE_FEED, orfe, build_cache(orfe, PageCounter()), held={e.guid: e for e in cold.events}
    )
    by_guid = {e.guid: e for e in warm.events}
    for event in enriched:
        carried = by_guid[event.guid]
        assert carried.title == event.title
        assert carried.title_source == TitleSource.ENRICHED.value
        assert carried.title_is_placeholder is False


def test_reuse_is_counted_so_a_quiet_run_is_distinguishable(orfe) -> None:  # type: ignore[no-untyped-def]
    """A run that fetched nothing and one that fetched everything and found nothing
    produce the same `enriched_` count, and only one is working as designed."""
    cold = build_from_file(ORFE_FEED, orfe, build_cache(orfe, PageCounter()))
    warm = build_from_file(
        ORFE_FEED, orfe, build_cache(orfe, PageCounter()), held={e.guid: e for e in cold.events}
    )
    carried = {k: v for k, v in warm.counts.items() if k.startswith("carried_")}
    assert carried
    assert all(v == len(cold.events) for v in carried.values())


def test_the_health_gate_does_not_false_alarm_on_a_warm_run(orfe) -> None:  # type: ignore[no-untyped-def]
    """Nothing was attempted, so there is no scrape success rate to breach.

    A total blackout still trips it, because a blackout attempts and fails.
    """
    cold = build_from_file(ORFE_FEED, orfe, build_cache(orfe, PageCounter()))
    warm = build_from_file(
        ORFE_FEED, orfe, build_cache(orfe, PageCounter()), held={e.guid: e for e in cold.events}
    )
    assert warm.ok
    assert warm.status == "ok"
