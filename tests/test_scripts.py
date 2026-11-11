"""The operator's scripts: `make bootstrap`, `make protect`, and the venv rule under them.

These are what someone runs on a fresh machine, or after Homebrew has moved the ground
under them, so they are tested against the shapes that actually happened: a `.venv` whose
interpreter a Python upgrade deleted, a tool that is missing, a ruleset that exists or does
not. Every external tool is a stub on a private PATH -- `gh`, `docker`, `docker-compose`,
`make` -- so nothing here reaches GitHub, a Docker daemon or the network, and each stub
records how it was called so a test can assert what *would* have changed something.

zsh and make are test dependencies, installed in CI and in the dev image. A test that
skipped itself when either was absent would report green while checking nothing.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests.support import REPO_ROOT

ZSH = shutil.which("zsh")
MAKE = shutil.which("make")


def test_the_script_runners_are_installed() -> None:
    """Fail rather than skip: every test below needs these."""
    assert ZSH, "zsh is a test dependency; CI and the dev image install it"
    assert MAKE, "make is a test dependency; CI and the dev image install it"


# --------------------------------------------------------------------------------------
# The venv rule
# --------------------------------------------------------------------------------------


def _dry_make(tmp_path: Path, target: str) -> str:
    shutil.copy(REPO_ROOT / "Makefile", tmp_path / "Makefile")
    result = subprocess.run(
        [str(MAKE), "-n", "-C", str(tmp_path), target],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_a_venv_whose_interpreter_is_gone_is_rebuilt(tmp_path: Path) -> None:
    """2026-10-20: Homebrew upgraded Python 3.14 to 3.15 and deleted the interpreter
    `.venv/bin/python` linked to. The directory was still there, so a rule keyed on the
    directory considered the venv built, and `make install` failed calling a python that
    no longer existed."""
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(tmp_path / "gone" / "python3.14")
    commands = _dry_make(tmp_path, "install")
    assert "rm -rf .venv" in commands
    assert "python3 -m venv .venv" in commands


def test_a_working_venv_is_left_alone(tmp_path: Path) -> None:
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    commands = _dry_make(tmp_path, "install")
    assert "python3 -m venv" not in commands
    assert "pip install --quiet -e '.[dev]'" in commands


# --------------------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------------------

#: Each stub logs its own name and arguments, then behaves as the FAKE_* variables say.
#: Written for /bin/sh with no external commands, because the PATH under test holds only
#: these stubs -- a real python3 or gh leaking in would make "missing tool" untestable.
_STUBS = {
    "git": "exit 0",
    "make": "exit 0",
    "node": 'echo "v${FAKE_NODE:-22.3.0}"',
    "docker": '[ "$1" = info ] && exit "${FAKE_DOCKER_INFO:-0}"; exit 0',
    "docker-compose": '[ "$1" = run ] && exit "${FAKE_MOUNT:-0}"; exit 0',
    "python3": (
        'case "$*" in *version_info*) '
        '[ -n "${FAKE_PY_VERSION:-}" ] && { echo "$FAKE_PY_VERSION"; exit 0; } ;; esac\n'
        'exec "$REAL_PYTHON" "$@"'
    ),
    "gh": """case "$1 $2" in
  "auth status") exit "${FAKE_GH_AUTH:-0}" ;;
  "repo view") echo pu-shd/upcoming; exit 0 ;;
esac
case "$*" in
  *--method*) exit 0 ;;
  *rules/branches/main*) /bin/cat "$FAKE_LIVE_RULES"; exit 0 ;;
  *rulesets*) [ -n "${FAKE_RULESET_ID:-}" ] && echo "$FAKE_RULESET_ID"; exit 0 ;;
esac
exit 0""",
}


def _stub_bin(tmp_path: Path, *, omit: tuple[str, ...] = ()) -> Path:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    for name, body in _STUBS.items():
        if name in omit:
            continue
        stub = bin_dir / name
        stub.write_text(
            # One line per call, even when an argument is a multi-line `python3 -c`.
            f'#!/bin/sh\nprintf "%s" "{name} $*" | /usr/bin/tr "\\n" " " >> "$STUB_LOG"\n'
            f'echo >> "$STUB_LOG"\n{body}\n',
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _checkout(tmp_path: Path) -> Path:
    """A minimal checkout: the scripts, the ruleset, and the package protect imports."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    for script in ("bootstrap.zsh", "protect-main.zsh"):
        shutil.copy(REPO_ROOT / "scripts" / script, root / "scripts" / script)
    (root / ".github" / "rulesets").mkdir(parents=True)
    shutil.copy(REPO_ROOT / ".github" / "rulesets" / "main.json", root / ".github" / "rulesets")
    (root / "upcoming").symlink_to(REPO_ROOT / "upcoming")
    return root


