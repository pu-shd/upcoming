"""Enrichment: reading fields off event pages, and telling failure from silence.

The health tests here are the important ones. A scrape that reaches no pages and a scrape
that reaches every page and finds nothing produce identical output; only the recorded
outcome tells them apart, and the predecessor records neither.
"""

from __future__ import annotations

import pytest

from tests.fixture_transport import EMPTY_PAGE, BlockedTransport, FixtureTransport
from tests.support import FIXTURES
from upcoming.build import build_from_file, load_pronunciation
from upcoming.enrich import breaches
from upcoming.fetch import FetchOutcome, HttpTransport, PageCache
from upcoming.scrape import ScrapeStats, build_cache, rejected_by, select_all, select_first


def feed(slug: str):  # type: ignore[no-untyped-def]
    return FIXTURES / "feeds" / slug / "feed.ics"


def build(slug: str, registry, transport=None):  # type: ignore[no-untyped-def]
    load_pronunciation()
    source = registry.by_slug(slug)
    cache = build_cache(source, transport or FixtureTransport())
    return build_from_file(feed(slug), source, cache), cache


# --------------------------------------------------------------------------------------
# What enrichment adds
# --------------------------------------------------------------------------------------


def test_a_scraped_title_replaces_a_synthesized_one(registry) -> None:
    """ORFE's feed carries no titles; its pages do.

    Before enrichment all 14 are synthesized. The one page in the fixture set supplies a
    real title, and the event stops being a placeholder.
    """
    result, _ = build("orfe", registry)
    assert result.status == "ok"
    assert result.counts["enriched_title"] == 1

    enriched = [e for e in result.events if e.title_source == "enriched"]
    assert len(enriched) == 1
    event = enriched[0]
    assert event.title.startswith("Data-Driven Decision Making")
    assert event.title_is_placeholder is False


def test_both_of_maes_page_shapes_are_read(registry) -> None:
    """Its FPO pages carry a speaker field with a bare name; its seminar pages carry the
    subtitle with a name and an affiliation. Two selectors, tried in priority order."""
    result, _ = build("mae", registry)
    found = {e.speakers[0].name: e.speakers[0].affiliation for e in result.events if e.speakers}

    assert found["Jun Eshima"] == "", "the FPO page gives a bare name"
    assert found["Dr. Rebecca Ciez"] == "Purdue University", "the seminar page carries both"


def test_every_speaker_on_a_multi_speaker_page_survives(registry) -> None:
    """bioengineering's Rising Stars symposium lists four.

    Taking only the first would publish one and drop three, with schema-valid output and
    nothing reporting it.
    """
    result, _ = build("bioengineering", registry)
    symposium = next(e for e in result.events if len(e.speakers) > 1)
    assert [s.name for s in symposium.speakers] == [
        "Jacqueline Bliley",
        "André Forjaz",
        "Helena Hu",
        "Felix Radford",
    ]


def test_a_wrong_but_plausible_value_is_declined_and_counted(registry) -> None:
    """materials' subtitle reads "Hosted by Alice Kunin" -- a real person, correctly
    scraped, and the wrong one.

    Publishing it as the speaker would be schema-valid and false. The decline is counted,
    so a selector that always rejects surfaces rather than quietly yielding nothing.
    """
    result, _ = build("materials", registry)
    assert result.counts["rejected_speakers"] == 1
    assert result.counts.get("enriched_speakers", 0) == 0

    assert not any("Alice Kunin" in e.speaker for e in result.events)


def test_enrichment_never_overwrites_what_the_feed_supplied(registry) -> None:
    """The publisher's own structured data outranks anything read off a rendered page."""
    result, _ = build("materials", registry)
    from_feed = next(e for e in result.events if "Sean Roberts" in e.speaker)
    assert from_feed.speakers[0].affiliation == "University of Texas at Austin"


