"""The location chain, against every LOCATION value measured on the 11 live feeds.

Table-driven, because the failure mode is quiet: a mis-split location is schema-valid and
looks plausible. The negative tests at the bottom record what the *wrong* rule produces,
so nobody simplifies a rule away on the grounds that another one looks equivalent.
"""

from __future__ import annotations

import pytest

from upcoming.locate import (
    DECLINED,
    MUST_PRECEDE_UNAMBIGUOUS,
    RULES,
    parse_location,
    rule_detail_dash_name,
    rule_unambiguous_room,
    unknown_rules,
)

#: The full chain, in the order a source would use it. `config/sources.yaml` gives each
#: source a subset; this exercises every rule together.
FULL = (
    "sentinel",
    "detail_dash_name",
    "slash_room",
    "parenthetical",
    "comma_room",
    "unambiguous_room",
    "whole",
)

#: Every distinct LOCATION value observed across the 11 live feeds, with the venue and
#: room it must produce. Measured 2026-09-11.
MEASURED = [
    # (source, raw, name, detail, rule)
    # --- orfe: room first, dash separated -----------------------------------------
    ("orfe", "101 - Sherrerd Hall", "Sherrerd Hall", "101", "detail_dash_name"),
    ("orfe", "125 - Sherrerd Hall", "Sherrerd Hall", "125", "detail_dash_name"),
    ("orfe", "101 - Sherrerd", "Sherrerd", "101", "detail_dash_name"),
    # The typo twin. Passed through as written -- correcting a publisher's spelling would
    # be inventing data, and the value is still a usable venue.
    ("orfe", "101 - Sherrerd Hal", "Sherrerd Hal", "101", "detail_dash_name"),
    # --- mae ----------------------------------------------------------------------
    ("mae", "Engineering Quad J Wing/J223", "Engineering Quad J Wing", "J223", "slash_room"),
    ("mae", "Bowen Hall 222", "Bowen Hall", "222", "unambiguous_room"),
    ("mae", "Bowen Hall", "Bowen Hall", "", "whole"),
    ("mae", "TBD", "", "", "sentinel"),
    # --- ai: three conventions in one feed -----------------------------------------
    ("ai", "Aaron Burr Hall, Room 216", "Aaron Burr Hall", "216", "comma_room"),
    ("ai", "101 Sherrerd Hall", "Sherrerd Hall", "101", "unambiguous_room"),
    # --- cbe: room-first AND building-first, same feed -----------------------------
    ("cbe", "E105 SEAS-BioE", "SEAS-BioE", "E105", "unambiguous_room"),
    ("cbe", "F204 SEAS-CBE", "SEAS-CBE", "F204", "unambiguous_room"),
    ("cbe", "SEAS-CBE F212", "SEAS-CBE", "F212", "unambiguous_room"),
    # --- citp / ece / quantum ------------------------------------------------------
    ("citp", "Sherrerd Hall 306", "Sherrerd Hall", "306", "unambiguous_room"),
    ("citp", "Sherrerd Hall 008", "Sherrerd Hall", "008", "unambiguous_room"),
    ("ece", "EQUAD J323", "EQUAD", "J323", "unambiguous_room"),
    ("ece", "EQUAD J401", "EQUAD", "J401", "unambiguous_room"),
    ("quantum", "EQuad J401", "EQuad", "J401", "unambiguous_room"),
    ("quantum", "Bowen Hall", "Bowen Hall", "", "whole"),
    # --- materials -----------------------------------------------------------------
    ("materials", "Bowen 222", "Bowen", "222", "unambiguous_room"),
    (
        "materials",
        "Briger Hall (C112 + Atrium C102)",
        "Briger Hall",
        "C112 + Atrium C102",
        "parenthetical",
    ),
    # --- bioengineering ------------------------------------------------------------
    (
        "bioengineering",
        "Briger Hall Auditorium, C112",
        "Briger Hall Auditorium",
        "C112",
        "comma_room",
    ),
    ("bioengineering", "Bioengineering E105", "Bioengineering", "E105", "unambiguous_room"),
    # --- cee / robotics: no room at all --------------------------------------------
    ("cee", "Maeder Hall Auditorium", "Maeder Hall Auditorium", "", "whole"),
    ("robotics", "Commons", "Commons", "", "whole"),
]


@pytest.mark.parametrize(
    "source,raw,name,detail,rule",
    MEASURED,
    ids=[f"{s}:{r}" for s, r, _, _, _ in MEASURED],
)
def test_every_measured_location_splits_correctly(
    source: str, raw: str, name: str, detail: str, rule: str
) -> None:
    location, matched = parse_location(raw, FULL)
    assert (location.name, location.detail) == (name, detail)
    assert matched == rule, f"{source}'s {raw!r} should match {rule}, matched {matched}"


