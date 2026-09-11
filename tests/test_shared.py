"""The two things every layer agreed on separately until they didn't have to.

Both modules here exist because the same knowledge was written down in several places. The
tests are about the seam: that one definition really is the only one, and that the pairs
which have to agree — the writer and the reader of an instant, the five config loaders —
now agree by construction rather than by coincidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.support import FIXTURES, REPO_ROOT
from upcoming.clock import INSTANT_FORMAT, INSTANT_WIDTH, parse, stamp
from upcoming.config import read_mapping
from upcoming.errors import ConfigFatal

MOMENT = datetime(2026, 9, 11, 17, 32, 49, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# The instant format
# --------------------------------------------------------------------------------------


def test_an_instant_round_trips() -> None:
    assert parse(stamp(MOMENT)) == MOMENT


def test_a_local_instant_is_converted_rather_than_relabelled() -> None:
    """Writing a New York wall time under a Z would be a lie in the one unambiguous format."""
    from zoneinfo import ZoneInfo

    local = MOMENT.astimezone(ZoneInfo("America/New_York"))
    assert local.hour != MOMENT.hour
    assert stamp(local) == stamp(MOMENT)


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-11T17:32:49+00:00",  # offset spelling
        "2026-09-11T17:32:49.123Z",  # sub-second
        "2026-09-11 17:32:49Z",  # space separator
        "not a time",
        "",
    ],
)
def test_a_near_miss_is_rejected_rather_than_coerced(value: str) -> None:
    """These strings sort inconsistently against ours, so accepting them would corrupt order."""
    assert parse(value) is None


def test_the_width_constant_matches_the_format() -> None:
    assert len(stamp(MOMENT)) == INSTANT_WIDTH
    assert INSTANT_FORMAT.endswith("Z")


def test_the_watchdog_can_read_what_publishing_writes(registry, tmp_path):  # type: ignore[no-untyped-def]
    """The closed loop, and the reason this module exists.

    The producer and the consumer of `generatedAt` were separately hardcoded. A change to
    either would have the watchdog report a healthy site as serving a timestamp that is not
    one — a false alarm indistinguishable from the real thing it exists to catch.
    """
    from upcoming.build import build_from_file, load_pronunciation
    from upcoming.publish import assemble, utc_now
    from upcoming.verify import check_freshness, parse_stamp

    load_pronunciation()
    results = {
        s.slug: build_from_file(FIXTURES / "feeds" / s.slug / "feed.ics", s) for s in registry.live
    }
    written = utc_now()
    tree = assemble(registry, results, {}, {}, root=tmp_path, generated_at=written)
    document = json.loads(tree.files["status.json"])

    read_back = parse_stamp(document["generatedAt"])
    assert read_back is not None
    assert check_freshness(document, now=read_back + timedelta(minutes=1), max_age_minutes=90) == []


# --------------------------------------------------------------------------------------
# Reading a config file
# --------------------------------------------------------------------------------------


def test_a_missing_required_file_names_what_was_expected(tmp_path):  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigFatal, match="no tag vocabulary at"):
        read_mapping(tmp_path / "gone.yaml", what="tag vocabulary", allow={"tags"})


def test_a_missing_optional_file_is_empty_not_an_error(tmp_path):  # type: ignore[no-untyped-def]
    """Some configs carry a working default in code, so absent is a choice."""
    assert read_mapping(tmp_path / "gone.yaml", what="x", allow={"a"}, required=False) == {}


def test_a_malformed_optional_file_still_fails(tmp_path):  # type: ignore[no-untyped-def]
    """Absent is a choice; malformed never is.

    Returning {} here would let a broken file read as "you didn't configure this", and the
    default would quietly apply instead of the settings someone wrote.
    """
    bad = tmp_path / "broken.yaml"
    bad.write_text("a: [1, 2\nb: 3")
    with pytest.raises(ConfigFatal, match="not valid YAML"):
        read_mapping(bad, what="x", allow={"a", "b"}, required=False)


def test_an_unknown_top_level_key_is_a_load_error(tmp_path):  # type: ignore[no-untyped-def]
    """The step worth protecting: a typo must not become a setting that never applies."""
    config = tmp_path / "c.yaml"
    config.write_text("tagz:\n  seminar: {}\n")
    with pytest.raises(ConfigFatal, match=r"unknown top-level keys \['tagz'\]"):
        read_mapping(config, what="tag vocabulary", allow={"tags"})


def test_the_error_lists_what_would_have_been_accepted(tmp_path):  # type: ignore[no-untyped-def]
    config = tmp_path / "c.yaml"
    config.write_text("nope: 1\n")
    with pytest.raises(ConfigFatal, match=r"Known: \['combos'\]"):
        read_mapping(config, what="combo configuration", allow={"combos"})


def test_a_file_that_is_not_a_mapping_is_refused(tmp_path):  # type: ignore[no-untyped-def]
    config = tmp_path / "c.yaml"
    config.write_text("- one\n- two\n")
    with pytest.raises(ConfigFatal, match="must be a mapping, not list"):
        read_mapping(config, what="x", allow={"a"})


def test_an_empty_file_reads_as_an_empty_mapping(tmp_path):  # type: ignore[no-untyped-def]
    config = tmp_path / "c.yaml"
    config.write_text("# only a comment\n")
    assert read_mapping(config, what="x", allow={"a"}) == {}


def test_every_config_loader_goes_through_the_shared_reader() -> None:
    """A sixth loader must not be able to reintroduce the block this replaced.

    The unknown-key check is the part most likely to be left out of a hand-rolled copy, and
    it is the part that turns a typo in config/ into an error instead of a silent no-op.
    """
    package = REPO_ROOT / "upcoming"
    offenders = [
        path.name
        for path in package.glob("*.py")
        if path.name != "config.py" and "yaml.safe_load" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("config/sources.yaml", {"defaults", "sources", "purposes"}),
        ("config/tags.yaml", {"tags"}),
        ("config/combos.yaml", {"combos"}),
        ("config/patterns.yaml", {"patterns", "not_a_person"}),
    ],
)
def test_the_shipped_configs_use_only_keys_their_loader_accepts(path: str, allowed: set) -> None:
    document = read_mapping(REPO_ROOT / path, what=path, allow=allowed)
    assert set(document) <= allowed
