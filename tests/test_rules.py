"""The mapping rule engine, and what it decides for each of the four declared sources.

The distribution tests are the heart of this file. A rule reordering that silently moves
four events from one rule to another still produces schema-valid output -- the only thing
that catches it is the recorded count of which rule fired.
"""

from __future__ import annotations

import collections
import textwrap

import pytest
import yaml

from tests.support import FIXTURES, REPO_ROOT
from upcoming.build import build_from_file, load_pronunciation
from upcoming.errors import ConfigFatal, SourceFatal
from upcoming.patterns import load_vocabulary
from upcoming.registry import load_registry
from upcoming.rules import apply_chain, load_chain, rule_ids

VOCAB = load_vocabulary(REPO_ROOT / "config" / "patterns.yaml")

#: What each source's chain must produce over its committed fixture. Exact counts: a
#: reclassification is invisible in the output and visible only here.
EXPECTED = {
    "quantum": {
        "quantum:series-quoted-title-dash-person": 1,
        "quantum:series-title-dash-person": 6,
        "quantum:series-title-comma-person": 10,
        "quantum:title": 1,
    },
    "citp": {
        "citp:speaker-dash-title": 6,
        "citp:bare-person-name": 5,
        "citp:title": 13,
    },
    "materials": {
        "materials:series-person-affiliation": 12,
        "materials:title": 4,
    },
    "ai": {
        "ai:series-listing": 5,
        "ai:bare-person-name": 1,
        "ai:title": 1,
    },
}


def built(slug: str, registry):  # type: ignore[no-untyped-def]
    load_pronunciation()
    result = build_from_file(FIXTURES / "feeds" / slug / "feed.ics", registry.by_slug(slug))
    assert result.status == "ok", result.diagnostics
    return result.events


def chain_from(text: str, slug: str = "alpha"):  # type: ignore[no-untyped-def]
    return load_chain(yaml.safe_load(textwrap.dedent(text)), VOCAB, slug=slug)


# --------------------------------------------------------------------------------------
# Distribution: which rule fired, and how often
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_the_rule_distribution_is_exactly_as_declared(slug: str, registry) -> None:
    events = built(slug, registry)
    actual = collections.Counter(e.mapping_rules[0] for e in events)
    assert dict(actual) == EXPECTED[slug]


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_every_rule_fires_at_least_once(slug: str, registry) -> None:
    """A rule nobody exercises is a rule nobody has tested."""
    source = registry.by_slug(slug)
    declared = set(rule_ids(source.summary_rules, slug))
    fired = {e.mapping_rules[0] for e in built(slug, registry)}
    assert not declared - fired, f"{slug} has dead rules: {sorted(declared - fired)}"


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_every_event_records_the_rule_that_mapped_it(slug: str, registry) -> None:
    for event in built(slug, registry):
        assert event.mapping_rules, f"{event.id} has no mapping attribution"
        assert event.mapping_rules[0].startswith(f"{slug}:")


# --------------------------------------------------------------------------------------
# The hard decompositions
# --------------------------------------------------------------------------------------


def test_a_title_keeps_its_own_colon_and_parenthetical(registry) -> None:
    """quantum's worst case, and the one that decides whether the patterns are right.

    "Princeton Quantum Colloquium: An Astonishing Quantum Universe in Two Dimensions: From
    Fundamental Physics to (Possibly) Quantum Computation, Jainendra Jain (Penn State
    University)"

    The series ends at the FIRST colon; the title keeps its own colon and its own
    parenthetical; the speaker and affiliation come from the LAST parenthetical. It works
    because `[^()]+` cannot cross a parenthesis, which forces the greedy title to give up
    only the final group.
    """
    event = next(e for e in built("quantum", registry) if "Astonishing" in e.title)

    assert event.title == (
        "An Astonishing Quantum Universe in Two Dimensions: From Fundamental Physics to "
        "(Possibly) Quantum Computation"
    )
    assert event.speakers[0].name == "Jainendra Jain"
    assert event.speakers[0].affiliation == "Penn State University"
    assert "Princeton Quantum Colloquium" in event.tags
    assert event.title_is_placeholder is False


def test_an_affiliation_may_contain_a_comma(registry) -> None:
    """ "University of Colorado, Boulder" must survive whole."""
    events = [e for e in built("quantum", registry) if e.speakers]
    boulder = [e for e in events if "Boulder" in (e.speakers[0].affiliation or "")]
    assert len(boulder) == 2
    for event in boulder:
        assert event.speakers[0].affiliation == "University of Colorado, Boulder"


