"""This pipeline against the predecessor's own expected output.

The two golden pairs in `pubino/mae-upcoming` are the only independent check available on
field-mapping semantics: real ICS in, a real department's expected JSON out, produced by a
pipeline that ran in production for a year. Agreeing with them on the fields we both
produce is strong evidence the mapping is right.

They are **not** a byte target, and `tests/fixtures/README.md` records why. So every
divergence below is its own named test that states the reason and asserts the golden really
does contain what it is diverging from -- a divergence test that passes because the golden
changed underneath it would be worse than no test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support import FIXTURES
from upcoming.build import build_events, load_pronunciation, render
from upcoming.registry import SourceConfig

#: Fields both pipelines produce with the same intended meaning.
SHARED_FIELDS = ("startTime", "endTime", "urlRef", "series", "cancelled", "bannerImage", "itemType")


def golden(slug: str) -> dict[str, dict]:
    records = json.loads((FIXTURES / "golden" / f"{slug}.predecessor.json").read_text("utf-8"))
    assert records, f"the {slug} golden must not be empty"
    return {r["guid"]: r for r in records}


def produced(source: SourceConfig) -> dict[str, dict]:
    load_pronunciation()
    text = (FIXTURES / "feeds" / source.slug / "feed.ics").read_text(encoding="utf-8")
    records = json.loads(render(build_events(text, source), source))
    assert records, f"the {source.slug} build must not be empty"
    return {r["guid"]: r for r in records}


@pytest.fixture
def orfe(registry) -> SourceConfig:
    return registry.by_slug("orfe")


@pytest.fixture
def mae(registry) -> SourceConfig:
    return registry.by_slug("mae")


# --------------------------------------------------------------------------------------
# Agreement
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["orfe", "mae"])
def test_the_shared_fields_agree(slug: str, registry) -> None:
    """The core of the comparison: same input, same answer.

    Every guid the golden knows about must exist here, and agree on the fields neither
    pipeline synthesizes.
    """
    source = registry.by_slug(slug)
    mine, theirs = produced(source), golden(slug)

    assert set(theirs) <= set(mine), f"missing from this build: {sorted(set(theirs) - set(mine))}"

    for guid, expected in theirs.items():
        actual = mine[guid]
        for field in SHARED_FIELDS:
            assert actual[field] == expected[field], f"{slug}:{guid} field {field}"


@pytest.mark.parametrize("slug", ["orfe", "mae"])
def test_locations_agree_exactly(slug: str, registry) -> None:
    """No tolerance for a swapped name and detail.

    The predecessor's own roundtrip test accepts `location.name` and `location.detail`
    being transposed -- in the test meant to pin the location convention -- so it would
    pass with every venue in the room field.
    """
    source = registry.by_slug(slug)
    mine, theirs = produced(source), golden(slug)
    for guid, expected in theirs.items():
        assert mine[guid]["location"] == expected["location"], f"{slug}:{guid}"


def test_orfe_speakers_agree_including_the_escaping(orfe) -> None:
    """ORFE's ingest expects `Elynn Chen\\, New York University`.

    This is the proof that moving escaping out of the model and into serialization did not
    change what a consumer receives -- and that splitting the affiliation into `speakers[]`
    structure left the scalar intact.
    """
    mine, theirs = produced(orfe), golden("orfe")
    compared = 0
    for guid, expected in theirs.items():
        if expected.get("speaker"):
            assert mine[guid]["speaker"] == expected["speaker"], guid
            compared += 1
    assert compared >= 10, "the ORFE golden should carry speakers on most events"


def test_orfe_speakers_are_escaped_in_the_output(orfe) -> None:
    """Anti-vacuity for the test above: the escaping must actually be present."""
    mine = produced(orfe)
    assert any("\\," in r["speaker"] for r in mine.values())


def test_mae_titles_agree_where_the_feed_supplied_one(mae) -> None:
    """MAE's SUMMARY is the title, and it must survive unescaped.

    `Winds, Waves, and Wakes` is the case the predecessor's per-source escaping boolean
    gets wrong when it is turned on for the speaker.
    """
    mine, theirs = produced(mae), golden("mae")
    compared = 0
    for guid, expected in theirs.items():
        if expected.get("titleSource") == "ics":
            assert mine[guid]["title"] == expected["title"], guid
            compared += 1
    assert compared >= 7, "most MAE events should carry a real title"

    titles = [r["title"] for r in mine.values()]
    assert any("Winds, Waves, and Wakes" in t for t in titles)
    assert not any("\\," in t for t in titles), "a title must never be comma-escaped"


def letters_and_digits(value: str) -> str:
    """The prose of a text field, with punctuation and spacing set aside.

    Coarse on purpose. The abstracts run to hundreds of characters, so matching every
    letter of one still proves the field was mapped from the right place -- while
    tolerating the two punctuation-level corruptions in the golden that the tests below
    pin exactly.
    """
    return "".join(ch for ch in value.lower() if ch.isalnum())


def test_content_carries_the_same_prose(registry) -> None:
    """The abstract came from the right field, word for word.

    Spacing and punctuation deliberately differ, for three reasons, each asserted
    separately below: the goldens were generated with collapsing switched off, they run
    words together at every line fold, and they leave a stray semicolon behind from each
    partially-decoded HTML entity.
    """
    source = registry.by_slug("orfe")
    mine, theirs = produced(source), golden("orfe")
    compared = 0
    for guid, expected in theirs.items():
        want = expected.get("content") or ""
        if not want:
            continue
        assert letters_and_digits(mine[guid]["content"]) == letters_and_digits(want), guid
        compared += 1
    assert compared >= 5, "the ORFE golden should carry abstracts on several events"


def test_html_entities_are_decoded_where_the_golden_half_decodes_them(registry) -> None:
    """The feed carries `&gt\\;` -- an HTML entity whose semicolon is ICS-escaped.

    This build decodes it to `>`. The golden yields `>\\;`: the `&gt` became `>` and the
    entity's own semicolon survived as a separate character, so the published abstract
    reads "N >; >; T" where the author wrote "N >> T".

    The predecessor is inconsistent about this, which is the evidence nobody decided:
    ORFE's golden half-decodes `&gt;`, while MAE's leaves `&amp\\;` and `&nbsp\\;`
    untouched -- from the same code.
    """
    source = registry.by_slug("orfe")
    mine, theirs = produced(source), golden("orfe")

    golden_text = " ".join((r.get("content") or "") for r in theirs.values())
    assert ">\\;" in golden_text, "precondition: the golden leaves a stray semicolon"

    mine_text = " ".join(r["content"] for r in mine.values())
    assert ">>" in mine_text, "the entity should decode to what the author wrote"
    assert ">\\;" not in mine_text
    assert "&gt;" not in mine_text and "&amp;" not in mine_text
    assert "\xa0" not in mine_text, "a non-breaking space should normalize"


def test_the_golden_runs_words_together_and_this_build_does_not(registry) -> None:
    """A real defect in the predecessor's published abstracts, fixed here.

    RFC 5545 folds a long line as CRLF plus **one** whitespace character, and unfolding
    removes exactly those two. The predecessor loses the space that preceded the fold as
    well, so any line broken after a space comes back with its words glued: `adata-driven`,
    `banditmodel`, `anyneural network`, `publichealth`. Fifteen times in this one fixture.

    Those abstracts go to campus systems, so this is corrupted text in production output,
    not a cosmetic difference.
    """
    source = registry.by_slug("orfe")
    mine, theirs = produced(source), golden("orfe")

    glued = [
        "adata-driven",
        "banditmodel",
        "group-specificlinear",
        "anyneural",
        "publichealth",
    ]
    golden_text = " ".join((r.get("content") or "") for r in theirs.values())
    for run_on in glued:
        assert run_on in golden_text, f"precondition: the golden should contain {run_on!r}"

    mine_text = " ".join(r["content"] for r in mine.values())
    for run_on in glued:
        assert run_on not in mine_text, f"{run_on!r} should have been unfolded correctly"

    assert "a data-driven" in mine_text
    assert "any neural network" in mine_text


# --------------------------------------------------------------------------------------
# Intended divergences, each with its reason
# --------------------------------------------------------------------------------------


def test_orfe_emits_the_record_the_golden_omits(orfe) -> None:
    """The golden has 13 records; its own feed has 14.

    `ps_events:11941` is a second Drupal node for the same talk -- "North Carolina Chapel
    Hill" without the comma, location typo "101 - Sherrerd Hal". It parses fine, so the
    golden is stale, and the test guarding it could not notice: it iterates the expected
    records looking each up in produced output, under the comment "Allow produced to
    contain additional events not yet listed in expected sample."

    A per-source feed is faithful to its upstream. De-duplicating is the consumer's call.
    """
    mine, theirs = produced(orfe), golden("orfe")
    assert len(theirs) == 13, "precondition: the golden is one record short"
    assert len(mine) == 14

    missing = set(mine) - set(theirs)
    assert missing == {"ps_events:11941:delta:0"}
    assert mine["ps_events:11941:delta:0"]["location"]["name"] == "Sherrerd Hal"


def test_mae_tbd_title_is_synthesized_rather_than_published(mae) -> None:
    """The golden ships the literal string "TBD" as a title, with no provenance at all.

    Both `titleSource` and `titleIsPlaceholder` are absent from that record, so a consumer
    cannot tell it is a placeholder -- it is schema-valid and wrong, which is the exact
    failure this project exists to prevent.
    """
    mine, theirs = produced(mae), golden("mae")

    tbd = [g for g, r in theirs.items() if r.get("title") == "TBD"]
    assert len(tbd) == 1, "precondition: the golden publishes exactly one TBD title"
    guid = tbd[0]
    assert theirs[guid].get("titleSource") is None
    assert theirs[guid].get("titleIsPlaceholder") is None

    record = mine[guid]
    assert record["title"] == "An MAE Departmental Seminars Talk"
    # `fallback-template` rather than `fallback-series`: a default title template now
    # lives in `defaults:`, so the template branch produces this rather than the
    # series-only last resort. The text is unchanged and the honesty is the same -- the
    # provenance is simply more accurate about which branch ran.
    assert record["titleSource"] == "fallback-template"
    assert record["titleIsPlaceholder"] is True


def test_orfe_titles_are_filled_where_the_golden_leaves_them_empty(orfe) -> None:
    """Every ORFE title in the golden is `""`, which fails the schema's own minLength: 1.

    ORFE's SUMMARY is the speaker, so no event carries a title. Synthesis is therefore a
    precondition for publishing, not a refinement -- and each synthesized title says so.
    """
    mine, theirs = produced(orfe), golden("orfe")
    assert all(r.get("title") == "" for r in theirs.values()), "precondition"

    for record in mine.values():
        assert record["title"], "a published title is never empty"
        assert record["titleIsPlaceholder"] is True
        assert record["titleSource"].startswith("fallback-")


def test_this_build_adds_fields_the_predecessor_has_no_concept_of(orfe) -> None:
    """Named so the extra keys are a decision, not drift."""
    mine, theirs = produced(orfe), golden("orfe")
    # Against the union of golden keys: the ORFE golden predates provenance and carries
    # no titleSource/rawEventDetails, while the MAE golden does.
    theirs_keys = set(golden("orfe")) and {k for r in theirs.values() for k in r}
    theirs_keys |= {k for r in golden("mae").values() for k in r}
    added = {k for r in mine.values() for k in r} - theirs_keys
    assert added == {
        "id",  # source-scoped identity: ps_events UIDs are per-site sequences
        "sources",  # always an array, in both output kinds
        "platform",  # kellercenter is not Site Builder
        "startsAt",  # absolute instant; startTime is a wall clock with no offset
        "endsAt",
        "timezone",
        "affiliation",  # derived from speakers[]
        "speakers",  # an event can have four
        "tags",  # canonical, so a combo predicate can mean something
        "rawCategories",
        "summaryRaw",  # what the mapping decision was made from
        "locationRaw",
        "mappingRules",
        "locationRule",
        "summaryRest",
        "mappingConflict",
        "rawEventDetails",  # enrichment output; neither golden was enriched
        "rawExtractAbstract",
        "rawExtractBio",
        "unmappedTags",  # categories no canonical tag recognised, published not dropped
    }


def test_the_predecessor_has_no_field_this_build_drops(registry) -> None:
    """The other direction: nothing a consumer reads today has gone away."""
    for slug in ("orfe", "mae"):
        source = registry.by_slug(slug)
        mine, theirs = produced(source), golden(slug)
        for guid, expected in theirs.items():
            dropped = set(expected) - set(mine[guid])
            assert not dropped, f"{slug}:{guid} no longer publishes {sorted(dropped)}"


# --------------------------------------------------------------------------------------
# The inversion
# --------------------------------------------------------------------------------------


def test_the_two_sources_are_read_in_opposite_directions(orfe, mae) -> None:
    """The proof the whole refactor exists to deliver.

    One codebase, two feeds whose SUMMARY means opposite things, both right -- with nothing
    differing but `config/sources.yaml`. The predecessor needed a fork.
    """
    orfe_records, mae_records = produced(orfe), produced(mae)

    # ORFE: the summary became the speaker, and no title came from the feed.
    for record in orfe_records.values():
        assert record["speaker"], "ORFE's SUMMARY is the speaker"
        assert record["titleSource"].startswith("fallback-")
        assert record["summaryRaw"].replace("\\,", ",") in record["speaker"].replace("\\,", ",")

    # MAE: the summary became the title, and no speaker came from the feed.
    from_feed = [r for r in mae_records.values() if r["titleSource"] == "ics"]
    assert len(from_feed) >= 7
    for record in from_feed:
        assert record["title"] == record["summaryRaw"]
        assert record["speaker"] == "", "MAE's speaker comes from the page, not the feed"


def test_no_module_branches_on_a_source_slug() -> None:
    """The inversion must live in config, not in an `if slug == "orfe"`.

    One such branch would make the whole registry decorative, and it is the most natural
    shortcut to reach for when a second source misbehaves.
    """
    package = Path(__file__).resolve().parent.parent / "upcoming"
    for module in package.rglob("*.py"):
        body = module.read_text(encoding="utf-8")
        for slug in ("orfe", "mae", "citp", "quantum", "materials", "bioengineering"):
            for pattern in (f'== "{slug}"', f"== '{slug}'", f'"{slug}" ==', f'slug == "{slug}"'):
                assert pattern not in body, f"{module.name} branches on the source slug"


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["orfe", "mae"])
def test_building_twice_produces_identical_bytes(slug: str, registry) -> None:
    source = registry.by_slug(slug)
    load_pronunciation()
    text = (FIXTURES / "feeds" / slug / "feed.ics").read_text(encoding="utf-8")
    assert render(build_events(text, source), source) == render(build_events(text, source), source)


def test_two_events_at_one_instant_order_stably(orfe) -> None:
    """ORFE's feed carries such a pair, which is why the sort needs a tiebreaker.

    The predecessor sorts on start time alone, over a `set`, so these two are free to swap
    between runs -- churning the published file and defeating byte-for-byte verification of
    the served feed.
    """
    mine = produced(orfe)
    pair = ("ps_events:11931", "ps_events:11941")
    twins = [r for r in mine.values() if r["guid"].startswith(pair)]
    assert len(twins) == 2
    assert twins[0]["startTime"] == twins[1]["startTime"], "precondition: same instant"

    text = (FIXTURES / "feeds" / "orfe" / "feed.ics").read_text(encoding="utf-8")
    ordered = [r["id"] for r in json.loads(render(build_events(text, orfe), orfe))]
    expected = sorted(ordered, key=lambda i: (mine[i.split(":", 1)[1]]["startTime"], i))
    assert ordered == expected
