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


def test_compose_images_are_named_for_this_repository() -> None:
    """Compose names an unnamed image `<directory>-<service>`, and the predecessor also
    lives in a directory called `upcoming` with a service called `tests`. Building either
    one overwrote the other's `upcoming-tests`, and `docker-compose run` reuses whatever
    image has that name: on 2026-10-20 this suite ran inside pu-orfe's image, with no Node
    and none of this package, and failed 148 tests that pass. Named explicitly rather than
    with a top-level `name:`, which Compose v1 rejects."""
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    built = {name: service for name, service in document["services"].items() if "build" in service}
    assert built, "expected at least one service that builds an image"
    for name, service in built.items():
        assert str(service.get("image", "")).startswith("pu-shd-upcoming"), (
            f"compose service {name!r} builds an image without naming it"
        )


def test_the_dev_image_carries_every_test_runner() -> None:
    """Node runs the simulator's tests; zsh and make run the operator scripts'. A test that
    skipped itself when one was missing would pass while checking nothing, so the image
    installs all three and the tests fail without them."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    dev = dockerfile.split("AS dev", 1)[1]
    for package in ("nodejs", "zsh", "make"):
        assert package in dev, f"the dev image does not install {package}"


def test_ci_installs_zsh_for_the_script_tests() -> None:
    """ubuntu-latest has make but not zsh."""
    tests = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    pytest_job = tests.split("  pytest:", 1)[1].split("\n  lint:", 1)[0]
    assert "apt-get install" in pytest_job and "zsh" in pytest_job


def test_compose_runs_the_suite_by_default() -> None:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert "pytest" in document["services"]["tests"]["command"]


def test_every_declared_dependency_is_actually_imported() -> None:
    """A dependency nobody imports is weight, and its version pins are live constraints.

    Four were found declared and unused, inherited from the predecessor: `ics` -- whose
    Calendar this package deliberately does not use, because its `events` is a set and
    loses feed order -- plus `tatsu` and `arrow`, which existed only to satisfy `ics`, and
    `markdownify`, never called. The worst part was the `tatsu>=4.2,<5.16` ceiling: an
    upper bound carried for a library we do not import, blocking a future upgrade for no
    reason at all.
    """
    import ast
    import re
    import tomllib

    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        re.split(r"[><=!\[;]", entry)[0].strip().lower()
        for entry in manifest["project"]["dependencies"]
    }

    #: Distribution name -> the module it is imported as, where they differ.
    module_of = {"beautifulsoup4": "bs4", "pyyaml": "yaml", "tzdata": "zoneinfo"}

    imported: set[str] = set()
    for path in (REPO_ROOT / "upcoming").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])

    unused = {
        name for name in declared if module_of.get(name, name.replace("-", "_")) not in imported
    }
    assert unused == set(), f"declared but never imported: {sorted(unused)}"


# --------------------------------------------------------------------------------------
# Branch protection
# --------------------------------------------------------------------------------------

RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"


def _ruleset() -> dict:  # type: ignore[type-arg]
    import json

    return json.loads(RULESET.read_text(encoding="utf-8"))


def test_main_requires_exactly_the_jobs_the_tests_workflow_runs() -> None:
    """A required check nothing produces blocks every merge forever; a job left out of the
    required set can fail and still merge. Both are drift between two files, so the job
    names are read from the workflow rather than written twice."""
    jobs = set(load_workflow(WORKFLOWS / "tests.yml")["jobs"])
    (checks,) = [r for r in _ruleset()["rules"] if r["type"] == "required_status_checks"]
    required = {c["context"] for c in checks["parameters"]["required_status_checks"]}
    assert required == jobs


def test_main_needs_a_pull_request_and_cannot_be_rewritten_or_deleted() -> None:
    types = {r["type"] for r in _ruleset()["rules"]}
    assert {"pull_request", "required_status_checks", "non_fast_forward", "deletion"} <= types
    assert _ruleset()["enforcement"] == "active"
    assert _ruleset()["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]


def test_only_deploy_keys_bypass_the_main_ruleset() -> None:
    """The heartbeat's keepalive is the one thing that must reach main without a pull
    request, and it pushes with a deploy key. GitHub refuses the Actions app as a bypass
    actor -- measured 2026-11-11, "Actor GitHub Actions integration must be part of the
    ruleset source or owner organization" -- so an Integration entry here would fail to
    apply rather than quietly working."""
    actors = _ruleset()["bypass_actors"]
    assert actors == [{"actor_id": None, "actor_type": "DeployKey", "bypass_mode": "always"}]


def test_the_heartbeat_pushes_with_the_deploy_key_and_checks_protection() -> None:
    text = (WORKFLOWS / "heartbeat.yml").read_text(encoding="utf-8")
    document = load_workflow(WORKFLOWS / "heartbeat.yml")
    assert document["permissions"]["contents"] == "read", (
        "the keepalive pushes with the deploy key; the token needs no write access"
    )
    assert "${{ secrets.HEARTBEAT_DEPLOY_KEY }}" in text
    assert 'git push origin "HEAD' not in text, "a token push is refused by the main ruleset"
    assert "--branch-rules branch-rules.json" in text
    assert "rules/branches/main" in text
    assert "StrictHostKeyChecking=yes" in text


def test_a_failed_keepalive_does_not_skip_the_repair_and_the_reports() -> None:
    """A step's `if:` without a status function carries an implicit `success()`, so a
    refused keepalive push -- no deploy key, or a revoked one -- would skip re-enabling the
    stopped schedules and reporting them. That failure arrives on exactly the days the
    repository has gone quiet, which is when schedules stop, so every step after it must
    still run."""
    steps = load_workflow(WORKFLOWS / "heartbeat.yml")["jobs"]["heartbeat"]["steps"]
    names = [step.get("name") for step in steps]
    after = steps[names.index("Commit the keepalive") + 1 :]
    assert after, "the heartbeat has no steps after the keepalive"
    for step in after:
        assert "!cancelled()" in str(step.get("if", "")), (
            f"step {step.get('name')!r} is skipped when the keepalive push fails"
        )
