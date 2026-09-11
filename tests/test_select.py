"""A source declining to publish part of its own upstream feed.

This changes what a per-source feed promises. Before it, every such feed was an
unconditional mirror of one upstream calendar; now it can be a *declared view* of one. The
difference matters enough that the count declined is published rather than left implicit --
a filtered feed and a feed whose upstream went quiet are identical from the outside, and
only one of them is somebody's decision.

ORFE is the case that drove it: the department does not list final public orals on its
upcoming-events display, and its predecessor pipeline has excluded them all along.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.support import FIXTURES, REPO_ROOT, TEST_ENV
from upcoming.build import build_events_with_stats, build_from_file, load_pronunciation, select
from upcoming.errors import ConfigFatal
from upcoming.registry import load_registry

ORFE_FEED = FIXTURES / "feeds" / "orfe" / "feed.ics"


@pytest.fixture
def orfe(registry):  # type: ignore[no-untyped-def]
    return next(s for s in registry.live if s.slug == "orfe")


def events_of(source):  # type: ignore[no-untyped-def]
    load_pronunciation()
    text = ORFE_FEED.read_text(encoding="utf-8")
    events, _stats, declined = build_events_with_stats(text, source)
    return events, declined


# --------------------------------------------------------------------------------------
# The predicate
# --------------------------------------------------------------------------------------


def test_a_source_with_no_predicate_mirrors_its_upstream(orfe):  # type: ignore[no-untyped-def]
    """The default has to stay an unconditional mirror.

    A feed that silently omits anything is a worse default than one that carries
    everything, because a consumer cannot tell by looking.
    """
    plain = replace(orfe, publish_where=None, publish_unless=None)
    assert plain.publishes_everything is True
    events, declined = events_of(plain)
    assert declined == 0
    assert len(events) == 14


def test_publish_unless_drops_only_what_it_names(orfe):  # type: ignore[no-untyped-def]
    filtered = replace(
        orfe, publish_unless={"tags": {"contains": "colloquium"}}, publish_where=None
    )
    everything, _ = events_of(replace(orfe, publish_where=None, publish_unless=None))
    kept, declined = events_of(filtered)
    assert declined == sum(1 for e in everything if "colloquium" in e.tags)
    assert declined > 0
    assert all("colloquium" not in e.tags for e in kept)
    assert len(kept) + declined == len(everything)


def test_publish_where_keeps_only_what_it_names(orfe):  # type: ignore[no-untyped-def]
    """The inclusive half, supported because the machinery is identical.

    A centre that wants to publish only its own seminar series out of a departmental feed
    needs this, and refusing it while accepting the exclusive form would be arbitrary.
    """
    only = replace(orfe, publish_where={"tags": {"contains": "seminar"}}, publish_unless=None)
    kept, declined = events_of(only)
    assert kept
    assert declined > 0
    assert all("seminar" in e.tags for e in kept)


def test_both_predicates_compose_as_an_intersection(orfe):  # type: ignore[no-untyped-def]
    both = replace(
        orfe,
        publish_where={"tags": {"contains": "seminar"}},
        publish_unless={"series": {"contains": "Wilks"}},
    )
    kept, _ = events_of(both)
    assert kept
    assert all("seminar" in e.tags and "Wilks" not in e.series for e in kept)


def test_select_is_a_pure_function_over_events(orfe):  # type: ignore[no-untyped-def]
    """Usable without running the whole pipeline, so the predicate can be reasoned about."""
    everything, _ = events_of(replace(orfe, publish_where=None, publish_unless=None))
    kept, declined = select(everything, replace(orfe, publish_unless={"tags": {"contains": "fpo"}}))
    assert len(kept) + declined == len(everything)


# --------------------------------------------------------------------------------------
# Saying what was declined
# --------------------------------------------------------------------------------------


def test_the_declined_count_is_published_not_implied(orfe):  # type: ignore[no-untyped-def]
    """ "We chose not to" must never read as "there was nothing there"."""
    filtered = replace(orfe, publish_unless={"tags": {"contains": "colloquium"}})
    result = build_from_file(ORFE_FEED, filtered)
    assert result.ok
    assert result.counts["declined"] > 0
    note = next(n for n in result.notes if n.startswith("declined:"))
    assert "publish_unless" in note
    assert "colloquium" in note


def test_an_unfiltered_source_says_nothing_about_declining(orfe):  # type: ignore[no-untyped-def]
    """A note on every feed would bury the ones that mean something."""
    result = build_from_file(ORFE_FEED, replace(orfe, publish_where=None, publish_unless=None))
    assert "declined" not in result.counts
    assert not [n for n in result.notes if n.startswith("declined:")]


def test_the_note_names_the_clause_that_did_it(orfe):  # type: ignore[no-untyped-def]
    """So "where did those events go" is answered in the manifest, not in git history."""
    only = replace(orfe, publish_where={"tags": {"contains": "seminar"}}, publish_unless=None)
    note = next(n for n in build_from_file(ORFE_FEED, only).notes if n.startswith("declined:"))
    assert "publish_where" in note


# --------------------------------------------------------------------------------------
# Where it sits in the pipeline
# --------------------------------------------------------------------------------------


def test_filtering_happens_before_enrichment(orfe):  # type: ignore[no-untyped-def]
    """An event we are not publishing must not cost somebody's web server a request.

    ORFE drops four final public orals, which is four fewer fetches every time the feed is
    built -- and at a 30-minute cadence that is nearly 200 requests a day not made.
    """
    from tests.fixture_transport import FixtureTransport
    from upcoming.scrape import build_cache

    counting = FixtureTransport()
    all_pages = build_cache(orfe, counting)
    build_from_file(ORFE_FEED, replace(orfe, publish_where=None, publish_unless=None), all_pages)
    full = len(counting.calls)

    counting_few = FixtureTransport()
    few_pages = build_cache(orfe, counting_few)
    build_from_file(
        ORFE_FEED, replace(orfe, publish_unless={"tags": {"contains": "colloquium"}}), few_pages
    )
    assert len(counting_few.calls) < full


def test_the_gates_measure_what_is_actually_published(orfe):  # type: ignore[no-untyped-def]
    """A gate reporting 23 events about a 19-event file would be describing nothing real."""
    filtered = replace(orfe, publish_unless={"tags": {"contains": "colloquium"}})
    result = build_from_file(ORFE_FEED, filtered)
    assert result.counts["events"] == len(result.events)


# --------------------------------------------------------------------------------------
# Refusal
# --------------------------------------------------------------------------------------


def bad_registry(tmp_path, predicate: dict):  # type: ignore[no-untyped-def]
    """The shipped registry with one predicate replaced, so only that is under test.

    Built from the real file rather than a minimal stub: the registry has a dozen other
    refusals, and a hand-written stub tests which of those fires first, not the predicate.
    """
    import yaml

    document = yaml.safe_load((REPO_ROOT / "config" / "sources.yaml").read_text())
    for entry in document["sources"]:
        if entry["slug"] == "orfe":
            entry["publish_unless"] = predicate
    target = tmp_path / "sources.yaml"
    target.write_text(yaml.safe_dump(document))
    return target


def test_a_predicate_on_an_unknown_tag_is_a_load_error(tmp_path):  # type: ignore[no-untyped-def]
    """The same refusal a combo gets, from the same validator.

    A predicate naming a tag nothing produces filters to nothing and reports success --
    which is exactly how a feed comes to be quietly empty while every check passes.
    """
    config = bad_registry(tmp_path, {"tags": {"contains": "finalpublicoral"}})
    with pytest.raises(ConfigFatal, match="not a canonical tag"):
        load_registry(config, env=TEST_ENV)


def test_a_predicate_with_an_unknown_operator_is_a_load_error(tmp_path):  # type: ignore[no-untyped-def]
    config = bad_registry(tmp_path, {"series": {"resembles": "FPO"}})
    with pytest.raises(ConfigFatal, match="unknown operator"):
        load_registry(config, env=TEST_ENV)


def test_the_error_names_the_source_and_the_clause(tmp_path):  # type: ignore[no-untyped-def]
    """A message naming neither leaves somebody grepping seventeen source blocks."""
    config = bad_registry(tmp_path, {"series": {"resembles": "FPO"}})
    with pytest.raises(ConfigFatal, match=r"orfe\.publish_unless"):
        load_registry(config, env=TEST_ENV)


def test_the_source_and_combo_predicates_share_one_vocabulary() -> None:
    """One dialect, so reading the combos docs teaches the source syntax and vice versa."""
    from upcoming import combine, predicate, registry

    assert predicate.validate is not None
    # Both call the same validator rather than each carrying a copy.
    assert combine.validate_predicate is predicate.validate
    assert registry.validate_predicate is predicate.validate


# --------------------------------------------------------------------------------------
# ORFE, as shipped
# --------------------------------------------------------------------------------------


def test_orfe_declines_final_public_orals_as_shipped(orfe) -> None:  # type: ignore[no-untyped-def]
    """The configuration this was built for.

    ORFE does not list final public orals on its upcoming-events display, and its
    predecessor pipeline has excluded them all along -- measured against its live feed, it
    publishes 19 of the 23 events its calendar carries.
    """
    assert orfe.publishes_everything is False
    assert orfe.publish_unless == {"tags": {"contains": "fpo"}}
    assert orfe.publish_where is None


def test_orfe_matches_on_the_tag_rather_than_the_series_string(orfe) -> None:  # type: ignore[no-untyped-def]
    """The tag survives a rename; the literal series string does not.

    ORFE has already renamed a series once, and that rename silently dropped four events
    from a filtered feed until the unmapped-tags gate caught it. Matching `fpo` means the
    four upstream spellings across three departments all resolve here.
    """
    assert "series" not in (orfe.publish_unless or {})
    tags, unmapped = orfe.tags.normalize(["FPO"])
    assert tags == ("fpo",) and unmapped == ()


def test_no_other_source_filters_its_own_feed(registry) -> None:  # type: ignore[no-untyped-def]
    """Every other feed stays an unconditional mirror.

    Filtering is a per-department editorial decision, so it must never arrive by default or
    by copy-paste; a source that grows one should show up in this test's diff.
    """
    filtering = [s.slug for s in registry.sources if not s.publishes_everything]
    assert filtering == ["orfe"]
