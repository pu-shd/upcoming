"""Reading a configuration file, once rather than five times.

Five modules had grown the same block: resolve the path, refuse if absent, parse the YAML,
turn a parse error into a ``ConfigFatal``, and reject unknown top-level keys. The last step
is the one worth protecting -- it is what makes a typo in ``config/`` a load error instead
of a setting that silently never applies -- and it is exactly the step a sixth loader would
be most likely to leave out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigFatal


def read_mapping(
    path: str | Path, *, what: str, allow: Iterable[str], required: bool = True
) -> Mapping[str, Any]:
    """Parse a YAML config file into a mapping, or fail with a message that names the file.

    ``required=False`` returns an empty mapping when the file is absent, for the configs
    that carry a working default in code. Absent is then a choice; malformed never is, so
    a file that exists and does not parse still fails.
    """
    config = Path(path)
    if not config.is_file():
        if required:
            raise ConfigFatal(f"no {what} at {config}")
        return {}

    try:
        document = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigFatal(f"{config} is not valid YAML: {exc}") from exc

    if not isinstance(document, dict):
        raise ConfigFatal(f"{config} must be a mapping, not {type(document).__name__}")

    permitted = set(allow)
    if unknown := set(document) - permitted:
        raise ConfigFatal(
            f"{config}: unknown top-level keys {sorted(unknown)}. Known: {sorted(permitted)}."
        )
    return document


__all__ = ["read_mapping"]
