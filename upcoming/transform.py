"""``RawEvent`` to ``Event``, driven by the source's declared ``summary_role``.

This is where the project's central decision is applied. ORFE's ICS ``SUMMARY`` carries the
speaker; MAE's carries the talk title. Both feeds satisfy the same schema, so reading one
as the other produces valid, error-free output with the two fields transposed and nothing
anywhere reporting a problem.

The role is therefore **declared per source and has no default**. A role this module cannot
yet handle raises ``SourceFatal`` rather than falling back to a plausible mapping: a source
that fails to build is a problem someone fixes, and a source that builds wrongly is a
problem nobody notices.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import locate, provenance, rules
from .errors import SourceFatal
from .model import Event, Speaker, normalize_instant
from .parse import RawEvent
from .registry import SourceConfig

#: Roles this module implements. ``composite`` and ``mixed`` need the rule engine, because
#: their SUMMARY carries several fields at once (quantum) or means different things on
#: different events of one feed (ai).
IMPLEMENTED_ROLES = frozenset({"speaker", "title", "rules"})

#: The compact ICS form, ``20260915T203000Z`` or ``20260915T163000``.
_ICS_DT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(?P<utc>Z)?)?$")

_TZID_RE = re.compile(r"TZID=(?P<tzid>[^;:]+)")

#: Local wall-clock output format, matching the predecessor's published shape because the
#: campus ingest parses it.
_WALL_FORMAT = "%Y-%m-%dT%H:%M:%S"

#: A trailing institution in a ``Name, Institution`` string. Deliberately narrow: it wants
#: evidence the tail is an organisation, not merely that a comma exists. "Zhuoran Yang *22,
#: Yale University" splits; "Bliley, Forjaz" does not.
_INSTITUTION_RE = re.compile(
    r"\b("
    r"universit(y|e|at|ät|à|é)|univ\.?|college|institut(e|o|)|school|academy|"
    r"laborator(y|ies)|lab|centre|center|hospital|foundation|"
    r"observatory|museum|academ(y|ia)|polytechnic|"
    r"MIT|NASA|CERN|NIH|NIST|INRIA|EPFL|ETH|CNRS|Quantinuum"
    r")\b",
    re.IGNORECASE,
)


def _timezone(name: str, slug: str) -> ZoneInfo:
    """The source's timezone, or a hard failure.

    The predecessor wraps its timezone conversion in a bare ``except Exception`` and falls
    through with the *unconverted* value, so a typo'd zone publishes a silently wrong local
    time -- four or five hours off, with no warning anywhere.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SourceFatal(
            slug,
            f"timezone {name!r} is not a known IANA zone. Guessing would publish every "
            f"event at the wrong time with nothing reporting it.",
        ) from exc


def _parse_ics_datetime(value: str, params: str, zone: ZoneInfo, slug: str) -> datetime:
    """Decode a ``DTSTART``/``DTEND`` value into an aware datetime."""
    match = _ICS_DT_RE.match(value.strip())
    if not match:
        raise SourceFatal(slug, f"cannot read {value!r} as an ICS date-time")

    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    hour, minute, second = (int(match.group(i) or 0) for i in (4, 5, 6))

    if match.group("utc"):
        return datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo("UTC"))

    if tzid := _TZID_RE.search(params or ""):
        return datetime(
            year, month, day, hour, minute, second, tzinfo=_timezone(tzid.group("tzid"), slug)
        )

    # A floating time means "local wherever this is read", and for a departmental calendar
    # that is the department's own zone.
    return datetime(year, month, day, hour, minute, second, tzinfo=zone)


def split_speaker(value: str) -> Speaker:
    """Split ``Name, Institution`` into a pair, or decline and keep the whole string.

    Only splits when the tail carries evidence of being an institution. Two bare surnames
    separated by a comma are not a name and an affiliation, and guessing would attribute an
    institution nobody named.

    Splits on the **last** comma, so "Zhuoran Yang *22, Yale University" and "Xinghua
    Zheng, Hong Kong University of Science and Technology" both work.
    """
    text = " ".join(value.split())
    if "," not in text:
        return Speaker(name=text)

    head, _, tail = text.rpartition(",")
    head, tail = head.strip(), tail.strip()
    if head and tail and _INSTITUTION_RE.search(tail):
        return Speaker(name=head, affiliation=tail)
    return Speaker(name=text)


def decode_entities(value: str) -> str:
    """Decode HTML entities and normalize the non-breaking space.

    These feeds carry a department's HTML body flattened into a plain-text ICS field, so
    ``&amp;``, ``&gt;`` and ``&nbsp;`` arrive as literal character sequences. A consumer
    rendering ``content`` as text would show them verbatim, which is not what the publisher
    wrote.

    The predecessor decides this inconsistently: ORFE's committed golden has ``&gt;``
    decoded, while MAE's still carries ``&amp\\;`` and ``&nbsp\\;`` undecoded -- from the
    same code. That inconsistency is the evidence nobody decided; this decodes, once, after
    ICS unescaping.

    ``html.unescape`` is a single pass, so ``&amp;gt;`` correctly yields ``&gt;`` rather
    than ``>``.
    """
    return html.unescape(value).replace("\xa0", " ")


