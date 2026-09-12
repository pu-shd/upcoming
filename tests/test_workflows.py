"""The CI definitions, checked the way the code is.

A workflow can only be exercised by pushing, which makes YAML the least reviewable part of
a repository and the easiest place for a mistake to live for months. Everything asserted
here is something that has actually gone wrong in the predecessor or would silently undo a
decision the rest of the codebase is built around.
"""

from __future__ import annotations

import re

import pytest
import yaml

from tests.support import REPO_ROOT

WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    # PyYAML reads the bare `on:` key as the boolean True, which is YAML 1.1 behaving as
    # specified. Normalized here so a test can say `on` and mean it.
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    if True in document:
        document["on"] = document.pop(True)
    return document


ALL = sorted(p.name for p in WORKFLOWS.glob("*.yml"))


@pytest.mark.parametrize("name", ALL)
def test_every_workflow_parses(name: str) -> None:
    assert load(name)["jobs"]


@pytest.mark.parametrize("name", ALL)
def test_every_job_has_a_timeout(name: str) -> None:
    """An unbounded job can hold the concurrency group for six hours.

    With `cancel-in-progress: false` on the publish group, one hung run blocks every
    scheduled publish behind it -- the site stops updating and nothing reports a failure,
    because the hung run has not failed yet.
    """
    for job_name, job in load(name)["jobs"].items():
        assert "timeout-minutes" in job, f"{name}:{job_name}"


# --------------------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------------------


def test_the_publish_crons_do_not_overlap() -> None:
    """An overlap is a duplicate run, and two publishes racing for one branch.

    Cheap to check and impossible to eyeball: the daytime and overnight expressions differ
    only in their hour fields.
    """
    fired: set[tuple[int, int, int]] = set()
    for entry in load("publish.yml")["on"]["schedule"]:
        for tick in _expand(entry["cron"]):
            assert tick not in fired, f"{entry['cron']} collides at {tick}"
            fired.add(tick)


def _expand(cron: str) -> set[tuple[int, int, int]]:
    """Every (weekday, hour, minute) a cron expression fires at, for a day-of-month of *."""
    minute, hour, dom, month, dow = cron.split()
    assert dom == "*" and month == "*", "this expansion only handles day-of-month *"
    return {(d, h, m) for d in _field(dow, 7) for h in _field(hour, 24) for m in _field(minute, 60)}


def _field(spec: str, size: int) -> set[int]:
    if spec == "*":
        return set(range(size))
    values: set[int] = set()
    for part in spec.split(","):
        if part.startswith("*/"):
            values |= set(range(0, size, int(part[2:])))
        elif "-" in part:
            low, high = part.split("-")
            values |= set(range(int(low), int(high) + 1))
        else:
            values.add(int(part))
    return values


def test_publishing_is_never_cancelled_in_flight() -> None:
    """A cancelled publish can leave the branch and the deployment disagreeing.

    And a cancellation is not a failure: nothing would report it.
    """
    assert load("publish.yml")["concurrency"]["cancel-in-progress"] is False


def test_the_deploy_runs_even_when_a_source_failed() -> None:
    """One department's upstream outage must not stop the whole site updating.

    The build publishes the other eleven plus the combined feeds from the failed source's
    last good copy; gating the deploy on a green build would throw that away.
    """
    deploy = load("publish.yml")["jobs"]["deploy"]
    assert "always()" in deploy["if"]
    assert "cancelled" in deploy["if"]


def test_the_deploy_is_its_own_job_not_a_step() -> None:
    """A step that does not run because an earlier step failed is reported as skipped.

    Skipped reads as green. That is exactly how the predecessor's site went stale while
    its workflow list stayed clean, and it is the reason this repository has a watchdog.
    """
    jobs = load("publish.yml")["jobs"]
    assert "deploy" in jobs
    assert jobs["deploy"]["needs"] == "publish"
    build_steps = " ".join(str(s) for s in jobs["publish"]["steps"])
    assert "deploy-pages" not in build_steps


def test_publishing_waits_for_the_test_suite() -> None:
    """They used to start together, and publishing finished first.

    Measured: 34 seconds before the suite it had not waited for, so a commit could deploy
    and only then be found broken. The gates inside the build catch output that is
    *invalid*; nothing there catches output that is valid and wrong.
    """
    triggers = load("publish.yml")["on"]
    assert "push" not in triggers, "a push must reach publishing through the suite"
    assert triggers["workflow_run"]["workflows"] == ["Tests"]
    assert triggers["workflow_run"]["branches"] == ["main"]


def test_a_failed_suite_does_not_publish() -> None:
    """`types: [completed]` fires on failure too, so the conclusion has to be checked."""
    condition = " ".join(load("publish.yml")["jobs"]["publish"]["if"].split())
    assert "workflow_run.conclusion == 'success'" in condition
    # Scheduled and manual runs carry no workflow_run payload and must still publish.
    assert "github.event_name != 'workflow_run'" in condition


