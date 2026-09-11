"""The build boundary and the CLI.

Two themes: a source that cannot be built correctly must fail rather than publish
something plausible, and one source's failure must not reach any other.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support import FIXTURES, REPO_ROOT
from upcoming.build import build_events, build_from_file, build_from_text, load_pronunciation
from upcoming.cli import EXIT_CONFIG, EXIT_OK, EXIT_SOURCE_FAILED, main
from upcoming.errors import SourceFatal

REGISTRY = str(REPO_ROOT / "config" / "sources.yaml")


def feed_path(slug: str) -> Path:
    return FIXTURES / "feeds" / slug / "feed.ics"


# --------------------------------------------------------------------------------------
# Refusing rather than guessing
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["citp", "quantum", "materials", "ai"])
def test_the_declared_rule_sources_build(slug: str, registry) -> None:
    """The four that refused before Phase 3.

    Each declares an ordered rule chain because its SUMMARY packs several fields into one
    string, or means different things on different events of the same feed.
    """
    load_pronunciation()
    result = build_from_file(feed_path(slug), registry.by_slug(slug))
    assert result.status == "ok", result.diagnostics
    assert result.counts["events"] > 0


def test_an_unimplemented_summary_role_still_refuses(registry) -> None:
    """The refusal that made Phase 2 honest is still in place for a role nobody built.

    Falling back to a plain title mapping would produce schema-valid output with the
    speaker buried inside the title -- a source that fails to build is a problem somebody
    fixes, and a source that builds wrongly is a problem nobody notices.
    """
    from dataclasses import replace

    source = registry.by_slug("mae")
    bogus = replace(source, expectations=replace(source.expectations, summary_role="someday"))
    result = build_from_file(feed_path("mae"), bogus)
    assert result.status == "failed"
    assert "not implemented" in result.diagnostics[0]


def test_a_platform_mismatch_refuses_to_build(registry) -> None:
    """A feed that changes platform under a stable URL changes what every field means.

    Exactly kellercenter's situation: `-//Drupal iCal API//EN` served from a path that
    looks like every Site Builder feed.
    """
    text = (
        feed_path("mae")
        .read_text(encoding="utf-8")
        .replace("PRODID:princeton-site-builder", "PRODID:-//Drupal iCal API//EN")
    )
    result = build_from_text(text, registry.by_slug("mae"))
    assert result.status == "failed"
    assert "PRODID" in result.diagnostics[0]


def test_an_unknown_timezone_refuses_rather_than_publishing_a_wrong_time(registry) -> None:
    """The predecessor falls through with the unconverted value.

    Its conversion sits in a bare `except Exception`, so a typo'd zone publishes every
    event four or five hours off with nothing reporting it.
    """
    from dataclasses import replace

    source = replace(registry.by_slug("mae"), timezone="Not/AZone")
    with pytest.raises(SourceFatal, match="IANA zone"):
        build_events(feed_path("mae").read_text(encoding="utf-8"), source)


def test_an_event_without_a_start_fails_its_source(registry) -> None:
    text = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\n"
        "SUMMARY:No times\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    result = build_from_text(text, registry.by_slug("mae"))
    assert result.status == "failed"
    assert "DTSTART" in result.diagnostics[0]


def test_a_missing_feed_file_fails_that_source_only(registry) -> None:
    result = build_from_file("/nonexistent/feed.ics", registry.by_slug("mae"))
    assert result.status == "failed"
    assert "cannot read" in result.diagnostics[0]


def test_one_source_failing_leaves_another_buildable(registry) -> None:
    """The whole point of the per-source boundary."""
    broken = build_from_file("/nonexistent/feed.ics", registry.by_slug("mae"))
    healthy = build_from_file(feed_path("orfe"), registry.by_slug("orfe"))
    assert broken.status == "failed"
    assert healthy.status == "ok"
    assert healthy.counts["events"] == 14


# --------------------------------------------------------------------------------------
# Counts reported honestly
# --------------------------------------------------------------------------------------


def test_the_result_reports_how_many_titles_were_invented(registry) -> None:
    """A number a human can act on, rather than a silent success.

    ORFE's feed carries no title on any event, so all 14 are synthesized -- normal here,
    and alarming anywhere else.
    """
    load_pronunciation()
    orfe = build_from_file(feed_path("orfe"), registry.by_slug("orfe"))
    assert orfe.counts["events"] == 14
    assert orfe.counts["placeholder_titles"] == 14

    mae = build_from_file(feed_path("mae"), registry.by_slug("mae"))
    assert mae.counts["events"] == 9
    assert mae.counts["placeholder_titles"] == 1, "only the TBD event"


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_build_writes_a_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_BYPASS_HEADER", "x-cli-test: placeholder")
    out = tmp_path / "events.json"
    code = main(
        [
            "--registry",
            REGISTRY,
            "build",
            "--source",
            "mae",
            "--feed",
            str(feed_path("mae")),
            "--out",
            str(out),
        ]
    )
    assert code == EXIT_OK
    records = json.loads(out.read_text(encoding="utf-8"))
    assert len(records) == 9
    assert all(r["title"] for r in records), "a published title is never empty"


def test_build_exits_distinctly_when_a_source_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A data failure and a config failure need different people to fix them."""
    monkeypatch.setenv("BOT_BYPASS_HEADER", "x-cli-test: placeholder")
    code = main(
        [
            "--registry",
            REGISTRY,
            "build",
            "--source",
            "mae",
            "--feed",
            str(tmp_path / "absent.ics"),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert code == EXIT_SOURCE_FAILED
    assert "cannot read" in capsys.readouterr().err


def test_build_refuses_a_source_declared_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`cs` has no feed, and the reason travels with the refusal."""
    monkeypatch.setenv("BOT_BYPASS_HEADER", "x-cli-test: placeholder")
    code = main(
        ["--registry", REGISTRY, "build", "--source", "cs", "--feed", str(feed_path("orfe"))]
    )
    assert code == EXIT_CONFIG
    assert "unavailable" in capsys.readouterr().err


def test_build_writes_to_stdout_on_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BOT_BYPASS_HEADER", "x-cli-test: placeholder")
    code = main(
        [
            "--registry",
            REGISTRY,
            "build",
            "--source",
            "mae",
            "--feed",
            str(feed_path("mae")),
            "--out",
            "-",
        ]
    )
    assert code == EXIT_OK
    assert len(json.loads(capsys.readouterr().out)) == 9


def test_the_written_feed_ends_in_a_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So it is a well-formed text file and a diff does not flag every change."""
    monkeypatch.setenv("BOT_BYPASS_HEADER", "x-cli-test: placeholder")
    out = tmp_path / "events.json"
    main(
        [
            "--registry",
            REGISTRY,
            "build",
            "--source",
            "mae",
            "--feed",
            str(feed_path("mae")),
            "--out",
            str(out),
        ]
    )
    assert out.read_text(encoding="utf-8").endswith("]\n")


# --------------------------------------------------------------------------------------
# Every live source
# --------------------------------------------------------------------------------------


def test_every_live_source_builds(registry) -> None:
    """All twelve, offline from committed fixtures.

    The four that declare rule chains were refusing to build before Phase 3; the other
    eight use the two direct roles. Nothing here branches on a source slug -- the
    difference is entirely `config/sources.yaml`.
    """
    load_pronunciation()
    built = {}
    for source in registry.live:
        feed = FIXTURES / "feeds" / source.slug / "feed.ics"
        assert feed.is_file(), f"{source.slug} is live but has no committed fixture"
        result = build_from_file(feed, source)
        assert result.status == "ok", f"{source.slug}: {result.diagnostics}"
        built[source.slug] = result.counts["events"]

    assert len(built) == 12, f"expected 12 live sources, built {sorted(built)}"
    assert sum(built.values()) >= 110


def test_a_legitimately_empty_feed_builds_rather_than_failing(registry) -> None:
    """kellercenter serves a well-formed calendar with no events.

    Empty and broken produce identical payloads, so only the source's declared
    `allow_empty` can tell them apart -- and a count floor it could never satisfy would
    leave it permanently red, which teaches everyone to ignore the alerts.
    """
    source = registry.by_slug("kellercenter")
    assert source.expectations.allow_empty is True

    result = build_from_file(FIXTURES / "feeds" / "kellercenter" / "feed.ics", source)
    assert result.status == "ok"
    assert result.counts["events"] == 0


def test_a_non_site_builder_feed_is_not_forced_through_the_site_builder_check(registry) -> None:
    """kellercenter is `-//Drupal iCal API//EN`, and says so in its config."""
    assert registry.by_slug("kellercenter").platform == "drupal-ical-api"