def _run(
    tmp_path: Path,
    script: str,
    *args: str,
    omit: tuple[str, ...] = (),
    **fake: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    root = tmp_path / "checkout"
    if not root.exists():
        _checkout(tmp_path)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    env = {
        "PATH": str(_stub_bin(tmp_path, omit=omit)),
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
        "REAL_PYTHON": os.path.realpath(__import__("sys").executable),
        **fake,
    }
    result = subprocess.run(
        [str(ZSH), str(root / "scripts" / script), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result, log.read_text(encoding="utf-8").splitlines()


def _changes(calls: list[str]) -> list[str]:
    """The calls that would change something: everything but the read-only checks."""
    read_only = ("node --version", "docker info", "gh auth status", "gh repo view")
    return [
        c
        for c in calls
        if not c.startswith(read_only)
        and not c.startswith("python3")
        and not (c.startswith("gh api") and "--method" not in c)
    ]


# --------------------------------------------------------------------------------------
# make bootstrap
# --------------------------------------------------------------------------------------


def test_bootstrap_builds_everything_and_says_so(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "bootstrap.zsh")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "bootstrap: ready"
    assert _changes(calls) == [
        "make install",
        "make check",
        "docker-compose build tests",
        "docker-compose run --rm tests true",
    ]


def test_bootstrap_dry_run_changes_nothing_and_says_so(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "bootstrap.zsh", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "bootstrap: would install the package and its dev tools into .venv" in result.stdout
    assert result.stdout.splitlines()[-1] == "bootstrap: dry run; nothing changed"
    assert _changes(calls) == []


def test_bootstrap_reports_every_missing_tool_at_once_and_changes_nothing(
    tmp_path: Path,
) -> None:
    """One run should list everything to install, not one tool per rerun."""
    result, calls = _run(tmp_path, "bootstrap.zsh", omit=("node", "gh"))
    assert result.returncode == 1
    assert "2 problem(s); nothing was changed" in result.stderr
    assert "node is not installed -- brew install node" in result.stderr
    assert "gh is not installed -- brew install gh && gh auth login" in result.stderr
    assert _changes(calls) == []


@pytest.mark.parametrize(
    ("fake", "complaint"),
    [
        ({"FAKE_PY_VERSION": "3 11"}, "python3 is 3.11; 3.12+ is required"),
        ({"FAKE_NODE": "20.11.1"}, "node is 20; 22+ is required"),
        ({"FAKE_DOCKER_INFO": "1"}, "docker cannot reach a daemon -- start one: colima start"),
        ({"FAKE_GH_AUTH": "1"}, "gh is not logged in -- gh auth login"),
    ],
)
def test_bootstrap_refuses_a_tool_that_is_present_but_unusable(
    tmp_path: Path, fake: dict[str, str], complaint: str
) -> None:
    result, calls = _run(tmp_path, "bootstrap.zsh", **fake)
    assert result.returncode == 1
    assert complaint in result.stderr
    assert _changes(calls) == []


def test_bootstrap_explains_a_folder_docker_cannot_mount(tmp_path: Path) -> None:
    """The ~/Downloads case: Colima's VM may not read it until limactl is granted access."""
    result, _ = _run(tmp_path, "bootstrap.zsh", FAKE_MOUNT="1")
    assert result.returncode == 1
    assert "Docker cannot mount" in result.stderr
    assert "limactl" in result.stderr
    assert "bootstrap: ready" not in result.stdout


def test_bootstrap_says_why_it_is_rebuilding_a_venv(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").symlink_to(tmp_path / "gone" / "python3.14")
    result, _ = _run(tmp_path, "bootstrap.zsh")
    assert result.returncode == 0, result.stderr
    assert ".venv's interpreter is gone" in result.stdout


def test_bootstrap_refuses_an_unknown_argument(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "bootstrap.zsh", "--yes")
    assert result.returncode == 2
    assert "unknown argument: --yes" in result.stderr
    assert calls == []


# --------------------------------------------------------------------------------------
# make protect
# --------------------------------------------------------------------------------------


def _live_rules(tmp_path: Path, *, matching: bool) -> str:
    """What `GET rules/branches/main` answers: the spec's rules when applied, else []."""
    import json

    spec = json.loads((REPO_ROOT / ".github" / "rulesets" / "main.json").read_text())
    live = tmp_path / "live.json"
    live.write_text(json.dumps(spec["rules"] if matching else []), encoding="utf-8")
    return str(live)


def test_protect_dry_run_names_what_it_would_create_and_changes_nothing(
    tmp_path: Path,
) -> None:
    result, calls = _run(tmp_path, "protect-main.zsh", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "protect: would create ruleset 'main' on pu-shd/upcoming" in result.stdout
    assert result.stdout.splitlines()[-1] == "protect: dry run; nothing changed"
    assert _changes(calls) == []


def test_protect_updates_an_existing_ruleset_in_place(tmp_path: Path) -> None:
    """Re-running must not create a second ruleset beside the first."""
    result, calls = _run(
        tmp_path,
        "protect-main.zsh",
        FAKE_RULESET_ID="42",
        FAKE_LIVE_RULES=_live_rules(tmp_path, matching=True),
    )
    assert result.returncode == 0, result.stderr
    (write,) = _changes(calls)
    assert "--method PUT repos/pu-shd/upcoming/rulesets/42" in write
    assert "--input" in write and write.endswith(".github/rulesets/main.json")
    assert result.stdout.splitlines()[-1] == (
        "protect: main carries every rule in .github/rulesets/main.json"
    )


def test_protect_creates_the_ruleset_when_there_is_none(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path, "protect-main.zsh", FAKE_LIVE_RULES=_live_rules(tmp_path, matching=True)
    )
    assert result.returncode == 0, result.stderr
    (write,) = _changes(calls)
    assert "--method POST repos/pu-shd/upcoming/rulesets" in write


def test_protect_fails_when_github_accepts_the_ruleset_but_main_is_not_protected(
    tmp_path: Path,
) -> None:
    """An accepted PUT is not a protected branch -- an inactive or mistargeted ruleset is
    accepted too. The script reads back what main actually carries."""
    result, _ = _run(
        tmp_path, "protect-main.zsh", FAKE_LIVE_RULES=_live_rules(tmp_path, matching=False)
    )
    assert result.returncode == 1
    assert "main does not carry all of it" in result.stderr
    assert "rule `pull_request` is not enforced on main" in result.stderr


def test_protect_refuses_without_a_github_login(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "protect-main.zsh", FAKE_GH_AUTH="1")
    assert result.returncode == 1
    assert "gh is not logged in" in result.stderr
    assert _changes(calls) == []