def test_a_schedule_still_publishes_without_a_suite_run() -> None:
    """The schedule is the dominant path and must not depend on a recent push."""
    triggers = load("publish.yml")["on"]
    assert triggers["schedule"]
    assert "workflow_dispatch" in triggers


# --------------------------------------------------------------------------------------
# The watchdog
# --------------------------------------------------------------------------------------


def test_the_watchdog_is_a_separate_workflow() -> None:
    """It must survive the failure it is watching for.

    A check inside the publishing workflow is skipped by the same crash that skips the
    deploy, which is the failure that produced this file.
    """
    assert (WORKFLOWS / "verify.yml").is_file()
    assert "verify" not in load("publish.yml")["jobs"]


def test_the_watchdog_runs_without_the_bypass_credential() -> None:
    """It must see exactly what a consumer sees.

    Giving this job the bypass header would let it pass in cases where every real consumer
    of the feed is blocked -- a watchdog that cannot observe the failure it exists for.
    """
    text = (WORKFLOWS / "verify.yml").read_text(encoding="utf-8")
    live = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    assert not [ln for ln in live if "BOT_BYPASS_HEADER" in ln]


def test_the_watchdog_schedule_is_offset_from_publishing() -> None:
    """A check that lands mid-deploy reports a partial deploy that is merely in progress."""
    verify_minutes = {int(e["cron"].split()[0]) for e in load("verify.yml")["on"]["schedule"]}
    publish_minutes = {
        int(m)
        for entry in load("publish.yml")["on"]["schedule"]
        for m in entry["cron"].split()[0].split(",")
    }
    assert not (verify_minutes & publish_minutes)


def test_the_watchdog_reports_beyond_the_run_that_found_the_problem() -> None:
    """A red run in a list of green ones is easy to miss for a week."""
    steps = " ".join(str(s) for s in load("verify.yml")["jobs"]["verify"]["steps"])
    assert "gh issue create" in steps
    assert "gh issue close" in steps


def test_the_watchdog_still_fails_the_run_after_filing_the_issue() -> None:
    """Filing an issue is not a substitute for the run being red."""
    steps = load("verify.yml")["jobs"]["verify"]["steps"]
    assert any(step.get("run", "").strip() == "exit 1" for step in steps)


# --------------------------------------------------------------------------------------
# Quality gates
# --------------------------------------------------------------------------------------


def test_lint_cannot_block_a_publish() -> None:
    """A formatting failure must never be able to stop a departmental feed updating."""
    tests = load("tests.yml")
    assert "schedule" not in tests["on"]
    for job in load("publish.yml")["jobs"].values():
        assert "ruff" not in str(job)
        assert "mypy" not in str(job)


# --------------------------------------------------------------------------------------
# Shell, which is where the subtle failures live
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL)
def test_an_exit_code_is_never_read_from_a_bare_dollar_question(name: str) -> None:
    """The default shell is ``bash -e``, and ``set -uo pipefail`` does not clear it.

    A step that runs a command and then reads ``$?`` has already been killed by ``-e`` if
    the command failed -- so the branch handling the failure never runs. This shipped once
    in publish.yml, where it would have skipped the deploy whenever a source failed: the
    exact predecessor failure the whole design is built to avoid, reproduced in its fix.

    ``|| code=$?`` is the form that works, because the ``||`` makes the command succeed.
    """
    for job in load(name)["jobs"].values():
        for step in job.get("steps", []):
            script = step.get("run", "")
            for line in script.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "$?" not in stripped:
                    continue
                assert "|| code=$?" in stripped or "||" in stripped, (
                    f"{name}: `{stripped}` reads $? after a command that -e already ended"
                )


@pytest.mark.parametrize("name", ALL)
def test_no_step_captures_a_variable_on_the_left_of_a_pipe(name: str) -> None:
    """The left side of a pipeline runs in a subshell, so an assignment there is lost.

    ``cmd || code=$? | tee log`` looks like it records the failure and does not.
    """
    for job in load(name)["jobs"].values():
        for step in job.get("steps", []):
            for line in step.get("run", "").splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                before = stripped.split("|", 1)[0] if "|" in stripped else ""
                assert not re.search(r"\bcode=\$\?", before) or "||" in stripped, stripped


@pytest.mark.parametrize("name", ALL)
def test_no_heredoc_terminator_is_indented(name: str) -> None:
    """An indented terminator does not close a plain heredoc; the rest of the step is eaten.

    Only ``<<-`` strips leading whitespace, and only tabs. This is invisible in review and
    only shows up as a shell syntax error on a real run.
    """
    for job in load(name)["jobs"].values():
        for step in job.get("steps", []):
            script = step.get("run", "")
            for marker in re.findall(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", script):
                if f"<<-{marker}" in script or f"<<- {marker}" in script:
                    continue
                closers = [ln for ln in script.splitlines() if ln.rstrip() == marker]
                assert closers, f"{name}: heredoc <<{marker} is never closed at column 0"
