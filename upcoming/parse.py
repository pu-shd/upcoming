"""ICS text to ``RawEvent``: every VEVENT, in feed order, nothing interpreted.

This layer deliberately makes no mapping decisions. It answers "what did the feed say",
and nothing else; what ``SUMMARY`` *means* is the transform's business, driven by the
source's declared ``summary_role``.

It also does not use ``ics.Calendar``, for two reasons that both bear on correctness:

* ``Calendar.events`` is a **set**, so feed order is lost. The predecessor recovers an
  order by sorting on start time with no tiebreaker, which leaves events sharing a start
  time free to swap between runs -- and ORFE's own fixture contains such a pair. Churn in
  the published file defeats byte-for-byte verification of the served feed.
* That sort is ``key=lambda e: e.begin or ""``, which raises ``TypeError`` comparing an
  ``Arrow`` to a ``str`` the moment any event lacks a ``DTSTART``.

Parsing the VEVENT blocks directly keeps the feed's own order available as a tiebreaker,
keeps duplicate-but-distinct events, and lets a malformed event be attributed rather than
crashing the source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Content lines are folded at 75 octets and continued with a leading space or tab
#: (RFC 5545 3.1). Unfold before anything else or every long DESCRIPTION is truncated
#: mid-word.
_FOLD_RE = re.compile(r"\r?\n[ \t]")

_VEVENT_RE = re.compile(r"^BEGIN:VEVENT\s*$(.*?)^END:VEVENT\s*$", re.S | re.M)

#: ``NAME;PARAM=value:content``. The name is case-insensitive per RFC 5545; parameters are
#: kept because ``DTSTART;TZID=...`` needs them.
_LINE_RE = re.compile(r"^(?P<name>[A-Za-z0-9-]+)(?P<params>;[^:]*)?:(?P<value>.*)$")

_PRODID_RE = re.compile(r"^PRODID:(?P<value>.*)$", re.M)

#: RFC 5545 3.3.11 TEXT escapes. Order matters: backslash last, so that a literal
#: ``\\,`` decodes to ``\,`` rather than to a separator.
_UNESCAPE = (
    (r"\N", "\n"),
    (r"\n", "\n"),
    (r"\,", ","),
    (r"\;", ";"),
    (r"\\", "\\"),
)


def unfold(text: str) -> str:
    """Join RFC 5545 folded content lines."""
    return _FOLD_RE.sub("", text)


def unescape_text(value: str) -> str:
    """Decode an RFC 5545 TEXT value.

    The model holds clean text: escaping is a wire-format concern applied when writing,
    never carried through the pipeline. The predecessor keeps ICS escaping in the model and
    re-escapes on output, which is why one per-source boolean had to govern both a speaker
    name (where re-escaping is right) and a talk title (where it mangles
    ``Winds\\, Waves\\, and Wakes``).
    """
    out: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == "\\" and index + 1 < length:
            pair = value[index : index + 2]
            for escape, plain in _UNESCAPE:
                if pair.lower() == escape.lower():
                    out.append(plain)
                    index += 2
                    break
            else:
                # An unknown escape is data, not a directive: keep the backslash rather
                # than dropping a character the publisher meant to send.
                out.append(char)
                index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out)


def split_list(value: str) -> tuple[str, ...]:
    """Split a comma-separated ICS list value, honouring escaped commas.

    ``CATEGORIES:Final Public Oral Examinations,Final Public Orals`` is two values, while
    ``CATEGORIES:Wilks\\, Memorial`` is one. A plain ``str.split(",")`` cannot tell them
    apart.
    """
    parts: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            current.append(value[index : index + 2])
            index += 2
            continue
        if char == ",":
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return tuple(unescape_text(p).strip() for p in parts if p.strip())


@dataclass(frozen=True, slots=True)
class RawEvent:
    """One VEVENT, decoded but uninterpreted.

    ``ordinal`` is the event's position in the feed. It exists so output ordering has a
    stable tiebreaker that does not depend on any field's value, and so a faithful
    per-source feed can preserve the publisher's own sequence.
    """

    ordinal: int
    uid: str = ""
    summary: str = ""
    description: str = ""
    location: str = ""
    url: str = ""
    categories: tuple[str, ...] = ()
    #: Raw ``DTSTART``/``DTEND`` values with their parameters, left as strings. Turning
    #: them into instants needs the source's timezone, which this layer does not know.
    dtstart: str = ""
    dtend: str = ""
    dtstart_params: str = ""
    dtend_params: str = ""
    #: Every decoded property, for fields a source maps that this dataclass does not name.
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def has_times(self) -> bool:
        return bool(self.dtstart)


def prodid(text: str) -> str:
    """The feed's ``PRODID``, used to check a source is on the platform it declares.

    Worth checking rather than assuming: ``kellercenter`` serves
    ``-//Drupal iCal API//EN`` from a URL that looks like every other one.
    """
    match = _PRODID_RE.search(unfold(text))
    return unescape_text(match.group("value").strip()) if match else ""


def parse_ics(text: str) -> tuple[RawEvent, ...]:
    """Decode every VEVENT in ``text``, in the order the feed lists them.

    Duplicate or near-duplicate events are all returned. ORFE's own feed carries two
    events with the same speaker and start time; suppressing one here would silently edit
    a publisher's data, and de-duplicating is a consumer's decision to make.
    """
    unfolded = unfold(text)
    events: list[RawEvent] = []

    for ordinal, block in enumerate(_VEVENT_RE.findall(unfolded)):
        properties: dict[str, str] = {}
        params: dict[str, str] = {}
        categories: list[str] = []

        for line in block.splitlines():
            if not line.strip():
                continue
            match = _LINE_RE.match(line.strip())
            if not match:
                continue
            name = match.group("name").upper()
            raw_params = (match.group("params") or "").lstrip(";")
            value = match.group("value")

            if name == "CATEGORIES":
                categories.extend(split_list(value))
                continue

            decoded = unescape_text(value).strip()
            if name in {"DTSTART", "DTEND"}:
                # Keep these undecoded-ish: the value is a date-time, not TEXT, and the
                # parameters carry the timezone.
                properties[name] = value.strip()
                params[name] = raw_params
            else:
                properties[name] = decoded

        events.append(
            RawEvent(
                ordinal=ordinal,
                uid=properties.get("UID", ""),
                summary=properties.get("SUMMARY", ""),
                description=properties.get("DESCRIPTION", ""),
                location=properties.get("LOCATION", ""),
                url=properties.get("URL", ""),
                categories=tuple(categories),
                dtstart=properties.get("DTSTART", ""),
                dtend=properties.get("DTEND", ""),
                dtstart_params=params.get("DTSTART", ""),
                dtend_params=params.get("DTEND", ""),
                properties=properties,
            )
        )

    return tuple(events)