def test_the_table_covers_every_rule() -> None:
    """Anti-vacuity: a rule with no measured value behind it is untested."""
    exercised = {rule for _, _, _, _, rule in MEASURED}
    assert exercised == set(FULL), f"rules with no measured case: {set(FULL) - exercised}"


def test_the_table_covers_every_live_source() -> None:
    covered = {source for source, *_ in MEASURED} | {source for source, *_ in PER_SOURCE}
    expected = {
        "orfe",
        "mae",
        "ai",
        "cbe",
        "citp",
        "ece",
        "quantum",
        "materials",
        "bioengineering",
        "cee",
        "robotics",
        "nam",
        "dais",
    }
    assert covered == expected, f"uncovered sources: {expected - covered}"


#: Measured values resolved through each source's **own** chain rather than the full one.
#:
#: They are kept apart from `MEASURED` because the chain is the thing under test. `dais`
#: omits `detail_dash_name` deliberately -- that rule encodes ORFE's room-first order and
#: inverts DaIS's name-first one -- so the same string has to split differently depending
#: on which chain sees it, and a table resolved through `FULL` could not say that.
PER_SOURCE = [
    # --- nam: a comma form and two bare room forms, no dashes anywhere ---------------
    ("nam", "Bendheim House, 103", "Bendheim House", "103", "comma_room"),
    ("nam", "Bendheim 103", "Bendheim", "103", "unambiguous_room"),
    ("nam", "Friend 006", "Friend", "006", "unambiguous_room"),
    # --- dais: the widest vocabulary of any source here -----------------------------
    ("dais", "Computer Science 105", "Computer Science", "105", "unambiguous_room"),
    ("dais", "Chancellor Green", "Chancellor Green", "", "whole"),
    # `002 Auditorium` is not room-only, so the comma rule declines rather than dropping
    # the word "Auditorium" to make it fit.
    ("dais", "Maeder Hall, 002 Auditorium", "Maeder Hall, 002 Auditorium", "", "whole"),
    (
        "dais",
        "Julis Romo Rabinowitz Building - A01",
        "Julis Romo Rabinowitz Building",
        "A01",
        "unambiguous_room",
    ),
    # Neither half is room-shaped, so it stays whole. Honest, and better than the split
    # the next test shows.
    (
        "dais",
        "Frist Health Center - A78B McLain Pavilion",
        "Frist Health Center - A78B McLain Pavilion",
        "",
        "whole",
    ),
]


@pytest.mark.parametrize(
    "source,raw,name,detail,rule",
    PER_SOURCE,
    ids=[f"{s}:{r}" for s, r, _, _, _ in PER_SOURCE],
)
def test_each_new_feed_splits_through_its_own_chain(
    registry,  # type: ignore[no-untyped-def]
    source: str,
    raw: str,
    name: str,
    detail: str,
    rule: str,
) -> None:
    chain = registry.by_slug(source).location_rules
    location, matched = parse_location(raw, chain)
    assert (location.name, location.detail) == (name, detail)
    assert matched == rule, f"{source}'s {raw!r} should match {rule}, matched {matched}"


def test_orfes_chain_would_invert_a_dais_location(registry) -> None:  # type: ignore[no-untyped-def]
    """Why `detail_dash_name` is absent from `dais`, written down where it can fail.

    ORFE writes the room first (`101 - Sherrerd Hall`); DaIS writes it last. Running
    DaIS's values through ORFE's chain produces the venue `A78B McLain Pavilion` and the
    room `Frist Health Center` -- schema-valid, confidently wrong, and exactly the kind of
    output nobody looks at twice. Adding the rule to that chain makes this test fail.
    """
    raw = "Frist Health Center - A78B McLain Pavilion"
    inverted, rule = parse_location(raw, registry.by_slug("orfe").location_rules)
    assert (inverted.name, inverted.detail) == ("A78B McLain Pavilion", "Frist Health Center")
    assert rule == "detail_dash_name"

    kept, _ = parse_location(raw, registry.by_slug("dais").location_rules)
    assert kept.name == raw


def test_the_venue_is_never_empty_when_a_room_was_found() -> None:
    """The predecessor's dash strategy left `name` empty on every MAE event.

    A room with no venue is worse than an unsplit venue: the schema calls `name` the
    venue, and a consumer rendering the location gets nothing to show.
    """
    for _, raw, _, _, rule in MEASURED:
        location, _ = parse_location(raw, FULL)
        if location.detail and rule != "sentinel":
            assert location.name, f"{raw!r} produced a room with no venue"


