"""Combined feeds: the set algebra, and the merge rule that refuses to guess.

The merge and divergence paths are exercised by constructed events rather than by the
committed feeds, because **nothing in today's real data merges**. That is the expected
outcome of the all-details-match rule, not a gap in the fixtures -- and it is exactly why
the paths need building explicitly, since production will rarely reach them.
"""

from __future__ import annotations

import pytest

from tests.support import FIXTURES, REPO_ROOT
from upcoming.build import build_from_file, load_pronunciation
from upcoming.combine import (
    Combo,
    combine,
    compare_form,
    details_digest,
    load_combos,
)
from upcoming.errors import ConfigFatal
from upcoming.model import Event, Location, Speaker
from upcoming.tags import load_tags

TAGS = load_tags(REPO_ROOT / "config" / "tags.yaml")


def event(source: str, guid: str, **overrides) -> Event:  # type: ignore[no-untyped-def]
    base = {
        "id": Event.make_id(source, guid),
        "guid": guid,
        "sources": (source,),
        "platform": "princeton-site-builder",
        "starts_at": "2026-10-02T16:15:00Z",
        "ends_at": "2026-10-02T17:15:00Z",
        "start_time": "2026-10-02T12:15:00",
        "end_time": "2026-10-02T13:15:00",
        "timezone": "America/New_York",
        "title": "Peculiarities of Heat-Driven and Light-Driven Evaporation",
        "url": "https://example.edu/events/evaporation",
    }
    base.update(overrides)
    return Event(**base)  # type: ignore[arg-type]


@pytest.fixture
def live(registry):  # type: ignore[no-untyped-def]
    load_pronunciation()
    out = {}
    for source in registry.live:
        result = build_from_file(FIXTURES / "feeds" / source.slug / "feed.ics", source)
        assert result.status == "ok", result.diagnostics
        out[source.slug] = result.events
    return out


@pytest.fixture
def combos(registry):  # type: ignore[no-untyped-def]
    return load_combos(
        REPO_ROOT / "config" / "combos.yaml",
        known_sources=[s.slug for s in registry.sources],
        tags=TAGS,
        purposes=tuple(registry.purposes),
    )


# --------------------------------------------------------------------------------------
# Merging -- only when every detail matches
# --------------------------------------------------------------------------------------


def test_an_identical_event_from_two_sources_becomes_one_record() -> None:
    """The collision becomes a fact the record carries, in `sources`.

    Safe by construction: every compared detail already agrees, so there is nothing to
    choose between and no winner to pick.
    """
    combo = Combo(name="c", include=("cee", "mae"), key=("starts_at", "title"))
    result = combine(
        combo,
        {
            "cee": [event("cee", "ps_events:2411:delta:0")],
            "mae": [event("mae", "ps_events:4846:delta:0")],
        },
    )

    assert len(result.events) == 1
    assert result.merged == 1
    assert result.events[0].sources == ("cee", "mae")
    assert result.divergences == ()


def test_a_merged_record_keeps_a_source_scoped_id() -> None:
    """`sources` grows; `id` does not become ambiguous."""
    combo = Combo(name="c", include=("cee", "mae"))
    result = combine(
        combo,
        {"cee": [event("cee", "g1")], "mae": [event("mae", "g2")]},
    )
    assert result.events[0].id.startswith(("cee:", "mae:"))
    assert len(result.events[0].sources) == 2


@pytest.mark.parametrize(
    "differing",
    [
        {"location": Location(name="Maeder Hall Auditorium")},
        {"title": "A Different Talk Entirely"},
        {"speakers": (Speaker("Someone Else"),)},
        {"url": "https://example.edu/events/other"},
    ],
)
def test_a_key_match_with_differing_details_emits_both(differing: dict) -> None:
    """Merging would mean picking a winner, and picking a winner means guessing.

    The real instance is cee and mae publishing the same talk with different locations --
    one says "Maeder Hall Auditorium", the other says "TBD".
    """
    combo = Combo(name="c", include=("cee", "mae"), key=("starts_at",))
    result = combine(
        combo,
        {
            "cee": [event("cee", "g1")],
            "mae": [event("mae", "g2", **differing)],
        },
    )

    assert len(result.events) == 2, "both records survive"
    assert result.merged == 0
    assert len(result.divergences) == 1
    assert result.divergences[0].differing


