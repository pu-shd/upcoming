"""Contract tests over the wiring: CI YAML, the Makefile, and the packaging.

These catch the class of bug unit tests structurally cannot -- a workflow that never runs
the suite, a credential given a real-looking default, a Make recipe that works on Linux and
not on the maintainer's Mac. The predecessor's equivalent file is the strongest asset in
either repo, and this is its beginning here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.support import REPO_ROOT

WORKFLOWS = REPO_ROOT / ".github" / "workflows"
MAKEFILE = REPO_ROOT / "Makefile"
COMPOSE = REPO_ROOT / "docker-compose.yml"


def workflow_paths() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def load_workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(workflow: dict) -> dict:
    """The ``on:`` block.

    PyYAML parses the bare key ``on`` as the boolean ``True``, so both spellings have to be
    tried or every trigger assertion silently passes against an empty dict.
    """
    return workflow.get("on") or workflow.get(True) or {}


def test_there_are_workflows_to_check() -> None:
    """Anti-vacuity: everything below iterates workflows."""
    assert workflow_paths()


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_every_workflow_is_valid_yaml_with_triggers(path: Path) -> None:
    workflow = load_workflow(path)
    assert isinstance(workflow, dict), f"{path.name} is not a mapping"
    assert triggers(workflow), f"{path.name} declares no triggers"


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_every_job_has_a_timeout(path: Path) -> None:
    """A hung job holds its concurrency group open past the next scheduled run.

    With ``cancel-in-progress: false`` GitHub keeps only one *pending* run per group and a
    third arrival cancels the pending one -- invisible at one source, a silent data-loss
    mechanism once many jobs contend for the same group.
    """
    for name, job in load_workflow(path)["jobs"].items():
        assert "timeout-minutes" in job, f"{path.name}:{name} has no timeout-minutes"


def test_the_suite_runs_on_pull_requests() -> None:
    """Without this, a pull request carries no test signal at all."""
    on_pr = [p.name for p in workflow_paths() if "pull_request" in triggers(load_workflow(p))]
    assert on_pr, "no workflow runs on pull_request"


def test_a_workflow_runs_pytest() -> None:
    steps = " ".join(p.read_text(encoding="utf-8") for p in workflow_paths())
    assert "pytest" in steps


def test_the_container_path_is_exercised_in_ci() -> None:
    """So `make docker-test` cannot rot.

    The container is what a developer runs locally; nothing else in CI would notice it
    breaking.
    """
    steps = " ".join(p.read_text(encoding="utf-8") for p in workflow_paths())
    assert "docker compose" in steps or "docker-compose" in steps


def test_lint_never_gates_a_publish() -> None:
    """Quality gates pull requests; it must not be able to stop a feed from publishing.

    Asserted now, while there is only a test workflow, so the rule is already in place when
    a publishing workflow arrives.
    """
    for path in workflow_paths():
        workflow = load_workflow(path)
        text = path.read_text(encoding="utf-8")
        publishes = "deploy-pages" in text or "gh release" in text
        lints = "ruff" in text or "mypy" in text
        assert not (publishes and lints), (
            f"{path.name} both publishes and lints; a style failure must not stop a feed"
        )
        assert workflow is not None


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_no_workflow_gives_the_bypass_credential_a_plausible_default(path: Path) -> None:
    """The specific failure this project was built to avoid.

    The predecessors ship ``os.getenv("BOT_BYPASS_HEADER_VALUE", "1")`` and an inline
    ``|| '1'`` in CI, so with the secret unset the pipeline sends ``1``, gets 403 on every
    event page, scrapes nothing, and reports success.

    So a workflow may assign this only two ways: from the secret store, which is the real
    credential and reaches real hosts, or as a visibly fake placeholder, which is fine
    precisely because the job using it reaches no network. Anything in between -- a value
    that might be a credential and might not -- is the failure above.
    """
    text = path.read_text(encoding="utf-8")
    assert "|| '1'" not in text and '|| "1"' not in text
    for line in text.splitlines():
        if "BOT_BYPASS_HEADER:" in line:
            value = line.split("BOT_BYPASS_HEADER:", 1)[1].strip()
            assert value not in {"1", "'1'", '"1"'}, (
                f"{path.name} gives the bypass credential the value {value}, which is what "
                f"makes a total scrape failure look like success"
            )
            from_secret_store = "secrets.BOT_BYPASS_HEADER" in value
            visibly_fake = "placeholder" in value or "not-a-credential" in value
            assert from_secret_store or visibly_fake, (
                f"{path.name}: read the credential from the secret store, or name the CI "
                f"value so it is visibly not a real one"
            )


def test_the_registry_is_validated_before_anything_uses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`upcoming check` is the cheapest gate; it must stay reachable from the CLI."""
    from upcoming.cli import EXIT_OK, main

    monkeypatch.setenv("BOT_BYPASS_HEADER", "x-contract-test: placeholder")
    registry = str(REPO_ROOT / "config" / "sources.yaml")
    assert main(["--registry", registry, "check"]) == EXIT_OK


