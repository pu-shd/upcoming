"""Turning events into published bytes. All escaping lives here.

The model carries clean text. Escaping is a property of the wire format, not of the event,
and putting it here is what lets one knob per *field* replace the predecessor's one knob
per *source*.

That distinction is not academic. The predecessor keeps ICS escaping in the model and
re-escapes on output under a single ``escape_name_commas`` boolean, which governs the
field ``SUMMARY`` maps to. On ORFE that field is the speaker, where re-escaping
``Elynn Chen\\, New York University`` is what the downstream ingest expects. On MAE the
same field is the title, where it mangles ``Winds\\, Waves\\, and Wakes``. One boolean,
two incompatible correct answers -- so retargeting the pipeline required flipping it, and
flipping it is part of why a fork happened.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .model import Event, to_wire

#: Re-add RFC 5545 TEXT escaping to a value. Idempotent: the negative lookbehind means an
#: already-escaped comma is left alone.
_COMMA_RE = re.compile(r"(?<!\\),")
_SEMICOLON_RE = re.compile(r"(?<!\\);")


def escape_ics_text(value: str) -> str:
    """Re-escape commas and semicolons, the way the campus ingest expects them."""
    return _COMMA_RE.sub(r"\\,", _SEMICOLON_RE.sub(r"\\;", value))


def collapse_whitespace(value: str) -> str:
    """Fold every run of whitespace, including newlines, into one space.

    Applied to free text so a publisher's line breaks and double spaces do not reach the
    feed. Note this is what production does; the predecessor's committed goldens were
    generated with it *off*, because setting ``represent_newlines_as = "literal_r"`` in a
    test also disables collapsing through
    ``collapse = collapse_whitespace_in_description and rep_mode == "space"``.
    """
    return " ".join(value.split())


@dataclass(frozen=True)
class WireFormat:
    """How one source's events are rendered.

    Per field, not per source, which is the whole point: ``escape_fields`` names exactly
    the fields whose commas the ingest wants escaped, so a source can escape its speaker
    without touching its title.
    """

    #: Fields to re-escape. ORFE's downstream ingest expects an escaped speaker.
    escape_fields: frozenset[str] = field(default_factory=frozenset)
    #: Fields whose whitespace is collapsed.
    collapse_fields: frozenset[str] = field(
        default_factory=lambda: frozenset({"content", "abstract", "bio"})
    )
    indent: int = 2
    #: Non-ASCII is escaped as ``\\uXXXX``, matching the predecessor's output so a
    #: consumer diffing the two sees no spurious change.
    ensure_ascii: bool = True

    def apply(self, wire: dict[str, Any]) -> dict[str, Any]:
        out = dict(wire)
        for key in self.collapse_fields:
            value = out.get(key)
            if isinstance(value, str) and value:
                out[key] = collapse_whitespace(value)
        for key in self.escape_fields:
            value = out.get(key)
            if isinstance(value, str) and value:
                out[key] = escape_ics_text(value)
            elif isinstance(value, list):
                # `speakers` is a list of objects; escape the strings inside it so the
                # structured form agrees with the scalar derived from it.
                out[key] = [_escape_item(item) for item in value]
        return out


def _escape_item(item: Any) -> Any:
    """Escape the strings inside one `speakers[]` object."""
    if not isinstance(item, dict):
        return item
    return {k: escape_ics_text(v) if isinstance(v, str) and v else v for k, v in item.items()}


def render_events(events: Sequence[Event], wire_format: WireFormat) -> list[dict[str, Any]]:
    """Wire dicts for ``events``, in a total, deterministic order.

    Sorted by ``(starts_at, id)``. The tiebreaker is not optional: several of these
    departments hold concurrent seminars, and ORFE's own feed carries two events at the
    same instant, so ordering on start time alone leaves them free to swap between runs.
    That churns the published file and defeats byte-for-byte verification of the served
    feed.
    """
    ordered = sorted(events, key=Event.sort_key)
    return [wire_format.apply(to_wire(event)) for event in ordered]


def dump_feed(events: Sequence[Event], wire_format: WireFormat) -> str:
    """The published bytes for one feed, ending in a newline.

    A trailing newline so the file is a well-formed text file and a diff does not report
    "\\ No newline at end of file" on every change. The predecessor omits it.
    """
    payload = render_events(events, wire_format)
    return (
        json.dumps(payload, indent=wire_format.indent, ensure_ascii=wire_format.ensure_ascii) + "\n"
    )


#: Fields a source may name under ``wire.escape``. Restricted so a typo is a config error
#: rather than an escaping rule that silently never applies.
ESCAPABLE_FIELDS = frozenset({"speaker", "speakers", "title", "content", "series", "affiliation"})


def wire_format_for(escape_fields: Sequence[str]) -> WireFormat:
    """Build a ``WireFormat`` from a source's declared ``wire.escape`` list."""
    return WireFormat(escape_fields=frozenset(escape_fields))