def test_events_differing_on_a_key_field_never_group_at_all() -> None:
    """Not a divergence -- they were never candidates.

    A divergence means "the key said these might be one event, and the details said no".
    Two events at different times were never the same event to begin with.
    """
    combo = Combo(name="c", include=("cee", "mae"), key=("starts_at",))
    result = combine(
        combo,
        {
            "cee": [event("cee", "g1")],
            "mae": [event("mae", "g2", starts_at="2026-10-03T16:15:00Z")],
        },
    )
    assert len(result.events) == 2
    assert result.divergences == ()


def test_a_divergence_names_the_fields_that_disagree() -> None:
    combo = Combo(name="c", include=("cee", "mae"), key=("starts_at",))
    result = combine(
        combo,
        {
            "cee": [event("cee", "g1")],
            "mae": [event("mae", "g2", location=Location(name="Somewhere Else"))],
        },
    )
    assert result.divergences[0].differing == ("location",)


def test_scrape_derived_fields_do_not_defeat_a_merge() -> None:
    """They depend on *when* each source's page was fetched.

    A banner rotating between two requests, or an editor's tweak, would otherwise split a
    pair that is plainly the same event.
    """
    combo = Combo(name="c", include=("a", "b"))
    result = combine(
        combo,
        {
            "a": [event("a", "g1", raw_details="<div>fetched at noon</div>", abstract="one")],
            "b": [event("b", "g2", raw_details="<div>fetched at one</div>", abstract="two")],
        },
    )
    assert result.merged == 1
    assert result.events[0].sources == ("a", "b")


def test_trivial_formatting_differences_do_not_defeat_a_merge() -> None:
    """Two departments typing the same title differently still merge."""
    combo = Combo(name="c", include=("a", "b"))
    result = combine(
        combo,
        {
            "a": [event("a", "g1", title="Winds, Waves — and Wakes")],
            "b": [event("b", "g2", title="winds,  waves - and   wakes")],
        },
    )
    assert result.merged == 1


def test_three_sources_carrying_one_event_merge_to_one_record() -> None:
    combo = Combo(name="c", include=("a", "b", "c"))
    result = combine(
        combo,
        {"a": [event("a", "g1")], "b": [event("b", "g2")], "c": [event("c", "g3")]},
    )
    assert len(result.events) == 1
    assert result.events[0].sources == ("a", "b", "c")
    assert result.merged == 2


def test_a_source_carrying_the_same_event_twice_is_not_silently_collapsed() -> None:
    """A per-source feed reproduces its upstream, duplicates included.

    ORFE really does publish one talk twice, as two Drupal nodes with different UIDs and
    punctuation. In a combined feed those merge only if every detail agrees -- and if they
    do, one record naming the source once is the honest result.
    """
    combo = Combo(name="c", include=("orfe",))
    result = combine(
        combo,
        {"orfe": [event("orfe", "g1"), event("orfe", "g2", title="Slightly Different")]},
    )
    assert len(result.events) == 2


# --------------------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------------------


def test_the_fpo_filter_works_across_every_upstream_spelling(live, combos) -> None:
    """The reason the canonical tag vocabulary exists.

    Three departments spell it four ways. The predecessor excludes the literal string
    "FPO", which matches nothing in MAE's feed, so the feed it advertises as filtered is
    identical to the unfiltered one.
    """
    everything = combine(next(c for c in combos if c.name == "all"), live)
    filtered = combine(next(c for c in combos if c.name == "all-no-fpo"), live)

    removed = len(everything.events) - len(filtered.events)
    assert removed == 10, f"expected the 10 FPO events to go, {removed} went"
    assert not any("fpo" in e.tags for e in filtered.events)

    # Anti-vacuity: the removed events really did come from more than one department.
    departments = {s for e in everything.events if "fpo" in e.tags for s in e.sources}
    assert len(departments) >= 3, f"only {departments} contributed FPO events"


def test_a_location_predicate_crosses_sources(live, combos) -> None:
    """Sherrerd Hall is where SHD is, and three sources schedule into it.

    Filtering on the resolved venue rather than on the source is what makes that work --
    a source's events are not all in one building.
    """
    result = combine(next(c for c in combos if c.name == "sherrerd-hall"), live)
    assert result.events
    assert all("sherrerd" in e.location.name.casefold() for e in result.events)
    assert len({s for e in result.events for s in e.sources}) >= 2


def test_an_include_list_restricts_to_those_sources(live, combos) -> None:
    result = combine(next(c for c in combos if c.name == "engineering"), live)
    named = {s for e in result.events for s in e.sources}
    assert named <= {"cbe", "cee", "ece", "mae", "bioengineering"}


def test_a_star_include_covers_every_built_source(live, combos) -> None:
    result = combine(next(c for c in combos if c.name == "all"), live)
    assert set(result.inputs) == set(live)
    assert len(result.events) == sum(len(v) for v in live.values())