def test_check_fails_loudly_when_the_credential_is_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point, exercised end to end through the CLI.

    With no credential the command must exit non-zero and say why, rather than proceeding
    with a placeholder and letting every later page fetch 403 under a green run.
    """
    from upcoming.cli import EXIT_CONFIG, main

    monkeypatch.delenv("BOT_BYPASS_HEADER", raising=False)
    registry = str(REPO_ROOT / "config" / "sources.yaml")
    assert main(["--registry", registry, "check"]) == EXIT_CONFIG
    assert "BOT_BYPASS_HEADER" in capsys.readouterr().err


# --------------------------------------------------------------------------------------
# Makefile portability -- macOS is the primary development target, CI is Linux
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,reason",
    [
        ("sha256sum", "macOS has shasum -a 256; hash in hashlib so local and CI agree"),
        ("getent", "Linux-only; use python3 -c 'import socket; socket.gethostbyname(...)'"),
        ("readlink -f", "macOS readlink has no -f"),
        ("grep -P", "macOS grep has no -P"),
        ("date -d", "macOS date has no -d"),
    ],
)
def test_the_makefile_avoids_linux_only_tools(tool: str, reason: str) -> None:
    body = MAKEFILE.read_text(encoding="utf-8")
    # The rules are documented in a comment block, so only check recipe and variable lines.
    lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    for line in lines:
        assert tool not in line, f"Makefile uses {tool!r}: {reason}"


def test_the_makefile_pins_a_shell() -> None:
    """Recipes must not depend on the developer's interactive zsh configuration."""
    assert "SHELL := /bin/bash" in MAKEFILE.read_text(encoding="utf-8")


def test_documented_make_targets_exist() -> None:
    """A help text naming a target that does not exist is worse than no help text."""
    body = MAKEFILE.read_text(encoding="utf-8")
    declared = set()
    for line in body.splitlines():
        if line.startswith(".PHONY:"):
            declared.update(line.removeprefix(".PHONY:").replace("\\", "").split())
        elif line.startswith("        "):
            declared.update(line.replace("\\", "").split())
    for advertised in ("install", "sources", "check", "test", "lint", "typecheck", "docker-test"):
        assert advertised in declared, f"help advertises {advertised!r} but it is not declared"


# --------------------------------------------------------------------------------------
# Compose
# --------------------------------------------------------------------------------------


def test_compose_keeps_the_version_key() -> None:
    """Invoked as `docker-compose` (v1), which reads a file without `version` as the
    legacy format -- where top-level keys are service names, so it would try to build a
    service literally called "services"."""
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert "version" in document


def test_compose_runs_the_suite_by_default() -> None:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert "pytest" in document["services"]["tests"]["command"]