# --------------------------------------------------------------------------------------
# The four cases both predecessors get wrong
# --------------------------------------------------------------------------------------


def test_the_room_keyword_is_not_left_in_the_venue_name() -> None:
    """`Aaron Burr Hall, Room 216`.

    The predecessor's trailing-token regex is greedy up to the last whitespace, so it
    matches `216` and leaves `Aaron Burr Hall, Room` as the venue.
    """
    location, rule = parse_location("Aaron Burr Hall, Room 216", FULL)
    assert location.name == "Aaron Burr Hall"
    assert location.detail == "216"
    assert rule == "comma_room"
    assert "Room" not in location.name


def test_a_leading_room_number_without_a_dash_still_splits() -> None:
    """`101 Sherrerd Hall`.

    Neither predecessor strategy handles this: dash finds no separator and puts the whole
    string in `detail`; building-room finds no trailing room token and puts it all in
    `name`.
    """
    location, rule = parse_location("101 Sherrerd Hall", FULL)
    assert (location.name, location.detail) == ("Sherrerd Hall", "101")
    assert rule == "unambiguous_room"


def test_a_hyphenated_building_code_is_not_split_on_its_hyphen() -> None:
    """`E105 SEAS-BioE`.

    The predecessor's dash strategy splits on the first hyphen anywhere, so it returns
    name `BioE`, detail `E105 SEAS` -- nonsense in both slots. Requiring whitespace around
    the dash is what prevents it.
    """
    assert rule_detail_dash_name("E105 SEAS-BioE") is None, (
        "the dash rule must decline a hyphen with no surrounding whitespace"
    )
    location, rule = parse_location("E105 SEAS-BioE", FULL)
    assert (location.name, location.detail) == ("SEAS-BioE", "E105")
    assert rule == "unambiguous_room"


def test_a_parenthetical_room_group_is_split_not_swallowed() -> None:
    """`Briger Hall (C112 + Atrium C102)`.

    The trailing `)` means the predecessor's room regex cannot match, so the whole value
    becomes the venue and the rooms are lost.
    """
    location, rule = parse_location("Briger Hall (C112 + Atrium C102)", FULL)
    assert location.name == "Briger Hall"
    assert location.detail == "C112 + Atrium C102"
    assert rule == "parenthetical"


def test_a_two_letter_room_prefix_splits() -> None:
    """The predecessor's `[A-Z]?` accepts only one letter, so `AB212` silently fails
    and the whole value collapses into the venue name."""
    location, _ = parse_location("Friend Center AB212", FULL)
    assert (location.name, location.detail) == ("Friend Center", "AB212")


# --------------------------------------------------------------------------------------
# Declining
# --------------------------------------------------------------------------------------


def test_an_ambiguous_value_is_not_guessed() -> None:
    """Room tokens at both ends: there is no right answer, so no answer is invented.

    This is the rule that lets `cbe` write both orders in one feed without an ordering
    that has to be wrong for one of them.
    """
    assert rule_unambiguous_room("101 Sherrerd 222") is None
    location, rule = parse_location("101 Sherrerd 222", FULL)
    assert location.name == "101 Sherrerd 222"
    assert location.detail == ""
    assert rule == "whole"


def test_a_value_with_no_room_keeps_its_whole_self_as_the_venue() -> None:
    for raw in ("Commons", "Maeder Hall Auditorium", "Bowen Hall"):
        location, rule = parse_location(raw, FULL)
        assert location.name == raw
        assert location.detail == ""
        assert rule == "whole"


@pytest.mark.parametrize("raw", ["TBD", "tbd", " T.B.D. ", "TBA", "n/a", "To Be Announced"])
def test_a_sentinel_location_is_empty_rather_than_a_venue_named_tbd(raw: str) -> None:
    """Publishing `TBD` as a venue is the same class of error as publishing it as a title.

    The predecessor stores it as the venue name, so a consumer renders a room called TBD.
    """
    location, rule = parse_location(raw, FULL)
    assert location.is_empty
    assert rule == "sentinel"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_an_absent_location_declines(raw: str | None) -> None:
    """citp has one event with no LOCATION property at all; materials has two."""
    location, rule = parse_location(raw, FULL)
    assert location.is_empty
    assert rule == DECLINED


def test_a_chain_without_whole_can_decline() -> None:
    """So a source that would rather publish nothing than an unsplit venue can say so."""
    location, rule = parse_location("Commons", ("detail_dash_name",))
    assert location.is_empty
    assert rule == DECLINED


# --------------------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------------------