def test_output_is_sorted_and_stable(live, combos) -> None:
    combo = next(c for c in combos if c.name == "all")
    first, second = combine(combo, live), combine(combo, live)
    assert [e.id for e in first.events] == [e.id for e in second.events]
    assert [e.sort_key() for e in first.events] == sorted(e.sort_key() for e in first.events)


# --------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------


def write(tmp_path, text: str):  # type: ignore[no-untyped-def]
    path = tmp_path / "combos.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_keying_on_a_bare_identifier_is_refused(tmp_path, registry) -> None:
    """The near miss that made this a hard error.

    An earlier draft assumed ps_events UIDs were globally unique. They are per-site
    sequences: ps_events:4056:delta:0 is ai's "ORFE Colloquium" and materials' "Materials
    Institute Symposium", two unrelated events. Keying on guid would have merged them.
    """
    path = write(tmp_path, "combos:\n  - name: c\n    include: ['*']\n    key: [guid]\n")
    with pytest.raises(ConfigFatal, match="per-site sequences"):
        load_combos(
            path,
            known_sources=[s.slug for s in registry.sources],
            tags=TAGS,
            purposes=tuple(registry.purposes),
        )


def test_an_unknown_source_is_refused(tmp_path, registry) -> None:
    """A combo over a source that does not exist silently produces fewer events."""
    path = write(tmp_path, "combos:\n  - name: c\n    include: [nosuchsource]\n")
    with pytest.raises(ConfigFatal, match="unknown source"):
        load_combos(
            path,
            known_sources=[s.slug for s in registry.sources],
            tags=TAGS,
            purposes=tuple(registry.purposes),
        )


def test_a_predicate_on_a_tag_nothing_produces_is_refused(tmp_path, registry) -> None:
    """It would filter to nothing and report success -- the predecessor's exact bug."""
    path = write(
        tmp_path,
        "combos:\n  - name: c\n    include: ['*']\n"
        "    exclude_where:\n      tags:\n        contains: FPO\n",
    )
    with pytest.raises(ConfigFatal, match="not a canonical tag"):
        load_combos(
            path,
            known_sources=[s.slug for s in registry.sources],
            tags=TAGS,
            purposes=tuple(registry.purposes),
        )


def test_an_unknown_key_field_is_refused(tmp_path, registry) -> None:
    path = write(tmp_path, "combos:\n  - name: c\n    include: ['*']\n    key: [startsat]\n")
    with pytest.raises(ConfigFatal, match="unknown field"):
        load_combos(
            path,
            known_sources=[s.slug for s in registry.sources],
            tags=TAGS,
            purposes=tuple(registry.purposes),
        )


def test_a_disabled_combo_must_record_why(tmp_path, registry) -> None:
    path = write(tmp_path, "combos:\n  - name: c\n    include: ['*']\n    enabled: false\n")
    with pytest.raises(ConfigFatal, match="records no reason"):
        load_combos(
            path,
            known_sources=[s.slug for s in registry.sources],
            tags=TAGS,
            purposes=tuple(registry.purposes),
        )


def test_the_committed_combos_load(combos) -> None:
    assert combos
    assert {c.name for c in combos} >= {"all", "sherrerd-hall", "all-no-fpo"}


def test_nextg_is_declared_but_disabled_with_its_reason(combos) -> None:
    """ece's feed has no NextG discriminator, so a predicate would be a guess."""
    nextg = next(c for c in combos if c.name == "nextg")
    assert nextg.enabled is False
    assert "no NextG discriminator" in " ".join(nextg.reason.split())


# --------------------------------------------------------------------------------------
# Comparison helpers
# --------------------------------------------------------------------------------------


def test_compare_form_folds_what_should_not_distinguish_two_events() -> None:
    assert compare_form("Winds, Waves — and Wakes") == compare_form("winds,  waves - and wakes")
    assert compare_form("a​b") == "ab", "zero-width characters are real in these feeds"


def test_compare_form_still_distinguishes_different_text() -> None:
    assert compare_form("Quantum Error Correction") != compare_form("Quantum Error Detection")


def test_the_digest_excludes_the_output_and_the_scrape() -> None:
    """`sources` is what a merge produces, so comparing it would be circular; scraped
    fields depend on fetch timing."""
    a = event("a", "g1", sources=("a",), raw_details="x", abstract="y", bio="z")
    b = event("b", "g2", sources=("b", "c"), raw_details="different", abstract="", bio="")
    assert details_digest(a) == details_digest(b)