def test_provenance_is_only_claimed_for_the_field_it_describes(registry) -> None:
    """A value scraped into `speakers` says nothing about where the title came from."""
    result, _ = build("mae", registry)
    for event in result.events:
        if event.speakers:
            assert event.title_source != "enriched"


# --------------------------------------------------------------------------------------
# Telling failure from silence -- the point of the whole layer
# --------------------------------------------------------------------------------------


def test_a_blocked_source_fails_rather_than_publishing_nothing_scraped(registry) -> None:
    """Every page 403s, which is what these hosts do without the bypass credential.

    The predecessor reports this as `attempted=125 updated=0 errors=0` -- byte-identical to
    a clean run that found nothing -- and publishes a green feed with every enrichment
    empty. Here it fails the source and says why.
    """
    result, _ = build("orfe", registry, BlockedTransport())
    assert result.status == "failed"

    reason = result.diagnostics[0]
    assert "reached 0 of" in reason
    assert "bot challenge" in reason, "the message should name the likely cause"
    assert "bypass credential" in reason


def test_a_page_that_exists_but_has_nothing_is_not_a_failure(registry) -> None:
    """The distinction that error counting cannot make.

    Plenty of real event pages carry no abstract and no speaker field. A selector finding
    nothing there is an ordinary outcome and must not look like a blocked request.
    """
    transport = FixtureTransport(pages={}, missing="ok")
    result, _ = build("orfe", registry, transport)
    assert result.status == "ok"
    assert result.counts["enriched_title"] == 0


def test_the_success_rate_counts_pages_reached_not_values_found() -> None:
    stats = ScrapeStats(attempted=10, filled=1, selector_misses=9)
    assert stats.success_rate == 1.0, "nine empty pages is not a failure"

    stats = ScrapeStats(attempted=10, filled=0, http_errors=10)
    assert stats.success_rate == 0.0
    assert stats.total_failures == 10


def test_no_attempts_is_not_a_breach(registry) -> None:
    """A source whose events all lack a URL has nothing to report."""
    assert ScrapeStats().success_rate == 1.0
    assert breaches({"title": ScrapeStats()}, registry.by_slug("orfe")) == ()


def test_a_partial_outage_still_breaches(registry) -> None:
    """The floor is per source, and a source may declare a looser one."""
    source = registry.by_slug("orfe")
    half = ScrapeStats(attempted=10, http_errors=5, filled=5)
    problems = breaches({"title": half}, source)
    assert problems
    assert "50%" in problems[0]


def test_each_target_is_judged_separately(registry) -> None:
    """A source whose raw details scrape fine and whose speakers all fail has a real
    problem that a combined rate would average away."""
    source = registry.by_slug("orfe")
    problems = breaches(
        {
            "raw_details": ScrapeStats(attempted=10, filled=10),
            "title": ScrapeStats(attempted=10, http_errors=10),
        },
        source,
    )
    assert len(problems) == 1
    assert problems[0].startswith("title:")


# --------------------------------------------------------------------------------------
# The cache
# --------------------------------------------------------------------------------------


def test_each_page_is_fetched_once_however_many_fields_read_it(registry) -> None:
    """The predecessor issues a separate request per field against the same page, and the
    cache parameter that would prevent it is never passed by its caller."""
    result, cache = build("orfe", registry)
    assert result.status == "ok"

    urls = {e.url for e in result.events if e.url}
    assert cache.requests_made == len(urls)
    # orfe scrapes two fields per event, so without the cache this would be doubled.
    assert len(result.events) == 14


def test_a_failed_fetch_is_not_retried_within_a_run(registry) -> None:
    """A URL that 403s once will 403 again, and re-asking is ruder as well as slower."""
    transport = BlockedTransport()
    _result, _cache = build("orfe", registry, transport)
    assert len(transport.calls) == len(set(transport.calls))