def test_the_dash_rule_must_precede_the_room_rule() -> None:
    """Order is still load-bearing, but for provenance rather than for the split.

    `101 - Sherrerd Hall` has a room token in its first position, so `unambiguous_room`
    matches it as well. It used to return the venue `- Sherrerd Hall`, dash and all, which
    is what made the ordering urgent; `_trim_separators` removed that, so both rules now
    produce the same answer. What differs is the rule name recorded against the event --
    the provenance a maintainer reads when a location looks wrong.
    """
    coped = rule_unambiguous_room("101 - Sherrerd Hall")
    assert coped is not None, "the room rule does match this, which is why order matters"
    assert (coped.name, coped.detail) == ("Sherrerd Hall", "101"), (
        "no longer the dangling dash it used to produce"
    )

    location, rule = parse_location("101 - Sherrerd Hall", FULL)
    assert location.name == "Sherrerd Hall"
    assert rule == "detail_dash_name", "ORFE's own convention is what gets credited"


def test_the_dash_rule_still_does_something_the_room_rule_cannot() -> None:
    """Otherwise the precedence list would be protecting nothing.

    The room rule declines when neither end is room-shaped, so a dash with a descriptive
    detail only splits because `detail_dash_name` is in the chain.
    """
    assert rule_unambiguous_room("Ground Floor - Friend Center") is None
    location, rule = parse_location("Ground Floor - Friend Center", FULL)
    assert (location.name, location.detail) == ("Friend Center", "Ground Floor")
    assert rule == "detail_dash_name"


# --------------------------------------------------------------------------------------
# A separator is not part of a name
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "name", "detail"),
    [
        # `dais`, and the value that found this: its chain has no `detail_dash_name`, so a
        # name-first dash reaches `unambiguous_room` intact for the first time.
        ("Julis Romo Rabinowitz Building - A01", "Julis Romo Rabinowitz Building", "A01"),
        ("Bendheim House , 103", "Bendheim House", "103"),
        ("Friend Center / 006", "Friend Center", "006"),
    ],
)
def test_a_dangling_separator_does_not_become_part_of_the_venue(
    raw: str, name: str, detail: str
) -> None:
    """Schema-valid and wrong: `Julis Romo Rabinowitz Building -` is a venue name nobody
    would write, and it would have gone into the published feed and then into a newsletter
    line reading "in Julis Romo Rabinowitz Building - A01"."""
    location = rule_unambiguous_room(raw)
    assert location is not None
    assert (location.name, location.detail) == (name, detail)


def test_an_internal_hyphen_is_not_a_separator() -> None:
    """Only whole tokens are dropped. `SEAS-CBE` is a building code, not a join."""
    location = rule_unambiguous_room("SEAS-CBE F212")
    assert location is not None
    assert location.name == "SEAS-CBE"


def test_a_value_that_is_only_a_room_and_a_dash_declines() -> None:
    """Trimming can leave nothing behind, and a venue named `-` is worse than an unsplit
    one. Declining hands it to `whole`, which keeps it as written."""
    assert rule_unambiguous_room("- 101") is None
    location, rule = parse_location("- 101", FULL)
    assert (location.name, rule) == ("- 101", "whole")


def test_the_declared_precedence_list_matches_the_full_chain() -> None:
    """Keeps the documented constraint and the chain from drifting apart."""
    index = FULL.index("unambiguous_room")
    for name in MUST_PRECEDE_UNAMBIGUOUS:
        assert FULL.index(name) < index, f"{name} must precede unambiguous_room"


def test_normalization_happens_before_the_rules_see_the_value() -> None:
    """Feeds carry stray internal whitespace; a rule should not have to cope with it."""
    location, _ = parse_location("  Bowen   Hall   222  ", FULL)
    assert (location.name, location.detail) == ("Bowen Hall", "222")


# --------------------------------------------------------------------------------------
# Registry integration
# --------------------------------------------------------------------------------------


def test_unknown_rule_names_are_reportable() -> None:
    assert unknown_rules(["whole", "nosuchrule", "sentinel"]) == ("nosuchrule",)
    assert unknown_rules(FULL) == ()


def test_every_rule_name_in_the_committed_registry_exists(registry) -> None:
    """A typo'd rule name would silently never fire, degrading every location."""
    for source in registry.sources:
        missing = unknown_rules(source.location_rules)
        assert not missing, f"{source.slug} names unknown location rules: {missing}"


def test_every_rule_is_reachable_from_some_source(registry) -> None:
    """A rule no source uses is either dead or a source is misconfigured."""
    used = {rule for source in registry.sources for rule in source.location_rules}
    unused = set(RULES) - used
    assert unused <= {"sentinel"}, f"rules no source uses: {unused}"
