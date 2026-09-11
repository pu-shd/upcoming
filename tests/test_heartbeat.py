"""Keeping the schedules alive.

The failure this guards against is the most complete one available: GitHub stops running
every scheduled workflow after 60 days of repository quiet, and nothing in the repository
says so. The site stops updating, the watchdog stops watching, and the workflow list stays
green because nothing ran to go red.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from tests.support import REPO_ROOT
from upcoming.heartbeat import (
    ACTIVE,
    DEFAULT_THRESHOLD_DAYS,
    DISABLE_AFTER_DAYS,
    DISABLED_FOR_INACTIVITY,
    WorkflowState,
    decide,
    keepalive,
    parse_states,
    report,
    scheduled_workflows,
    stopped,
)

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


# --------------------------------------------------------------------------------------
# When to commit
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("days", [0, 1, 20, 34.9])
def test_an_active_repository_needs_no_keepalive(days: float) -> None:
    """Ordinary development is activity.

    Committing anyway would put a keepalive in the history of every active week, which is
    noise that trains reviewers to skim past exactly the commit that matters.
    """
    assert decide(ago(days), now=NOW).needed is False


@pytest.mark.parametrize("days", [35, 40, 59])
def test_a_quiet_repository_gets_one(days: float) -> None:
    decision = decide(ago(days), now=NOW)
    assert decision.needed is True
    assert f"{DISABLE_AFTER_DAYS - days:.1f} days from" in decision.reason


def test_the_threshold_leaves_real_slack() -> None:
    """35 against a 60-day limit is 25 days of margin.

    Enough to survive a long holiday plus several consecutive failed heartbeat runs. A
    threshold at 55 would be arithmetically fine and operationally useless.
    """
    assert DISABLE_AFTER_DAYS - DEFAULT_THRESHOLD_DAYS >= 21


def test_past_the_limit_the_message_stops_pretending_to_be_a_countdown() -> None:
    """ "-393 days remaining" is arithmetic impersonating a warning."""
    decision = decide(ago(453), now=NOW)
    assert decision.needed is True
    assert "already been disabled" in decision.reason
    assert not re.search(r"-\d", decision.reason), decision.reason


def test_a_commit_dated_in_the_future_is_reported_not_treated_as_activity() -> None:
    """A clock that wrong makes every other number here meaningless."""
    decision = decide(NOW + timedelta(days=3), now=NOW)
    assert decision.needed is False
    assert "ahead" in decision.reason


def test_the_keepalive_explains_itself() -> None:
    """Someone finding this commit in a year must not have to read the workflow."""
    document = json.loads(keepalive(decide(ago(40), now=NOW), now=NOW, ref="main", sha="abc"))
    assert str(DISABLE_AFTER_DAYS) in document["purpose"]
    assert document["quietDays"] == 40.0
    assert document["sourceSha"] == "abc"
    assert document["writtenAt"] == "2026-09-11T12:00:00Z"


# --------------------------------------------------------------------------------------
# Noticing a stopped schedule
# --------------------------------------------------------------------------------------


def test_the_expected_set_is_read_from_the_files_not_the_api() -> None:
    """What was *intended*, so a schedule removed in a commit is not reported as broken."""
    found = scheduled_workflows(
        {p.name: p.read_text(encoding="utf-8") for p in WORKFLOWS.glob("*.yml")}
    )
    assert found == ["heartbeat.yml", "publish.yml", "verify.yml"]
    assert "tests.yml" not in found  # runs on push and pull_request, never on a schedule


def test_every_scheduled_workflow_is_checked_by_the_heartbeat() -> None:
    """A new scheduled workflow must not be able to go unwatched by being forgotten."""
    declared = {
        p.name for p in WORKFLOWS.glob("*.yml") if "- cron:" in p.read_text(encoding="utf-8")
    }
    assert declared == set(
        scheduled_workflows(
            {p.name: p.read_text(encoding="utf-8") for p in WORKFLOWS.glob("*.yml")}
        )
    )


def test_an_active_schedule_reports_nothing() -> None:
    states = [WorkflowState("Publish", ".github/workflows/publish.yml", ACTIVE)]
    assert stopped(states, ["publish.yml"]) == []
    assert report([]) == "every scheduled workflow is active"


def test_a_schedule_disabled_for_inactivity_is_reported_with_that_reason() -> None:
    states = [WorkflowState("Publish", ".github/workflows/publish.yml", DISABLED_FOR_INACTIVITY)]
    found = stopped(states, ["publish.yml"])
    assert len(found) == 1
    assert "repository inactivity" in report(found)


def test_a_schedule_someone_turned_off_by_hand_is_reported_too() -> None:
    """Turning the publisher off is legitimate; leaving it off silently is not.

    A heartbeat that stayed quiet about a manual disable would be picking the wrong half
    of that, and the site would sit frozen with nothing saying why.
    """
    states = [WorkflowState("Publish", ".github/workflows/publish.yml", "disabled_manually")]
    found = stopped(states, ["publish.yml"])
    assert len(found) == 1
    assert "disabled_manually" in report(found)


def test_a_workflow_we_do_not_schedule_is_not_our_problem() -> None:
    states = [WorkflowState("CodeQL", ".github/workflows/codeql.yml", "disabled_manually")]
    assert stopped(states, ["publish.yml", "verify.yml"]) == []


def test_the_api_listing_is_read_defensively() -> None:
    """A shape change upstream must not read as "nothing is stopped"."""
    assert parse_states({}) == []
    assert parse_states({"workflows": "not a list"}) == []
    assert parse_states({"workflows": [{"name": "P", "path": "p.yml", "state": ACTIVE}]}) == [
        WorkflowState("P", "p.yml", ACTIVE)
    ]


def test_states_are_matched_on_basename_not_full_path() -> None:
    """The API returns `.github/workflows/publish.yml`; the file glob gives `publish.yml`."""
    states = [WorkflowState("Publish", ".github/workflows/publish.yml", "disabled_manually")]
    assert len(stopped(states, ["publish.yml"])) == 1


# --------------------------------------------------------------------------------------
# The workflow
# --------------------------------------------------------------------------------------


def test_the_heartbeat_can_repair_what_it_finds() -> None:
    """Reporting a stopped schedule without restarting it leaves the site down."""
    body = (WORKFLOWS / "heartbeat.yml").read_text(encoding="utf-8")
    assert "actions: write" in body
    assert "/enable" in body


def test_the_heartbeat_states_the_limit_it_cannot_cover() -> None:
    """If every scheduled workflow is disabled, this one is too and cannot rescue itself.

    Inherent to self-monitoring. Documented rather than implied, so nobody reads this
    workflow as a guarantee it cannot give.
    """
    body = (WORKFLOWS / "heartbeat.yml").read_text(encoding="utf-8")
    assert "cannot rescue itself" in body


def test_the_heartbeat_does_not_collide_with_the_other_schedules() -> None:
    import yaml

    def minutes(name: str) -> set[int]:
        document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
        schedule = document[True]["schedule"]
        return {int(m) for e in schedule for m in e["cron"].split()[0].split(",")}

    assert not (minutes("heartbeat.yml") & minutes("publish.yml"))
    assert not (minutes("heartbeat.yml") & minutes("verify.yml"))
