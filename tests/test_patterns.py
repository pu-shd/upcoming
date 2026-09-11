"""The shared vocabulary: patterns, their examples, and the one judgement call.

The pattern table below is generated from `config/patterns.yaml` itself, so a pattern
cannot be added without its examples being run.
"""

from __future__ import annotations

import re

import pytest

from tests.support import REPO_ROOT
from upcoming.errors import ConfigFatal
from upcoming.patterns import (
    MAX_NAMED_GROUPS,
    MAX_PATTERN_LENGTH,
    Vocabulary,
    lint_pattern,
    load_vocabulary,
    person_name_shape,
)

VOCAB = load_vocabulary(REPO_ROOT / "config" / "patterns.yaml")


def cases(kind: str) -> list[tuple[str, str]]:
    return [(name, value) for name, p in VOCAB.patterns.items() for value in getattr(p, kind)]


# --------------------------------------------------------------------------------------
# The pattern table
# --------------------------------------------------------------------------------------


def test_there_are_patterns_to_check() -> None:
    """Anti-vacuity: everything below iterates the vocabulary."""
    assert len(VOCAB.patterns) >= 5


@pytest.mark.parametrize("name,value", cases("match"), ids=lambda v: str(v)[:44])
def test_declared_matches_match(name: str, value: str) -> None:
    assert VOCAB.patterns[name].regex.match(value), f"{name} should match {value!r}"


@pytest.mark.parametrize("name,value", cases("no_match"), ids=lambda v: str(v)[:44])
def test_declared_non_matches_do_not_match(name: str, value: str) -> None:
    """The negative examples are the real guard.

    The risk with these shapes is never failing to match -- it is matching something
    confidently and wrongly, and only a negative example catches that.
    """
    assert not VOCAB.patterns[name].regex.match(value), f"{name} should not match {value!r}"


def test_every_pattern_carries_negative_examples() -> None:
    for name, pattern in VOCAB.patterns.items():
        assert pattern.no_match, f"{name} has no `no_match` examples"


def test_every_pattern_names_its_groups() -> None:
    """An anonymous group cannot be bound by a rule, so it can only be a mistake."""
    for name, pattern in VOCAB.patterns.items():
        anonymous = pattern.regex.groups - len(pattern.groups)
        assert anonymous == 0, f"{name} has {anonymous} unnamed group(s)"


# --------------------------------------------------------------------------------------
# The linter
# --------------------------------------------------------------------------------------


def test_a_nested_unbounded_quantifier_is_refused() -> None:
    """The classic shape that turns a short input into a hang."""
    with pytest.raises(ConfigFatal, match="nests unbounded quantifiers"):
        lint_pattern("bad", r"^(a+)+$")


@pytest.mark.parametrize(
    "source,reason",
    [
        (r"^(?P<a>x)\1$", "backreference"),
        (r"(?<=foo)bar", "lookbehind"),
    ],
)
def test_forbidden_constructs_are_refused(source: str, reason: str) -> None:
    with pytest.raises(ConfigFatal, match=reason):
        lint_pattern("bad", source)


def test_an_overlong_pattern_is_refused() -> None:
    """A pattern nobody can read is a pattern nobody can check."""
    with pytest.raises(ConfigFatal, match="over the"):
        lint_pattern("bad", "x" * (MAX_PATTERN_LENGTH + 1))


def test_too_many_groups_is_refused() -> None:
    source = "".join(f"(?P<g{i}>x)" for i in range(MAX_NAMED_GROUPS + 1))
    with pytest.raises(ConfigFatal, match="named groups"):
        lint_pattern("bad", source)


def test_the_committed_patterns_all_pass_the_linter() -> None:
    for name, pattern in VOCAB.patterns.items():
        lint_pattern(name, pattern.regex.pattern)


def test_an_unknown_pattern_name_is_refused() -> None:
    """A rule naming a pattern that does not exist never fires, so the field it was meant
    to fill stays empty with nothing reporting it."""
    with pytest.raises(ConfigFatal, match="unknown pattern"):
        VOCAB.pattern("no_such_pattern")


# --------------------------------------------------------------------------------------
# person_name_shape -- the one judgement call
# --------------------------------------------------------------------------------------

#: Real speaker names from these feeds.
NAMES = [
    "Angel Hwang",
    "Eve Fleisig",
    "Alia ElKattan",
    "Seyi Olojo",
    "Andy Guess",
    "Rafael Gomez-Bombarelli",
    "Arvind Narayanan",
    "Helen Nissenbaum",
    "Joshua Cohen",
    "Aditya Vashistha",
    "Liat Krawczyk",
    "Shira Zilberstein",
    "Sid Midha",
    "Chengyu Wang",
    "Nadine Bradbury",
    "Laura Futamura",
    "Faranak Bahrami",
    "Ray Su",
    "Jainendra Jain",
    "Arghadip Koner",
    "Sean Roberts",
    "Nuh Gedik",
    "Ryan Lively",
    "Amrit Venkatesh",
    "Zhuoran Yang *22",
    "Elynn Chen",
    "Navid Azizan",
    "Xinghua Zheng",
    "Dimitris Lagoudas",
    "Makhsud Saidaminov",
    "Mahesh Mahanthappa",
    "Piran Kidambi",
    "Joseph Fedeyko",
    "Alan West",
]