def test_the_parsed_tree_is_reused() -> None:
    calls: list[str] = []

    def transport(url, *, headers, timeout):  # type: ignore[no-untyped-def]
        calls.append(url)
        return FetchOutcome("ok", url, code=200, body="<div class='a'>x</div>")

    cache = PageCache(transport=transport)
    first, second = cache.soup("https://example.test/p"), cache.soup("https://example.test/p")
    assert first is second
    assert len(calls) == 1


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------


def test_a_403_is_never_retried() -> None:
    """A bot challenge is not transient; retrying burns budget while the fix is a
    credential."""
    attempts: list[str] = []

    class Blocked:
        status_code = 403
        text = ""

    def fake_get(url, headers, timeout):  # type: ignore[no-untyped-def]
        attempts.append(url)
        return Blocked()

    transport = HttpTransport(retries=3, _sleep=lambda _s: None)
    import upcoming.fetch as fetch_module

    original = __import__("requests").get
    try:
        __import__("requests").get = fake_get
        outcome = transport("https://example.test/p", headers={}, timeout=(1, 1))
    finally:
        __import__("requests").get = original

    assert outcome.status == "http_error"
    assert outcome.code == 403
    assert len(attempts) == 1, "403 must not be retried"
    assert fetch_module is not None


def test_a_transient_status_is_retried() -> None:
    attempts: list[str] = []

    class Flaky:
        def __init__(self, code: int) -> None:
            self.status_code = code
            self.text = "body" if code == 200 else ""

    def fake_get(url, headers, timeout):  # type: ignore[no-untyped-def]
        attempts.append(url)
        return Flaky(200 if len(attempts) > 2 else 503)

    transport = HttpTransport(retries=3, _sleep=lambda _s: None)
    original = __import__("requests").get
    try:
        __import__("requests").get = fake_get
        outcome = transport("https://example.test/p", headers={}, timeout=(1, 1))
    finally:
        __import__("requests").get = original

    assert outcome.ok
    assert len(attempts) == 3


def test_an_ok_outcome_with_an_empty_body_is_still_ok() -> None:
    """The page existed. That is not the same as the request failing."""
    outcome = FetchOutcome("ok", "https://example.test/p", code=200, body="")
    assert outcome.ok
    assert not outcome.failed


# --------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------


def test_selectors_are_tried_in_the_order_given() -> None:
    """CSS resolves a comma group in document order, which would answer with whichever
    shape appears first in the markup rather than the one this source prefers."""
    from bs4 import BeautifulSoup

    # The *second* preference appears first in the document.
    soup = BeautifulSoup(
        "<div class='second'>wrong</div><div class='first'>right</div>", "html.parser"
    )
    assert select_first(soup, ["div.first", "div.second"]) == ("right", "div.first")
    assert select_first(soup, ["div.second", "div.first"]) == ("wrong", "div.second")


def test_a_malformed_selector_skips_to_the_next() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<div class='ok'>value</div>", "html.parser")
    assert select_first(soup, ["div[", "div.ok"]) == ("value", "div.ok")


def test_selecting_all_deduplicates_while_keeping_order() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<p class='s'>A</p><p class='s'>B</p><p class='s'>A</p>", "html.parser")
    values, _ = select_all(soup, ["p.s"])
    assert values == ["A", "B"]


def test_nothing_is_selected_from_a_page_that_failed_to_load() -> None:
    assert select_first(None, ["div.anything"]) is None
    assert select_all(None, ["div.anything"]) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Hosted by Alice Kunin", True),
        ("hosted by alice kunin", True),
        ("Organised by the Institute", True),
        ("Organized by the Institute", True),
        ("Sean Roberts", False),
        ("Alice Kunin", False),
    ],
)
def test_reject_patterns_are_case_insensitive_and_anchored(
    value: str, expected: bool, registry
) -> None:
    target = next(t for t in registry.by_slug("materials").enrich if t.field_name == "speakers")
    assert bool(rejected_by(value, target.reject_patterns)) is expected


def test_an_empty_page_yields_nothing_without_erroring() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(EMPTY_PAGE, "html.parser")
    assert select_first(soup, ["div.event-subtitle"]) is None
