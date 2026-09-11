"""The canonical event: identity, ordering, and the wire contract.

The identity tests here are regression tests for a wrong assumption an earlier draft of
this design carried -- that ``ps_events`` UIDs are globally unique. They are not, and the
data that disproves it is reproduced in the fixtures below.
"""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from upcoming.model import (
    PLATFORM_SITE_BUILDER,
    WIRE_FIELDS,
    Event,
    Location,
    Speaker,
    normalize_instant,
    to_wire,
)
from upcoming.provenance import TitleSource


def make_event(**overrides: object) -> Event:
    base: dict[str, object] = {
        "id": "orfe:ps_events:1:delta:0",
        "guid": "ps_events:1:delta:0",
        "sources": ("orfe",),
        "platform": PLATFORM_SITE_BUILDER,
        "starts_at": "2026-09-15T20:30:00Z",
        "ends_at": "2026-09-15T21:30:00Z",
        "start_time": "2026-09-15T16:30:00",
        "end_time": "2026-09-15T17:30:00",
        "timezone": "America/New_York",
        "title": "An Optimization Seminar Talk",
        "url": "https://orfe.princeton.edu/events/2026/example",
    }
    base.update(overrides)
    return Event(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------


def test_the_id_namespaces_the_upstream_uid() -> None:
    assert Event.make_id("orfe", "ps_events:1:delta:0") == "orfe:ps_events:1:delta:0"


def test_the_same_uid_from_two_sources_yields_two_distinct_ids() -> None:
    """The regression test for the per-site-nid finding.

    Measured on the live feeds: ``ps_events:4056:delta:0`` is ai's "ORFE Talks & Seminars
    Flyers Fall 2026 Colloquium" *and* materials' "Princeton Materials Institute
    Symposium" -- two unrelated events, six months apart. 12 of 125 nids collide this way,
    because Site Builder nids are per-site sequences rather than a global namespace.

    Treating the shared UID as identity would merge them. This asserts the namespacing
    that prevents it.
    """
    ai = make_event(
        id=Event.make_id("ai", "ps_events:4056:delta:0"),
        guid="ps_events:4056:delta:0",
        sources=("ai",),
        title="ORFE Talks & Seminars Flyers Fall 2026 Colloquium",
        starts_at="2026-09-15T20:30:00Z",
    )
    materials = make_event(
        id=Event.make_id("materials", "ps_events:4056:delta:0"),
        guid="ps_events:4056:delta:0",
        sources=("materials",),
        title="Princeton Materials Institute Symposium",
        starts_at="2027-03-31T12:30:00Z",
    )

    assert ai.guid == materials.guid, "the fixture must reproduce the real UID collision"
    assert ai.id != materials.id
    assert len({ai.id, materials.id}) == 2


def test_source_is_available_for_a_single_source_event() -> None:
    assert make_event().source == "orfe"


def test_source_refuses_to_guess_for_a_merged_event() -> None:
    """In a combo there is no single source.

    Returning ``sources[0]`` would be a small lie that anything sorting or labelling by it
    would carry onward, so asking for one is an error instead.
    """
    merged = make_event(sources=("ai", "materials"))
    with pytest.raises(ValueError, match=r"use \.sources"):
        _ = merged.source


# --------------------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------------------


def test_equal_start_events_order_deterministically() -> None:
    """The tiebreaker is not optional.

    The predecessor sorts on start time alone with no tiebreaker, over a container the ICS
    library does not guarantee to be ordered. Several of these departments hold 4:30pm
    seminars, so equal-start events can emit in different orders between runs -- churning
    the published file, defeating the watchdog's byte-for-byte comparison, and defeating
    any hash-based skip gate.
    """
    same_time = "2026-09-15T20:30:00Z"
    first = make_event(id="orfe:ps_events:2:delta:0", starts_at=same_time)
    second = make_event(id="orfe:ps_events:10:delta:0", starts_at=same_time)

    forward = sorted([first, second], key=Event.sort_key)
    backward = sorted([second, first], key=Event.sort_key)

    assert [e.id for e in forward] == [e.id for e in backward]


def test_the_instant_field_is_normalized_to_utc() -> None:
    """``starts_at`` must be canonical UTC, because it is compared as a string.

    This is a real constraint the offset form would break. ``2026-09-15T16:30:00-04:00``
    and ``2026-09-15T15:30:00-05:00`` are the *same instant*, but as strings they differ
    and sort apart -- so two sources spelling one instant differently would order
    inconsistently and would fail an all-details-match comparison that should succeed.
    Normalizing on ingest makes string comparison and instant comparison the same
    operation, which is also what keeps output byte-stable.
    """
    assert normalize_instant("2026-09-15T16:30:00-04:00") == "2026-09-15T20:30:00Z"
    assert normalize_instant("2026-09-15T15:30:00-05:00") == "2026-09-15T20:30:00Z"
    # The feeds already publish UTC in this form; it must survive unchanged.
    assert normalize_instant("20260915T203000Z") == "2026-09-15T20:30:00Z"


def test_two_spellings_of_one_instant_compare_equal_after_normalization() -> None:
    a = make_event(id="a:1", starts_at=normalize_instant("2026-09-15T16:30:00-04:00"))
    b = make_event(id="b:1", starts_at=normalize_instant("2026-09-15T15:30:00-05:00"))
    assert a.starts_at == b.starts_at
    # Ordering then falls to the id tiebreaker, deterministically.
    assert sorted([b, a], key=Event.sort_key)[0].id == "a:1"


def test_a_naive_instant_is_refused() -> None:
    """A wall clock with no offset is not an instant.

    The predecessor's own time helper rejects a suffix on its local-time field for the
    mirror-image reason: quietly reading an ambiguous value as UTC would shift a window by
    four or five hours. Same principle, opposite field.
    """
    with pytest.raises(ValueError, match="offset"):
        normalize_instant("2026-09-15T16:30:00")


# --------------------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------------------


def test_an_unsplit_location_keeps_the_whole_string_as_the_venue() -> None:
    """Declining to guess.

    "Commons" and "Maeder Hall Auditorium" carry no room. The schema calls ``name`` the
    venue, so an unsplittable value belongs there entire -- putting it in ``detail`` is
    what the predecessor's dash strategy did to every MAE event.
    """
    location = Location(name="Maeder Hall Auditorium")
    assert location.name == "Maeder Hall Auditorium"
    assert location.detail == ""
    assert not location.is_empty


def test_an_absent_location_is_empty() -> None:
    """citp has one event with no LOCATION property, materials has two."""
    assert Location().is_empty


# --------------------------------------------------------------------------------------
# Wire contract
# --------------------------------------------------------------------------------------


def test_wire_keys_follow_the_declared_order_exactly() -> None:
    """Key order is declared, not derived from the dataclass.

    Reordering dataclass fields for readability must not change published bytes, so the
    wire order is asserted against its declaration rather than against itself.
    """
    wire = to_wire(make_event())
    assert list(wire) == [wire_key for wire_key, _ in WIRE_FIELDS]


def test_every_stored_field_has_a_wire_key() -> None:
    """A field added to the model without a wire key would silently never publish."""
    mapped = {attr for _, attr in WIRE_FIELDS}
    declared = {f.name for f in fields(Event)}
    assert not declared - mapped, f"stored but never published: {sorted(declared - mapped)}"


def test_every_wire_key_resolves_on_an_event() -> None:
    """The other direction: a typo'd attribute name would raise at serialization time.

    Cannot be an equality check against the dataclass fields, because `speaker` and
    `affiliation` are deliberately derived properties rather than stored state.
    """
    event = make_event()
    for wire_key, attr in WIRE_FIELDS:
        assert hasattr(event, attr), f"{wire_key} maps to missing attribute {attr!r}"


def test_the_wire_keys_beyond_the_stored_fields_are_derived_properties() -> None:
    """Anything published but not stored must be computed, never a second copy of state.

    Two copies of the speaker list is how they come to disagree.
    """
    extra = {attr for _, attr in WIRE_FIELDS} - {f.name for f in fields(Event)}
    assert extra == {"speaker", "affiliation"}
    for attr in extra:
        assert isinstance(getattr(Event, attr), property)


def test_sources_serializes_as_an_array_even_for_one_source() -> None:
    """The shape must not change between a per-source feed and a combo.

    A conditional shape forces ``Array.isArray(...) ? ... : [...]`` into every consumer,
    and the branch exercised least is the one that breaks in production.
    """
    single = to_wire(make_event())["sources"]
    merged = to_wire(make_event(sources=("ai", "materials")))["sources"]
    assert single == ["orfe"]
    assert merged == ["ai", "materials"]
    assert isinstance(single, list) and isinstance(merged, list)


def test_location_serializes_with_all_three_keys() -> None:
    wire = to_wire(make_event(location=Location(name="Sherrerd Hall", detail="125")))
    assert wire["location"] == {"name": "Sherrerd Hall", "id": "", "detail": "125"}


def test_the_feed_carries_no_wall_clock_of_this_run() -> None:
    """The property that makes byte-for-byte verification of the published feed possible.

    Every generated-at value belongs in the health manifest. A timestamp here would make
    the feed differ on every run, so the watchdog could never tell a real change from a
    rebuild.
    """
    wire = to_wire(make_event())
    forbidden = {"generatedAt", "generated_at", "updatedAt", "builtAt", "fetchedAt"}
    assert not forbidden & set(wire)


def test_the_wire_form_is_json_serializable() -> None:
    json.dumps(to_wire(make_event()))


def test_provenance_round_trips_through_the_wire_form() -> None:
    wire = to_wire(
        make_event(title_source=TitleSource.FALLBACK_SPEAKER.value, title_is_placeholder=True)
    )
    assert wire["titleSource"] == "fallback-speaker"
    assert wire["titleIsPlaceholder"] is True


def test_with_sources_preserves_everything_else() -> None:
    original = make_event(title="Winds, Waves, and Wakes")
    merged = original.with_sources(("cee", "mae"))
    assert merged.sources == ("cee", "mae")
    assert merged.title == original.title
    assert merged.id == original.id


# --------------------------------------------------------------------------------------
# Speakers
# --------------------------------------------------------------------------------------


def test_one_speaker_reads_as_a_scalar_for_the_existing_ingest() -> None:
    event = make_event(speakers=(Speaker("Elynn Chen", "New York University"),))
    assert event.speaker == "Elynn Chen"
    assert event.affiliation == "New York University"


def test_four_speakers_all_survive() -> None:
    """Measured: bioengineering's Rising Stars symposium lists four speakers.

    Flattening to one scalar would silently drop three people -- valid output, wrong, and
    nothing anywhere reporting it.
    """
    names = ["Jacqueline Bliley", "André Forjaz", "Helena Hu", "Felix Radford"]
    event = make_event(speakers=tuple(Speaker(n) for n in names))

    assert [s.name for s in event.speakers] == names
    assert event.speaker == "Jacqueline Bliley; André Forjaz; Helena Hu; Felix Radford"
    wire = to_wire(event)
    assert [s["name"] for s in wire["speakers"]] == names


def test_speakers_are_joined_with_a_semicolon_not_a_comma() -> None:
    """A comma is already taken.

    These feeds use a comma *within* one speaker to separate name from affiliation
    (``Elynn Chen, New York University``), so comma-joining several speakers would produce
    a string no consumer could tell from one speaker with two affiliations.
    """
    event = make_event(speakers=(Speaker("A Person"), Speaker("B Person")))
    assert event.speaker == "A Person; B Person"
    assert ", " not in event.speaker


def test_the_scalar_affiliation_is_empty_when_speakers_disagree() -> None:
    """There is no single answer, and picking the first would attribute one person's
    institution to everyone else on the panel."""
    event = make_event(
        speakers=(Speaker("A", "Princeton University"), Speaker("B", "MIT")),
    )
    assert event.affiliation == ""
    # The per-speaker truth is still available.
    assert [s.affiliation for s in event.speakers] == ["Princeton University", "MIT"]


def test_a_shared_affiliation_still_reads_as_a_scalar() -> None:
    event = make_event(speakers=(Speaker("A", "Princeton"), Speaker("B", "Princeton")))
    assert event.affiliation == "Princeton"


def test_no_speakers_yields_empty_scalars() -> None:
    """ORFE's FPO events and quantum's TBD entries both reach here."""
    event = make_event()
    assert event.speakers == ()
    assert event.speaker == ""
    assert event.affiliation == ""


def test_a_nameless_speaker_is_not_joined_into_the_scalar() -> None:
    """Guards against a stray empty scrape producing "A Person; " with a trailing joiner."""
    event = make_event(speakers=(Speaker("A Person"), Speaker("")))
    assert event.speaker == "A Person"


def test_the_derived_scalars_cannot_disagree_with_the_speakers() -> None:
    """They are properties, not stored fields, so there is no state to fall out of step.

    The predecessor stores a single scalar, which is why a second speaker had nowhere to go.
    """
    assert isinstance(type(make_event()).speaker, property)
    assert isinstance(type(make_event()).affiliation, property)


def test_speaker_display_pairs_name_with_affiliation() -> None:
    assert Speaker("Sean Roberts", "University of Texas at Austin").display == (
        "Sean Roberts, University of Texas at Austin"
    )
    assert Speaker("Sean Roberts").display == "Sean Roberts"


def test_speakers_serialize_as_objects_with_both_keys() -> None:
    wire = to_wire(make_event(speakers=(Speaker("A", "Princeton"),)))
    assert wire["speakers"] == [{"name": "A", "affiliation": "Princeton"}]
    # The scalars are published alongside, so the existing ingest needs no change.
    assert wire["speaker"] == "A"
    assert wire["affiliation"] == "Princeton"