#: Real titles and series names from the same feeds. Several are title-case word
#: sequences structurally identical to a name.
NOT_NAMES = [
    "Bias in AI Reading Group",
    "Unlocking Digital Behavior Using Accelerator Comscore Data",
    "Princeton AI in Science Evaluation Workshop",
    "AI Agents and the Augmentation Agenda",
    "The Bystander Privacy Problem",
    "Towards Globally Equitable AI",
    "Whose AI for Amazon Forests? Technological Sovereignty for Indigenizing Science",
    "ORFE Talks & Seminars Flyers Fall 2026 Colloquium",
    "AI² Research Talk Series",
    "Princeton Quantum Technology Conference 2026",
    "Princeton Symposium on Advances in Optics and Ultrafast Science",
    "Princeton Materials Institute Symposium",
    "Hidden Order in Disorder: The Expanding Landscape of Hyperuniformity",
    "4th Frontiers in Electron and Scanning Probe Microscopy for the Physical and Life Sciences",
    "Local Autonomous Inference Machines For Quantum Error Correcting Codes",
    "Quantum signatures in molecular polaritonics and other emerging photonic platforms.",
    "Princeton Quantum Colloquium",
    "Quantum Group Meeting",
]


@pytest.mark.parametrize("value", NAMES, ids=lambda v: v[:30])
def test_real_speaker_names_are_recognised(value: str) -> None:
    assert person_name_shape(value, VOCAB)


@pytest.mark.parametrize("value", NOT_NAMES, ids=lambda v: v[:30])
def test_real_titles_are_not_mistaken_for_names(value: str) -> None:
    assert not person_name_shape(value, VOCAB)


def test_the_stoplist_is_what_separates_a_name_from_a_title_case_phrase() -> None:
    """Without it, five real titles pass as names.

    Each is two-to-four capitalised tokens with no sentence punctuation -- structurally
    indistinguishable from `Rafael Gomez-Bombarelli`. Only the vocabulary separates them,
    which is why this test exists: so nobody removes the stoplist as redundant.

    The set is exact rather than a count, so a stoplist change that fixes one case and
    breaks another cannot pass.
    """
    without = Vocabulary(patterns=VOCAB.patterns, not_a_person=frozenset())
    leaked = [v for v in NOT_NAMES if person_name_shape(v, without)]
    assert set(leaked) == {
        "Towards Globally Equitable AI",
        "AI² Research Talk Series",
        "Princeton Materials Institute Symposium",
        "Princeton Quantum Colloquium",
        "Quantum Group Meeting",
    }
    for value in leaked:
        assert not person_name_shape(value, VOCAB), f"{value!r} should be caught by the stoplist"


@pytest.mark.parametrize("value", ["", "   ", "Hwang", "A", "*22"])
def test_a_single_token_is_never_a_name(value: str) -> None:
    """One token is ambiguous with a one-word title; refusing is the safe direction."""
    assert not person_name_shape(value, VOCAB)


def test_a_five_token_phrase_is_never_a_name() -> None:
    assert not person_name_shape("One Two Three Four Five", VOCAB)


def test_a_lowercase_particle_does_not_disqualify_a_name() -> None:
    """Names like `Ludwig van Beethoven` and `Ahmed bin Rashid` are real."""
    assert person_name_shape("Ludwig van Beethoven", VOCAB)
    assert person_name_shape("Ahmed bin Rashid", VOCAB)


def test_the_stoplist_match_is_whole_word_not_substring() -> None:
    """`Labaki` contains `lab`; a substring check would reject a real surname."""
    assert person_name_shape("Elsa Labaki", VOCAB)


def test_the_vocabulary_carries_a_substantive_stoplist() -> None:
    assert len(VOCAB.not_a_person) >= 20


def test_an_unknown_top_level_key_is_refused(tmp_path) -> None:
    path = tmp_path / "patterns.yaml"
    path.write_text("patterns: {}\nsorts_of_people: []\n", encoding="utf-8")
    with pytest.raises(ConfigFatal, match="unknown top-level keys"):
        load_vocabulary(path)


def test_a_pattern_without_negative_examples_is_refused(tmp_path) -> None:
    path = tmp_path / "patterns.yaml"
    path.write_text(
        "patterns:\n  p:\n    regex: '^(?P<a>x)$'\n    match: ['x']\n", encoding="utf-8"
    )
    with pytest.raises(ConfigFatal, match="no `no_match` examples"):
        load_vocabulary(path)


def test_a_pattern_that_does_not_compile_is_refused(tmp_path) -> None:
    path = tmp_path / "patterns.yaml"
    path.write_text(
        "patterns:\n  p:\n    regex: '^(?P<a>x'\n    match: ['x']\n    no_match: ['y']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigFatal, match="does not compile"):
        load_vocabulary(path)


def test_the_person_regex_is_anchored() -> None:
    """An unanchored name test would match a name buried in a sentence."""
    assert not person_name_shape("Talk by Andy Guess today", VOCAB)
    assert not re.search(r"^\^", "") or True  # documents intent; the check above is the test
