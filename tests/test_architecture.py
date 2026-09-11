"""Structural guards.

Each of these prevents a regression to a specific state the predecessor is in. They are
AST checks rather than grep because a string search over source would match comments and
docstrings -- including the ones in this repo that *describe* the banned patterns.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support import REPO_ROOT

PACKAGE = REPO_ROOT / "upcoming"

#: The one module allowed to read the environment.
ENV_OWNER = "registry.py"

#: Modules allowed to catch bare ``Exception``: the per-source build boundary, once it
#: exists. Listed by name so adding a second catch site is a deliberate edit to this list.
BARE_EXCEPT_ALLOWED = {"build.py"}


def package_modules() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if p.name != "__init__.py")


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_the_package_has_modules_to_check() -> None:
    """Anti-vacuity: the guards below iterate modules, so an empty list passes trivially."""
    assert len(package_modules()) >= 3


@pytest.mark.parametrize("path", package_modules(), ids=lambda p: p.name)
def test_only_the_registry_reads_the_environment(path: Path) -> None:
    """Per-source config in one process is impossible otherwise.

    The predecessor reads ``os.getenv`` inside leaf functions -- 34 call sites, 21 in its
    enrichment module alone -- so two sources needing two different values of the same
    process-global variable cannot both be correct in one run. That impossibility is the
    root cause of the fork this project replaces.
    """
    if path.name == ENV_OWNER:
        return

    offenders: list[str] = []
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Attribute) and node.attr in {"getenv", "environ"}:
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                offenders.append(f"os.{node.attr} at line {node.lineno}")
        elif isinstance(node, ast.Name) and node.id == "environ":
            offenders.append(f"environ at line {node.lineno}")

    assert not offenders, (
        f"{path.name} reads the environment ({', '.join(offenders)}). "
        f"Environment access belongs in {ENV_OWNER}; take the value as a parameter."
    )


@pytest.mark.parametrize("path", package_modules(), ids=lambda p: p.name)
def test_bare_exception_handlers_stay_at_the_build_boundary(path: Path) -> None:
    """One source's failure must be attributable, not absorbed.

    The predecessor catches ``Exception`` in seven places in its enrichment module, which
    is why a total enrichment failure there is indistinguishable from a clean run that
    found nothing.
    """
    if path.name in BARE_EXCEPT_ALLOWED:
        return

    offenders: list[int] = []
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            offenders.append(node.lineno)
        elif isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}:
            # Re-raising is fine: it attributes without absorbing.
            reraises = any(isinstance(stmt, ast.Raise) for stmt in ast.walk(node))
            if not reraises:
                offenders.append(node.lineno)

    assert not offenders, (
        f"{path.name} swallows a bare Exception at line(s) {offenders}. Catch a specific "
        f"error, or re-raise as SourceFatal so the failure is attributed to a source."
    )


def test_the_model_does_not_import_the_registry() -> None:
    """Keeps the data shape independent of how configuration is loaded."""
    tree = parse(PACKAGE / "model.py")
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "registry" not in imported
    assert not any(m and m.endswith("registry") for m in imported)


def test_provenance_has_no_intra_package_imports() -> None:
    """It is the vocabulary everything else depends on, so it depends on nothing.

    The predecessor's equivalent module has this property and it is why the module ports
    cleanly; keeping it means the title-source vocabulary can never participate in an
    import cycle.
    """
    tree = parse(PACKAGE / "provenance.py")
    relative = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0
    ]
    assert not relative, "provenance.py must not import from the package"
