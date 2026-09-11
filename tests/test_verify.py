"""The watchdog: what the published site actually serves.

Every test here is offline. The point of the module is that its logic is testable at all --
the predecessor's equivalent lives in a YAML step, so the only way to find out whether it
catches a partial deploy is to have one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from upcoming.fetch import FetchOutcome
from upcoming.verify import (
    FAIL,
    WARN,
    check_declared_feeds,
    check_freshness,
    failures,
    parse_stamp,
    verify,
    warnings,
)

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
BASE = "https://upcoming.example.edu"


def status(generated_at: str = "2026-09-11T11:45:00Z", feeds: list | None = None) -> dict:
    return {
        "generatedAt": generated_at,
        "summary": {"ok": 2, "events": 3},
        "feeds": feeds
        if feeds is not None
        else [
            {"path": "feeds/orfe/events.json", "status": "ok", "events": 2, "sources": ["orfe"]},
            {"path": "combos/all/events.json", "status": "ok", "events": 3},
        ],
    }


@dataclass
class SiteTransport:
    """A site serving a given set of paths. Anything else 404s, as a real origin would."""

    files: dict[str, str] = field(default_factory=dict)
    down: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def __call__(self, url, *, headers, timeout):  # type: ignore[no-untyped-def]
        path = url[len(BASE) + 1 :]
        self.calls.append(path)
        if path in self.down:
            return FetchOutcome("network_error", url, error="connection refused")
        if path not in self.files:
            return FetchOutcome("http_error", url, code=404, error="HTTP 404")
        return FetchOutcome("ok", url, code=200, body=self.files[path])


def site(
    document: Mapping | None = None, overrides: Mapping[str, str] | None = None
) -> SiteTransport:
    document = status() if document is None else document
    files = {"status.json": json.dumps(document), "": "<!doctype html><html></html>"}
    for record in document.get("feeds", []):
        if record.get("status") != "disabled":
            files[record["path"]] = json.dumps([{"id": f"x:{i}"} for i in range(record["events"])])
    files.update(overrides or {})
    return SiteTransport(files=files)


# --------------------------------------------------------------------------------------
# The healthy site
# --------------------------------------------------------------------------------------


def test_a_healthy_site_reports_nothing() -> None:
    findings, document = verify(BASE, site(), now=NOW)
    assert findings == []
    assert document is not None


def test_every_non_disabled_path_is_actually_fetched() -> None:
    """The manifest agreeing with itself proves nothing; it was written by the same run."""
    transport = site()
    verify(BASE, transport, now=NOW)
    assert transport.calls == [
        "status.json",
        "feeds/orfe/events.json",
        "combos/all/events.json",
        "",  # the landing page, which nothing else would notice missing
    ]


def test_a_disabled_feed_is_not_fetched() -> None:
    """Nothing is served at a disabled path, so fetching one would report a false 404."""
    document = status(feeds=[{"path": "feeds/cs/events.json", "status": "disabled", "events": 0}])
    transport = site(document)
    findings, _ = verify(BASE, transport, now=NOW)
    assert findings == []
    assert transport.calls == ["status.json", ""]


# --------------------------------------------------------------------------------------
# The failure the predecessor could not see
# --------------------------------------------------------------------------------------


def test_an_unreachable_status_is_the_only_thing_reported() -> None:
    """One fact explains the rest; twenty fetch failures would bury it."""
    transport = SiteTransport(down={"status.json"})
    findings, document = verify(BASE, transport, now=NOW)
    assert document is None
    assert len(findings) == 1
    assert findings[0].level == FAIL
    assert transport.calls == ["status.json"]


def test_a_site_that_stopped_updating_fails() -> None:
    """The single most important question, and the one a feed alone cannot answer."""
    findings, _ = verify(BASE, site(status("2026-09-11T06:00:00Z")), now=NOW, max_age_minutes=90)
    assert [f.level for f in failures(findings)] == [FAIL]
    assert "360 minutes ago" in failures(findings)[0].message


def test_a_partial_deploy_is_caught_by_the_count_disagreeing() -> None:
    """Some paths updated and some did not.

    No single fetch reveals this: status.json is internally consistent and each feed is
    valid JSON. Only the two read together disagree.
    """
    transport = site(overrides={"feeds/orfe/events.json": json.dumps([{"id": "x:0"}])})
    findings, _ = verify(BASE, transport, now=NOW)
    bad = failures(findings)
    assert len(bad) == 1
    assert bad[0].path == "feeds/orfe/events.json"
    assert "partial" in bad[0].message


def test_a_promised_feed_that_is_not_served_fails() -> None:
    transport = site()
    del transport.files["combos/all/events.json"]
    bad = failures(verify(BASE, transport, now=NOW)[0])
    assert [f.path for f in bad] == ["combos/all/events.json"]


def test_a_feed_served_as_something_other_than_an_array_fails() -> None:
    transport = site(overrides={"feeds/orfe/events.json": '{"events": []}'})
    bad = failures(verify(BASE, transport, now=NOW)[0])
    assert "not a JSON array" in bad[0].message


def test_a_truncated_feed_fails_rather_than_reading_as_empty() -> None:
    transport = site(overrides={"feeds/orfe/events.json": '[{"id": "x:0"'})
    bad = failures(verify(BASE, transport, now=NOW)[0])
    assert "not valid JSON" in bad[0].message


# --------------------------------------------------------------------------------------
# Degraded but usable
# --------------------------------------------------------------------------------------


def test_a_stale_source_warns_rather_than_fails() -> None:
    """The site is working. One department is behind, and that is worth saying, not paging.

    Failing here would make the watchdog red for as long as an upstream outage lasts,
    which trains everyone to ignore it -- the failure mode that lets a real outage through.
    """
    document = status(
        feeds=[
            {
                "path": "feeds/orfe/events.json",
                "status": "failed",
                "events": 2,
                "stale": True,
                "detail": "upstream returned 503",
            }
        ]
    )
    findings, _ = verify(BASE, site(document), now=NOW)
    assert failures(findings) == []
    assert len(warnings(findings)) == 1
    assert "503" in warnings(findings)[0].message


def test_a_source_that_failed_with_nothing_to_serve_is_a_failure_not_a_warning() -> None:
    """Nothing is being served at that path at all, which is not merely degraded."""
    document = status(
        feeds=[
            {
                "path": "feeds/orfe/events.json",
                "status": "failed",
                "events": 0,
                "detail": "upstream returned 503. Never published.",
            }
        ]
    )
    findings = check_declared_feeds(document)
    assert [f.level for f in findings] == [FAIL]


# --------------------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["", "not a time", "2026-09-11T12:00:00+00:00", "2026-09-11 12:00:00Z", None]
)
def test_a_timestamp_that_is_not_the_published_format_is_rejected(value: object) -> None:
    assert parse_stamp(value) is None  # type: ignore[arg-type]


def test_a_missing_generated_at_fails_rather_than_reading_as_fresh() -> None:
    findings = check_freshness({}, now=NOW, max_age_minutes=90)
    assert [f.level for f in findings] == [FAIL]


def test_a_future_timestamp_warns_rather_than_being_clamped() -> None:
    """A negative age means something is wrong with the producer, not with the site."""
    future = (NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    findings = check_freshness({"generatedAt": future}, now=NOW, max_age_minutes=90)
    assert [f.level for f in findings] == [WARN]


def test_the_age_limit_is_exclusive_at_the_boundary() -> None:
    exactly = (NOW - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert check_freshness({"generatedAt": exactly}, now=NOW, max_age_minutes=90) == []
    over = (NOW - timedelta(minutes=91)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert check_freshness({"generatedAt": over}, now=NOW, max_age_minutes=90) != []