def test_the_quoted_pattern_wins_over_the_unquoted_one(registry) -> None:
    """Order is load-bearing: the unquoted pattern also matches a quoted string, and would
    keep the quote marks inside the title."""
    event = next(e for e in built("quantum", registry) if "Local Autonomous" in e.title)
    assert event.mapping_rules[0] == "quantum:series-quoted-title-dash-person"
    assert not event.title.startswith('"')
    assert '"' not in event.title


def test_a_tbd_title_is_absence_not_a_title(registry) -> None:
    """14 of quantum's 18 events have no title yet, and that is normal for this feed.

    The speaker and series are known and published; the title is synthesized and flagged.
    """
    events = built("quantum", registry)
    placeholders = [e for e in events if e.title_is_placeholder]
    assert len(placeholders) == 14

    for event in placeholders:
        assert event.title != "TBD"
        assert event.title_source.startswith("fallback-")
        assert event.speakers, "the speaker is known even when the title is not"


def test_a_bare_name_becomes_the_speaker_not_the_title(registry) -> None:
    """citp publishes five events whose entire SUMMARY is a person's name."""
    events = built("citp", registry)
    bare = [e for e in events if e.mapping_rules[0] == "citp:bare-person-name"]
    assert len(bare) == 5
    for event in bare:
        assert event.speakers[0].name == event.summary_raw.strip()
        assert event.title_is_placeholder, "the title is synthesized, since the feed has none"


def test_a_speaker_dash_title_splits_both_ways(registry) -> None:
    event = next(e for e in built("citp", registry) if "Augmentation Agenda" in e.title)
    assert event.speakers[0].name == "Arvind Narayanan"
    assert event.title == "AI Agents and the Augmentation Agenda"
    assert event.title_is_placeholder is False


def test_a_listing_keeps_the_publishers_wording_and_is_tagged(registry) -> None:
    """ "ORFE Talks & Seminars Flyers Fall 2026 Colloquium" names a listing page.

    Deferring the title instead yields "A Talk", which is worse than the publisher's own
    words. The tag is what lets a consumer filter these out.
    """
    listings = [e for e in built("ai", registry) if e.mapping_rules[0] == "ai:series-listing"]
    assert len(listings) == 5
    for event in listings:
        assert "listing" in event.tags
        assert event.title == event.summary_raw.strip()
        assert not event.speakers


def test_materials_publishes_a_speaker_where_the_feed_names_no_title(registry) -> None:
    event = next(e for e in built("materials", registry) if "Sean Roberts" in e.speaker)
    assert event.speakers[0].name == "Sean Roberts"
    assert event.speakers[0].affiliation == "University of Texas at Austin"
    assert "PMI/PCCM SEMINAR SERIES Fall 2026" in event.tags
    assert event.title_is_placeholder


# --------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------


def test_an_empty_chain_is_refused() -> None:
    """The role exists to make someone state what SUMMARY means."""
    with pytest.raises(ConfigFatal, match="lists none"):
        chain_from("rules: []")


def test_an_unknown_pattern_is_refused() -> None:
    with pytest.raises(ConfigFatal, match="unknown pattern"):
        chain_from(
            """
            rules:
              - id: r
                when: { matches: no_such_pattern }
                then: { assign: { title: summary } }
            """
        )


def test_an_unknown_predicate_is_refused() -> None:
    """Predicates are code with their own tests; config selects one but cannot define one."""
    with pytest.raises(ConfigFatal, match="unknown predicate"):
        chain_from(
            """
            rules:
              - id: r
                when: { predicate: looks_about_right }
                then: { assign: { title: summary } }
            """
        )


def test_an_unknown_target_field_is_refused() -> None:
    with pytest.raises(ConfigFatal, match="unknown field"):
        chain_from(
            """
            rules:
              - id: r
                when: { always: true }
                then: { assign: { titel: summary } }
            """
        )


def test_an_unbound_capture_group_is_refused() -> None:
    """An unused named group is a typo, and a typo here drops a field silently."""
    with pytest.raises(ConfigFatal, match="unbound"):
        chain_from(
            """
            rules:
              - id: r
                when: { matches: person_dash_title }
                then: { capture: { title: title } }
            """
        )


def test_capturing_a_group_the_pattern_lacks_is_refused() -> None:
    with pytest.raises(ConfigFatal, match="does not define"):
        chain_from(
            """
            rules:
              - id: r
                when: { matches: person_dash_title }
                then: { capture: { title: title, speakers: person, series_extra: nope } }
            """
        )


def test_a_duplicate_rule_id_is_refused() -> None:
    """Ids are published on events; two rules sharing one make attribution meaningless."""
    with pytest.raises(ConfigFatal, match="duplicate rule id"):
        chain_from(
            """
            rules:
              - id: r
                when: { always: true }
                then: { assign: { title: summary } }
              - id: r
                when: { always: true }
                then: { assign: { title: summary } }
            """
        )


