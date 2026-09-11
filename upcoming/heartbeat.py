"""Keeping the schedules alive, and noticing when one is not.

GitHub disables scheduled workflows on a public repository after 60 days with no
repository activity. It emails the owner and stops running them; nothing in the repository
reports it, and the site simply stops updating. For a project whose entire premise is that
a failure must never be silent, that is the most important silent failure available.

Two mechanisms, because they cover different halves and neither covers both:

* **Prevention.** Commit a keepalive when the repository has been quiet long enough to be
  approaching the limit. Done on a threshold rather than every day, so a repository under
  normal development never accumulates commits nobody asked for.
* **Detection.** Ask the API whether each scheduled workflow is still ``active``. This
  catches a workflow disabled by hand, or one disabled while others survived.

What neither covers, and the documentation says so plainly rather than implying otherwise:
if *every* scheduled workflow is disabled at once, the heartbeat is disabled too and cannot
rescue itself. That is inherent to running your own monitoring inside the thing being
monitored, and the only real answer is a check from outside the repository.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .clock import now as clock_now
from .clock import stamp

#: GitHub's own limit. Named rather than inlined so the margin below is legible as a
#: margin: acting at 35 days leaves 25 days of slack, which survives a long holiday and
#: several consecutive failed heartbeat runs.
DISABLE_AFTER_DAYS = 60
DEFAULT_THRESHOLD_DAYS = 35

#: The state the API reports for a workflow GitHub stopped for inactivity. Distinct from
#: ``disabled_manually``, which is somebody's deliberate decision and must not be undone.
DISABLED_FOR_INACTIVITY = "disabled_inactivity"
ACTIVE = "active"


@dataclass(frozen=True)
class Decision:
    """Whether to commit a keepalive, and the reasoning, so a log explains itself."""

    needed: bool
    quiet_days: float
    reason: str


def decide(
    last_commit: datetime, *, now: datetime, threshold_days: float = DEFAULT_THRESHOLD_DAYS
) -> Decision:
    """Is the repository quiet enough to need a keepalive commit?

    Measured from the last commit rather than from the last heartbeat, so ordinary
    development counts: a repository being worked on needs no keepalive, and committing one
    anyway would put noise in the history of every active week.
    """
    quiet = (now - last_commit).total_seconds() / 86400
    if quiet < 0:
        # A commit dated in the future. Reported rather than treated as maximal activity,
        # because a clock this wrong makes every other number here meaningless.
        return Decision(False, quiet, f"the last commit is dated {-quiet:.1f} days ahead")
    if quiet < threshold_days:
        return Decision(
            False,
            quiet,
            f"last commit {quiet:.1f} days ago, inside the {threshold_days:g}-day threshold",
        )
    if quiet >= DISABLE_AFTER_DAYS:
        # Past the limit already: the keepalive is now a repair rather than a precaution,
        # and the schedules are probably already stopped. Saying "-393 days remaining"
        # here would be arithmetic pretending to be a warning.
        return Decision(
            True,
            quiet,
            f"last commit {quiet:.1f} days ago, past GitHub's {DISABLE_AFTER_DAYS}-day "
            f"limit — the scheduled workflows have most likely already been disabled",
        )
    return Decision(
        True,
        quiet,
        f"last commit {quiet:.1f} days ago, past the {threshold_days:g}-day threshold and "
        f"{DISABLE_AFTER_DAYS - quiet:.1f} days from the schedules being disabled",
    )


def keepalive(decision: Decision, *, now: datetime, ref: str, sha: str) -> str:
    """The file whose commit is the keepalive.

    It carries the reasoning rather than a bare timestamp, so someone finding this commit
    in a year can tell what it was for without reading the workflow.
    """
    document = {
        "purpose": (
            "Keeps GitHub from disabling this repository's scheduled workflows, which it "
            f"does after {DISABLE_AFTER_DAYS} days without repository activity. Written "
            "only when the repository has been quiet past the threshold."
        ),
        "writtenAt": stamp(now),
        "quietDays": round(decision.quiet_days, 1),
        "reason": decision.reason,
        "ref": ref,
        "sourceSha": sha,
    }
    return json.dumps(document, indent=2) + "\n"


@dataclass(frozen=True)
class WorkflowState:
    """One workflow's name, file and state, as the API reports them."""

    name: str
    path: str
    state: str


def scheduled_workflows(paths: Mapping[str, str]) -> list[str]:
    """Which workflow files declare a schedule.

    Read from the files rather than from the API, because the API reports what is
    registered and this needs to know what was *intended* -- a workflow whose schedule was
    removed in a commit but is still registered should not be reported as broken.
    """
    return sorted(
        path
        for path, body in paths.items()
        if any(line.strip().startswith("- cron:") for line in body.splitlines())
    )


def stopped(states: Sequence[WorkflowState], expected: Sequence[str]) -> list[WorkflowState]:
    """Scheduled workflows that are not running, whether or not GitHub stopped them.

    ``disabled_manually`` is included: somebody turning off the publisher by hand is a
    legitimate action, but leaving it off silently is not, and a heartbeat that stayed
    quiet about it would be choosing the wrong half of that.
    """
    wanted = {path.split("/")[-1] for path in expected}
    return [s for s in states if s.path.split("/")[-1] in wanted and s.state != ACTIVE]


def report(stopped_states: Sequence[WorkflowState]) -> str:
    """What to say about stopped workflows, in a form an issue body can carry."""
    if not stopped_states:
        return "every scheduled workflow is active"
    lines = [f"{len(stopped_states)} scheduled workflow(s) are not running:", ""]
    for state in stopped_states:
        why = (
            "GitHub disabled it for repository inactivity"
            if state.state == DISABLED_FOR_INACTIVITY
            else f"state is {state.state}"
        )
        lines.append(f"- `{state.path}` ({state.name}) — {why}")
    return "\n".join(lines)


def parse_states(payload: Mapping[str, object]) -> list[WorkflowState]:
    """Read the API's workflow listing."""
    workflows = payload.get("workflows")
    if not isinstance(workflows, list):
        return []
    return [
        WorkflowState(
            name=str(item.get("name", "")),
            path=str(item.get("path", "")),
            state=str(item.get("state", "")),
        )
        for item in workflows
        if isinstance(item, dict)
    ]


def utc_now() -> datetime:
    """The current instant. Re-exported so callers need one import, not two."""
    return clock_now()


__all__ = [
    "ACTIVE",
    "DEFAULT_THRESHOLD_DAYS",
    "DISABLED_FOR_INACTIVITY",
    "DISABLE_AFTER_DAYS",
    "Decision",
    "WorkflowState",
    "decide",
    "keepalive",
    "parse_states",
    "report",
    "scheduled_workflows",
    "stopped",
    "utc_now",
]
