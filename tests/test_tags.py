"""Canonical tags: one name for what four departments spell four ways."""

from __future__ import annotations

import pytest

from tests.support import REPO_ROOT
from upcoming.build import build_from_file, load_pronunciation
from upcoming.errors import ConfigFatal
from upcoming.tags import load_tags

VOCAB = load_tags(REPO_ROOT / "config" / "tags.yaml")


@pytest.mark.parametrize(
    "spelling",
    [
        "FPO",  # orfe
        "Final Public Oral Exam",  # mae
        "Final Public Oral Examinations",  # cbe, ece
        "Final Public Orals",  # ece
        "final public orals",  # casefolded
        "  Final   Public  Orals  ",  # whitespace collapsed
    ],
)
def test_every_spelling_of_one_concept_reaches_one_tag(spelling: str) -> None:
    """Four spellings, one university, one concept.

    Without this a combined feed cannot say "exclude FPOs". The predecessor's own workflow
    shows the consequence: it excludes the literal string "FPO", which matches nothing in
    MAE's feed, so its "filtered" variant is identical to the unfiltered one and nothing
    reports it.
    """
    tags, unmapped = VOCAB.normalize([spelling])
    assert tags == ("fpo",)
    assert unmapped == ()


def test_two_aliases_on_one_event_yield_one_tag() -> None:
    """ece publishes both of its FPO spellings on the same events."""
    tags, _ = VOCAB.normalize(["Final Public Oral Examinations", "Final Public Orals"])
    assert tags == ("fpo",)


def test_an_unrecognised_category_is_reported_not_dropped() -> None:
    """A department inventing a series should surface as a number, not vanish."""
    tags, unmapped = VOCAB.normalize(["Brand New Series Nobody Has Declared"])
    assert tags == ()
    assert unmapped == ("Brand New Series Nobody Has Declared",)


def test_a_term_suffix_does_not_break_an_alias() -> None:
    """materials publishes "PMI/PCCM SEMINAR SERIES Fall 2026".

    The series is the same series next term, so the suffix is dropped before the lookup --
    otherwise the vocabulary would need a new entry every year and would silently stop
    matching in between.
    """
    assert VOCAB.normalize(["PMI/PCCM Seminar Series Fall 2026"])[0] == ("seminar",)
    assert VOCAB.normalize(["PMI/PCCM SEMINAR SERIES Spring 2031"])[0] == ("seminar",)
    # A year with no season is not a term suffix, and is left alone.
    assert VOCAB.normalize(["PMI/PCCM Seminar Series 2026"])[0] == ()


def test_matching_is_exact_never_fuzzy() -> None:
    """A tag that silently becomes a different tag is as invisible as a transposed title."""
    tags, unmapped = VOCAB.normalize(["Final Public Oral Examination Committee"])
    assert tags == ()
    assert unmapped


def test_a_canonical_name_is_its_own_alias() -> None:
    assert VOCAB.normalize(["fpo"])[0] == ("fpo",)
    assert VOCAB.normalize(["colloquium"])[0] == ("colloquium",)


def test_an_ambiguous_alias_is_refused(tmp_path) -> None:
    """Whichever won would be an accident of file order, and the loser would never appear."""
    path = tmp_path / "tags.yaml"
    path.write_text(
        "tags:\n  one:\n    aliases: [Shared]\n  two:\n    aliases: [Shared]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigFatal, match="maps to both"):
        load_tags(path)


def test_a_non_slug_tag_name_is_refused(tmp_path) -> None:
    """Canonical tags are published and matched on, so they need a stable spelling."""
    path = tmp_path / "tags.yaml"
    path.write_text("tags:\n  Not A Slug:\n    aliases: []\n", encoding="utf-8")
    with pytest.raises(ConfigFatal, match="lowercase slug"):
        load_tags(path)


def test_published_tags_are_only_ever_canonical(registry) -> None:
    """So a combo predicate and the schema can both rely on the value.

    Everything goes through the vocabulary -- CATEGORIES, a tag a rule added, and a series
    a rule read out of the SUMMARY -- and anything unrecognised stays out of `tags`.
    """
    load_pronunciation()
    for source in registry.live:
        result = build_from_file(f"tests/fixtures/feeds/{source.slug}/feed.ics", source)
        assert result.status == "ok", result.diagnostics
        for event in result.events:
            for tag in event.tags:
                assert VOCAB.known(tag), f"{source.slug} published non-canonical tag {tag!r}"


def test_the_fpo_tag_reaches_events_from_three_different_sources(registry) -> None:
    """Anti-vacuity: the point of the vocabulary is that it crosses sources."""
    load_pronunciation()
    sources_with_fpo = set()
    for source in registry.live:
        result = build_from_file(f"tests/fixtures/feeds/{source.slug}/feed.ics", source)
        if any("fpo" in e.tags for e in result.events):
            sources_with_fpo.add(source.slug)
    assert len(sources_with_fpo) >= 3, f"only {sources_with_fpo} reached the fpo tag"