def _series(categories: Sequence[str]) -> str:
    """Categories joined with a bare comma and no space, in the order the feed lists them.

    The delimiter is kept exactly, because downstream code in both predecessors splits this
    field on ``","``.

    The **order** is the feed's, not sorted. The predecessor sorts, but only because it
    reads categories out of a ``set`` and needs some way to be deterministic; the committed
    golden predates that and preserves feed order -- ORFE publishes
    ``Optimization Seminar,ORFE Department Colloquia``, where sorting would put ``ORFE``
    first. Parsing to an ordered tuple makes sorting unnecessary, and the publisher's own
    order is the more faithful answer.
    """
    return ",".join(categories)


def transform_event(raw: RawEvent, source: SourceConfig, *, tags: Sequence[str] = ()) -> Event:
    """One ``RawEvent`` into one ``Event``, per the source's declared role."""
    role = source.expectations.summary_role
    if role not in IMPLEMENTED_ROLES:
        raise SourceFatal(
            source.slug,
            f"summary_role {role!r} is not implemented. Refusing rather than guessing: a "
            f"summary read as the wrong field produces valid output with the speaker and "
            f"title transposed and nothing reporting it.",
        )

    if not raw.uid:
        raise SourceFatal(source.slug, f"event at position {raw.ordinal} has no UID")
    if not raw.has_times:
        raise SourceFatal(source.slug, f"{raw.uid} has no DTSTART")

    zone = _timezone(source.timezone, source.slug)
    starts = _parse_ics_datetime(raw.dtstart, raw.dtstart_params, zone, source.slug)
    ends = (
        _parse_ics_datetime(raw.dtend, raw.dtend_params, zone, source.slug) if raw.dtend else starts
    )

    title = ""
    speakers: tuple[Speaker, ...] = ()
    title_source: str | None = None
    mapping_rule = f"{source.slug}:summary-is-{role}"
    summary_rest = ""
    extra_tags: tuple[str, ...] = ()

    summary = " ".join(decode_entities(raw.summary).split())
    if role == "speaker":
        # ORFE: the feed names who is speaking; the talk's title lives on the event page
        # and is filled by enrichment or synthesized by the fallback chain.
        if summary:
            speakers = (split_speaker(summary),)
    elif role == "title":
        # MAE: the feed names the talk. A sentinel is absence, not a title -- publishing
        # "TBD" satisfies the schema's minLength and is wrong, and the predecessor does
        # exactly that with no provenance flags at all, so a consumer cannot even tell.
        if not provenance.is_missing(summary):
            title = summary
            title_source = provenance.TitleSource.ICS.value
    else:
        # The summary means different things on different events of this feed, so the
        # mapping is declared as an ordered chain and the rule that fired is published.
        outcome = rules.apply_chain(
            summary, source.summary_rules, source.vocabulary, slug=source.slug
        )
        mapping_rule = outcome.rule_id
        summary_rest = outcome.unresolved
        extra_tags = outcome.tags

        assigned = outcome.assigned
        if person := assigned.get("speakers"):
            speakers = (split_speaker(person),)
        if found := assigned.get("affiliation"):
            # A separately captured affiliation is more reliable than one split out of the
            # name, so it wins: the pattern knew where the parenthetical was.
            speakers = tuple(Speaker(name=s.name, affiliation=found) for s in speakers)
        candidate = assigned.get("title", "")
        if candidate and not provenance.is_missing(candidate):
            title = candidate
            title_source = provenance.TitleSource.ICS.value
        # `series_extra` is the series the summary itself names, which the feed's
        # CATEGORIES may not. Kept as a tag rather than overwriting CATEGORIES, so the
        # publisher's own categorisation is never silently replaced.
        if series_extra := assigned.get("series_extra"):
            extra_tags = (*extra_tags, series_extra)

    location, location_rule = locate.parse_location(
        decode_entities(raw.location), source.location_rules
    )

    return Event(
        id=Event.make_id(source.slug, raw.uid),
        guid=raw.uid,
        sources=(source.slug,),
        platform=source.platform,
        starts_at=normalize_instant(starts.isoformat()),
        ends_at=normalize_instant(ends.isoformat()),
        start_time=starts.astimezone(zone).strftime(_WALL_FORMAT),
        end_time=ends.astimezone(zone).strftime(_WALL_FORMAT),
        timezone=source.timezone,
        title=title,
        url=raw.url,
        location=location,
        speakers=speakers,
        series=_series(raw.categories),
        tags=tuple(tags) + extra_tags,
        raw_categories=tuple(raw.categories),
        content=decode_entities(raw.description),
        summary_raw=raw.summary,
        location_raw=raw.location,
        mapping_rules=(mapping_rule,),
        summary_rest=summary_rest,
        location_rule=location_rule,
        title_source=title_source,
        title_is_placeholder=provenance.is_placeholder_source(title_source),
    )


def transform(raw_events: Sequence[RawEvent], source: SourceConfig) -> tuple[Event, ...]:
    """Every event for one source, in a deterministic order.

    Ordered by ``(starts_at, id)`` rather than start time alone. ORFE's feed carries two
    events at the same instant, so without the tiebreaker they are free to swap between
    runs -- which churns the published file and defeats byte-for-byte verification.
    """
    events = [transform_event(raw, source) for raw in raw_events]
    return tuple(sorted(events, key=Event.sort_key))
