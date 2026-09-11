"""Splitting a raw ICS ``LOCATION`` into a venue and a room.

An ordered chain of named rules. Each either returns a ``Location`` or **declines**, and
the first that matches wins. A source picks which rules it uses and in what order; the
rules themselves know nothing about any source.

The governing principle is the predecessor's, generalized: *guessing a split is worse than
declining to*. The schema calls ``name`` the venue, so an unsplittable value belongs there
whole. The predecessor's dash strategy puts it in ``detail`` instead, which left
``location.name`` empty on every single MAE event.

Both existing strategies fail on real values from these feeds, verified against the live
data rather than assumed:

===============================  ==========================================
value                            what the predecessor does
===============================  ==========================================
``Aaron Burr Hall, Room 216``    leaves ``, Room`` dangling in the venue name
``101 Sherrerd Hall``            no split at all -- neither strategy handles a
                                 leading room number without a dash
``E105 SEAS-BioE``               dash splits the hyphen *inside the building
                                 code*: name ``BioE``, detail ``E105 SEAS``
``Briger Hall (C112 + ...)``     the trailing ``)`` defeats the room regex
===============================  ==========================================
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

from .model import Location

#: A room designator: an optional one- or two-letter prefix, digits, an optional trailing
#: letter. Real values across these feeds: 222, 306, 008, J223, J401, E105, F212, F204,
#: C112, 216, A10B. Two letters are allowed because the predecessor's single-letter
#: ``[A-Z]?`` silently fails on a prefix like ``AB212`` and drops the whole value into the
#: venue name.
_ROOM = r"[A-Za-z]{0,2}\d{1,4}[A-Za-z]?"

_ROOM_ONLY_RE = re.compile(rf"^{_ROOM}$")

#: A dash separating a leading room from a venue, e.g. ``101 - Sherrerd Hall``. The
#: whitespace is required: without it this also matches the hyphen inside a building code
#: like ``SEAS-BioE``, which is exactly how the predecessor turns ``E105 SEAS-BioE`` into
#: name ``BioE``.
# Hyphen, en dash and em dash: publishers use all three.
_DASH_RE = re.compile(r"^(?P<detail>.+?)\s+[-\u2013\u2014]\s+(?P<name>.+)$")

_PARENTHETICAL_RE = re.compile(r"^(?P<name>.*\S)\s*\((?P<detail>[^()]+)\)$")

#: ``, Room 216`` or ``, C112``. The ``Room``/``Rm`` keyword is optional and stripped --
#: retaining it is what leaves ``Aaron Burr Hall, Room`` as the venue today.
_COMMA_ROOM_RE = re.compile(
    rf"^(?P<name>.*\S)\s*,\s*(?:Room|Rm\.?|Suite|Ste\.?)?\s*(?P<detail>{_ROOM})$",
    re.IGNORECASE,
)

#: Values meaning "not decided yet". Publishing ``TBD`` as a venue name is the same class
#: of problem as publishing it as a title: schema-valid, and wrong.
_SENTINELS = frozenset({"tbd", "t.b.d.", "tba", "to be announced", "to be determined", "n/a"})

#: What a rule returns when it will not guess.
DECLINED = "declined"


def _is_room(token: str) -> bool:
    return bool(_ROOM_ONLY_RE.match(token))


def rule_sentinel(raw: str) -> Location | None:
    """``TBD`` and friends mean no location, not a venue called TBD."""
    return Location() if raw.strip().casefold() in _SENTINELS else None


def rule_detail_dash_name(raw: str) -> Location | None:
    """``101 - Sherrerd Hall`` -> venue ``Sherrerd Hall``, room ``101``.

    ORFE's convention: the room comes first. Requires whitespace around the dash, so a
    hyphenated building code is left alone.
    """
    match = _DASH_RE.match(raw)
    if not match:
        return None
    return Location(name=match.group("name").strip(), detail=match.group("detail").strip())


def rule_slash_room(raw: str) -> Location | None:
    """``Engineering Quad J Wing/J223`` -> venue ``Engineering Quad J Wing``, room ``J223``.

    MAE's wing convention. Splits on the last slash, and only when the tail is
    room-shaped -- otherwise a venue that merely contains a slash would be mangled.
    """
    name, separator, detail = raw.rpartition("/")
    if not separator:
        return None
    name, detail = name.strip(), detail.strip()
    if not name or not _is_room(detail):
        return None
    return Location(name=name, detail=detail)


def rule_parenthetical(raw: str) -> Location | None:
    """``Briger Hall (C112 + Atrium C102)`` -> venue ``Briger Hall``, room ``C112 + Atrium C102``.

    The parenthetical is taken whole rather than parsed. It names more than one room, and
    inventing a structure for "C112 + Atrium C102" would be guessing.
    """
    match = _PARENTHETICAL_RE.match(raw)
    if not match:
        return None
    return Location(name=match.group("name").strip(), detail=match.group("detail").strip())


def rule_comma_room(raw: str) -> Location | None:
    """``Aaron Burr Hall, Room 216`` -> venue ``Aaron Burr Hall``, room ``216``.

    Also handles ``Briger Hall Auditorium, C112``. The ``Room`` keyword is dropped so the
    room reads the same as it would from any other convention.
    """
    match = _COMMA_ROOM_RE.match(raw)
    if not match:
        return None
    return Location(name=match.group("name").strip(), detail=match.group("detail").strip())


def rule_unambiguous_room(raw: str) -> Location | None:
    """Split when a room token sits at **exactly one** end of the value.

    One rule rather than separate room-first and room-last rules, because ``cbe`` writes
    both orders in the same feed -- ``E105 SEAS-BioE`` and ``SEAS-CBE F212`` -- so no fixed
    ordering of two rules can be right for both.

    Declines when both ends look like rooms, or neither does. ``Bowen Hall`` and
    ``Commons`` fall through to the venue, which is correct; ``101 Sherrerd 222`` is
    genuinely ambiguous and is left alone rather than guessed.
    """
    tokens = raw.split()
    if len(tokens) < 2:
        return None

    first_is_room = _is_room(tokens[0])
    last_is_room = _is_room(tokens[-1])
    if first_is_room == last_is_room:
        return None

    if last_is_room:
        return Location(name=" ".join(tokens[:-1]), detail=tokens[-1])
    return Location(name=" ".join(tokens[1:]), detail=tokens[0])


def rule_whole(raw: str) -> Location | None:
    """Always matches: the value becomes the venue, entire.

    The end of every chain. ``Maeder Hall Auditorium`` and ``Commons`` have no room
    component, and the schema calls ``name`` the venue -- so this is the honest answer,
    not a fallback.
    """
    return Location(name=raw.strip())


#: Rules a source may name, by the name it uses in ``config/sources.yaml``.
RULES: dict[str, Callable[[str], Location | None]] = {
    "sentinel": rule_sentinel,
    "detail_dash_name": rule_detail_dash_name,
    "slash_room": rule_slash_room,
    "parenthetical": rule_parenthetical,
    "comma_room": rule_comma_room,
    "unambiguous_room": rule_unambiguous_room,
    "whole": rule_whole,
}

#: Rules that must run before ``unambiguous_room``, and why.
#:
#: ``101 - Sherrerd Hall`` has a room token at its first position, so ``unambiguous_room``
#: would match it and leave ``- Sherrerd Hall`` as the venue. The dash rule has to see it
#: first. Asserted by test rather than left to whoever edits a chain next.
MUST_PRECEDE_UNAMBIGUOUS = ("sentinel", "detail_dash_name", "slash_room", "parenthetical")


def unknown_rules(names: Iterable[str]) -> tuple[str, ...]:
    """Rule names that do not exist, for the registry to reject at load."""
    return tuple(name for name in names if name not in RULES)


def parse_location(raw: str | None, rules: Sequence[str]) -> tuple[Location, str]:
    """Apply ``rules`` in order; return the location and the name of the rule that matched.

    The rule name is published on the event, so a location that came out wrong can be
    traced to the rule that produced it without re-running anything. ``DECLINED`` means no
    rule matched at all, which only happens when a chain omits ``whole``.
    """
    if raw is None or not raw.strip():
        return Location(), DECLINED

    value = " ".join(raw.split())
    for name in rules:
        rule = RULES.get(name)
        if rule is None:
            # Unknown names are rejected at config load; reaching here means a caller
            # bypassed the registry. Skipping is safer than raising mid-build, and the
            # published rule name will show the chain never matched.
            continue
        location = rule(value)
        if location is not None:
            return location, name
    return Location(), DECLINED
