"""Checking what the published site actually serves.

This exists because of a specific failure in the predecessor, recorded in its own handover:
its pipeline job died before its Pages steps, so those steps were *skipped* rather than
failed. A skipped step is green. The site went stale and nothing reported it.

A check living inside the publishing job would have been skipped by the same failure, so
this one runs on its own schedule against the live origin and knows nothing about the run
that produced it. Its only inputs are bytes the public can fetch.

Everything here is a pure function over a fetched document, so the watchdog's logic is
covered by tests rather than by a YAML step nobody can run locally.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .clock import parse as parse_instant
from .fetch import FetchOutcome, Transport

#: How a finding is treated. ``fail`` means the site is not serving what it should;
#: ``warn`` means something is degraded but the site is still usable, which is exactly the
#: state a stale source leaves it in.
FAIL = "fail"
WARN = "warn"

#: How long a source may serve its last good feed before that stops being a blip. Six
#: hours is eighteen failed fetches at the daytime cadence -- generous enough that a
#: server reboot or a maintenance window does not page anyone, tight enough that nothing
#: sits broken for a whole working day unnoticed.
DEFAULT_MAX_STALE_MINUTES = 360


@dataclass(frozen=True)
class Finding:
    """One thing wrong with what the site is serving."""

    level: str
    path: str
    message: str


def parse_stamp(value: str) -> datetime | None:
    """Read a ``status.json`` timestamp, or ``None`` if it is not one.

    Shares its format with the code that writes it. Hardcoding it here once meant the
    producer and the consumer of the same string were defined separately, so a change to
    either would have this reporting a healthy site as serving a timestamp that is not one.
    """
    return parse_instant(value)


def check_freshness(
    document: Mapping[str, object], *, now: datetime, max_age_minutes: int
) -> list[Finding]:
    """Is the site being updated at all?

    The single most important question, and the one the predecessor could not answer: a
    consumer fetching a feed cannot distinguish fresh from frozen, because the feeds carry
    no timestamp -- deliberately, so they can be compared byte-for-byte. ``status.json`` is
    where the clock lives, so staleness of the whole site is measured here or nowhere.
    """
    raw = document.get("generatedAt")
    stamp = parse_stamp(raw) if isinstance(raw, str) else None
    if stamp is None:
        return [Finding(FAIL, "status.json", f"generatedAt is not a timestamp: {raw!r}")]

    age = (now - stamp).total_seconds() / 60
    if age > max_age_minutes:
        return [
            Finding(
                FAIL,
                "status.json",
                f"last published {age:.0f} minutes ago, over the {max_age_minutes}-minute "
                f"limit. The publishing workflow is not completing.",
            )
        ]
    if age < -5:
        # Clock skew large enough to make the age meaningless. Reported rather than
        # clamped, because a future timestamp means something is wrong with the producer.
        return [Finding(WARN, "status.json", f"generatedAt is {-age:.0f} minutes in the future")]
    return []


def check_declared_feeds(
    document: Mapping[str, object],
    *,
    now: datetime,
    max_stale_minutes: int = DEFAULT_MAX_STALE_MINUTES,
) -> list[Finding]:
    """Does the manifest itself report a problem?

    Read before fetching anything, because the site can be perfectly well served and still
    be publishing a week-old copy of a department -- a real finding even though every byte
    fetches with a 200.

    Staleness is graded by **duration** rather than treated as a yes/no. That distinction
    is the difference between a useful watchdog and an ignored one: a department whose
    server rebooted is stale for twenty minutes and needs nobody woken, while one stale for
    two days is an outage nobody has noticed. Reporting both the same way means either the
    first pages or the second does not, and both of those are wrong.
    """
    findings: list[Finding] = []
    feeds = document.get("feeds")
    if not isinstance(feeds, list):
        return [Finding(FAIL, "status.json", "no feeds array")]

    for record in feeds:
        if not isinstance(record, dict):
            continue
        path = str(record.get("path", "?"))
        detail = str(record.get("detail", "")) or "no reason given"

        if not record.get("stale"):
            if record.get("status") == "failed":
                # Failed with nothing to fall back on, so this path serves nothing at all.
                # Not merely degraded.
                findings.append(Finding(FAIL, path, f"the last run failed: {detail}"))
            continue

        age = _stale_minutes(record, now=now)
        if age is None:
            # No lastSuccessAt to measure against -- an older publish, or a feed that has
            # never succeeded. Warn, but never escalate on absent data.
            findings.append(Finding(WARN, path, f"serving a stale copy: {detail}"))
        elif age > max_stale_minutes:
            findings.append(
                Finding(
                    FAIL,
                    path,
                    f"stale for {_duration(age)} -- past the "
                    f"{_duration(max_stale_minutes)} limit, so this is an outage rather "
                    f"than a blip: {detail}",
                )
            )
        else:
            findings.append(
                Finding(WARN, path, f"stale for {_duration(age)}, still serving: {detail}")
            )
    return findings


def _stale_minutes(record: Mapping[str, object], *, now: datetime) -> float | None:
    """How long since this feed was last built from a live fetch."""
    raw = record.get("lastSuccessAt")
    moment = parse_stamp(raw) if isinstance(raw, str) else None
    return None if moment is None else (now - moment).total_seconds() / 60


def _duration(minutes: float) -> str:
    """Minutes as something a person reads without having to divide."""
    if minutes < 90:
        return f"{minutes:.0f} minutes"
    if minutes < 60 * 48:
        return f"{minutes / 60:.1f} hours"
    return f"{minutes / 1440:.1f} days"


def check_served(
    document: Mapping[str, object],
    base_url: str,
    transport: Transport,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: tuple[float, float] = (5.0, 15.0),
) -> list[Finding]:
    """Is every feed the manifest promises actually being served?

    The manifest is produced by the same run that wrote the feeds, so it agreeing with
    itself proves nothing. Fetching each path is what separates "we published" from "the
    deploy landed" -- the exact gap the predecessor's skipped steps fell into.
    """
    findings: list[Finding] = []
    feeds = document.get("feeds")
    if not isinstance(feeds, list):
        return findings

    for record in feeds:
        if not isinstance(record, dict) or record.get("status") == "disabled":
            continue
        path = str(record.get("path", ""))
        if not path:
            continue
        outcome = transport(
            f"{base_url.rstrip('/')}/{path}", headers=dict(headers or {}), timeout=timeout
        )
        findings.extend(_check_one(path, record, outcome))
    return findings


def _check_one(path: str, record: Mapping[str, object], outcome: FetchOutcome) -> Sequence[Finding]:
    if not outcome.ok:
        return [Finding(FAIL, path, f"not served: {outcome.error or outcome.status}")]
    try:
        payload = json.loads(outcome.body)
    except json.JSONDecodeError as exc:
        return [Finding(FAIL, path, f"served but not valid JSON: {exc}")]
    if not isinstance(payload, list):
        return [Finding(FAIL, path, f"served but not a JSON array: {type(payload).__name__}")]

    promised = record.get("events")
    if isinstance(promised, int) and len(payload) != promised:
        # The manifest and the feed came from one run and must agree. A mismatch means a
        # partial deploy: some paths updated and some did not, which no single fetch of
        # either file alone could reveal.
        return [
            Finding(
                FAIL,
                path,
                f"status.json promises {promised} events, the served feed has "
                f"{len(payload)}. The deploy is partial.",
            )
        ]
    return []


def check_landing_page(
    base_url: str,
    transport: Transport,
    *,
    headers: Mapping[str, str] | None = None,
) -> list[Finding]:
    """Is the root serving a page rather than a 404?

    Checked because it is the one path a person reaches by hand, and because nothing else
    would notice: every feed can be served perfectly while the root 404s, which is the
    state this site was in for its first three deploys. A warning rather than a failure --
    no consumer's ingest depends on it.
    """
    outcome = transport(
        f"{base_url.rstrip('/')}/", headers=dict(headers or {}), timeout=(5.0, 15.0)
    )
    if not outcome.ok:
        return [
            Finding(WARN, "/", f"the landing page is not served: {outcome.error or outcome.status}")
        ]
    if "<html" not in outcome.body[:2000].lower():
        return [Finding(WARN, "/", "the root is served but is not an HTML page")]
    return []


def verify(
    base_url: str,
    transport: Transport,
    *,
    now: datetime,
    max_age_minutes: int = 90,
    max_stale_minutes: int = DEFAULT_MAX_STALE_MINUTES,
    headers: Mapping[str, str] | None = None,
) -> tuple[list[Finding], Mapping[str, object] | None]:
    """Run every check against a live origin, returning findings and the manifest.

    Ordered deliberately: if ``status.json`` itself is unreachable there is nothing to
    check the feeds against, and reporting twenty fetch failures would bury the one fact
    that explains them all.
    """
    outcome = transport(
        f"{base_url.rstrip('/')}/status.json", headers=dict(headers or {}), timeout=(5.0, 15.0)
    )
    if not outcome.ok:
        return [
            Finding(FAIL, "status.json", f"not served: {outcome.error or outcome.status}")
        ], None
    try:
        document = json.loads(outcome.body)
    except json.JSONDecodeError as exc:
        return [Finding(FAIL, "status.json", f"not valid JSON: {exc}")], None
    if not isinstance(document, dict):
        return [Finding(FAIL, "status.json", "not a JSON object")], None

    findings = [
        *check_freshness(document, now=now, max_age_minutes=max_age_minutes),
        *check_declared_feeds(document, now=now, max_stale_minutes=max_stale_minutes),
        *check_served(document, base_url, transport, headers=headers),
        *check_landing_page(base_url, transport, headers=headers),
    ]
    return findings, document


def failures(findings: Sequence[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == FAIL]


def warnings(findings: Sequence[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == WARN]


__all__ = [
    "DEFAULT_MAX_STALE_MINUTES",
    "FAIL",
    "WARN",
    "Finding",
    "check_declared_feeds",
    "check_freshness",
    "check_landing_page",
    "check_served",
    "failures",
    "parse_stamp",
    "verify",
    "warnings",
]