def test_a_rule_without_an_id_is_refused() -> None:
    with pytest.raises(ConfigFatal, match="has no id"):
        chain_from(
            """
            rules:
              - when: { always: true }
                then: { assign: { title: summary } }
            """
        )


def test_predicate_nesting_beyond_the_ceiling_is_refused() -> None:
    """A condition this involved wants a named pattern, where it can carry examples."""
    with pytest.raises(ConfigFatal, match="nests deeper"):
        chain_from(
            """
            rules:
              - id: r
                when:
                  all:
                    - any:
                        - not:
                            - all: [{ always: true }]
                then: { assign: { title: summary } }
            """
        )


def test_an_unknown_operator_is_refused() -> None:
    with pytest.raises(ConfigFatal, match="unknown operator"):
        chain_from(
            """
            rules:
              - id: r
                when: { resembles: something }
                then: { assign: { title: summary } }
            """
        )


def test_an_unknown_action_is_refused() -> None:
    with pytest.raises(ConfigFatal, match="unknown action"):
        chain_from(
            """
            rules:
              - id: r
                when: { always: true }
                then: { transmogrify: { title: summary } }
            """
        )


def test_an_invalid_on_no_match_is_refused() -> None:
    with pytest.raises(ConfigFatal, match="no silent option"):
        chain_from(
            """
            on_no_match: ignore
            rules:
              - id: r
                when: { always: true }
                then: { assign: { title: summary } }
            """
        )


def test_an_unmatched_summary_fails_the_source_by_default() -> None:
    """Rather than guessing which field the text belongs to."""
    chain = chain_from(
        """
        on_no_match: fail
        rules:
          - id: person
            when: { predicate: person_name_shape }
            then: { assign: { speakers: summary } }
        """
    )
    with pytest.raises(SourceFatal, match="no rule matched"):
        apply_chain("Some Event Title That Is Not A Name", chain, VOCAB, slug="alpha")


def test_on_no_match_title_is_available_but_must_be_declared() -> None:
    chain = chain_from(
        """
        on_no_match: title
        rules:
          - id: person
            when: { predicate: person_name_shape }
            then: { assign: { speakers: summary } }
        """
    )
    outcome = apply_chain("Bias in AI Reading Group", chain, VOCAB, slug="alpha")
    assert outcome.assigned["title"] == "Bias in AI Reading Group"
    assert outcome.rule_id == "alpha:no-match-title"


def test_rules_declared_for_a_source_that_would_never_run_them_are_refused() -> None:
    """A chain under `summary_role: title` would sit there doing nothing."""
    registry_yaml = textwrap.dedent(
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
        sources:
          - slug: alpha
            host: alpha.example.edu
            summary:
              rules:
                - id: r
                  when: { always: true }
                  then: { assign: { title: summary } }
            expectations:
              summary_role: title
        """
    )
    path = REPO_ROOT / "tests" / "fixtures" / "_tmp_registry.yaml"
    path.write_text(registry_yaml, encoding="utf-8")
    try:
        with pytest.raises(ConfigFatal, match="would never run"):
            load_registry(path, env={"BOT_BYPASS_HEADER": "x-t: 1"})
    finally:
        path.unlink()


# --------------------------------------------------------------------------------------
# Evaluation semantics
# --------------------------------------------------------------------------------------


def test_the_first_matching_rule_wins() -> None:
    chain = chain_from(
        """
        rules:
          - id: first
            when: { always: true }
            then: { assign: { title: summary } }
          - id: second
            when: { always: true }
            then: { assign: { speakers: summary } }
        """
    )
    assert apply_chain("anything", chain, VOCAB, slug="alpha").rule_id == "alpha:first"


def test_a_not_combinator_inverts() -> None:
    chain = chain_from(
        """
        rules:
          - id: not-a-person
            when: { not: { predicate: person_name_shape } }
            then: { assign: { title: summary } }
          - id: person
            when: { always: true }
            then: { assign: { speakers: summary } }
        """
    )
    assert (
        apply_chain("Bias in AI Reading Group", chain, VOCAB, slug="a").rule_id == "a:not-a-person"
    )
    assert apply_chain("Andy Guess", chain, VOCAB, slug="a").rule_id == "a:person"


def test_an_all_combinator_requires_every_child() -> None:
    chain = chain_from(
        """
        rules:
          - id: both
            when:
              all:
                - { predicate: person_name_shape }
                - { not_contains: "Guess" }
            then: { assign: { speakers: summary } }
          - id: fallthrough
            when: { always: true }
            then: { assign: { title: summary } }
        """
    )
    assert apply_chain("Eve Fleisig", chain, VOCAB, slug="a").rule_id == "a:both"
    assert apply_chain("Andy Guess", chain, VOCAB, slug="a").rule_id == "a:fallthrough"
