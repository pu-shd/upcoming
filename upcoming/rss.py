"""Drupal events RSS to ``RawEvent``: the same records ``parse.py`` makes from ICS.

Computer Science publishes no iCal view. Its Drupal serves RSS 2.0 instead, and the RSS
core fields say nothing useful about *when* an event is: ``pubDate`` is when the node was
posted, in a non-RFC 822 spelling, and ``dc:date`` is the Unix epoch on every item. What
makes the feed usable is a site-specific ``events:`` namespace on each item::

    <events:date>Thursday, October 8, 2026 - 16:15
     - Thursday, October 8, 2026 - 17:15
    </events:date>
    <events:location>Computer Science Tea Room</events:location>
    <events:speaker>Ben Brooks</events:speaker>
    <events:speakerOrg>Black Forest Labs</events:speakerOrg>
    <events:hostOrg>Center for Information Technology Policy</events:hostOrg>

This module turns those items into ``RawEvent`` and stops. Producing the ICS-shaped record
rather than a second event type is what keeps everything downstream -- mapping, location
rules, selection, enrichment, the gates -- one implementation for every platform.

Like ``parse.py`` it interprets nothing it does not have to. The one exception is the date:
``events:date`` is wall-clock text with no zone, so it is rewritten into a *floating* ICS
date-time (``20261008T161500``), which the transform already reads as local to the source's
declared timezone -- the same path, and the same DST handling, as a floating ICS time.

A date this module cannot read fails the source rather than the event. Dropping one event
quietly is how a feed comes to be short with nothing reporting it; failing keeps the last
good feed published and names the item.

The XML is parsed with the standard library. The payload comes from a university web
server rather than from a user, and the bundled expat (2.6+ on every supported Python)
refuses the entity-expansion attacks that once made ``xml.etree`` unsafe for untrusted
input.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

from .errors import SourceFatal
from .parse import RawEvent

#: The namespace Computer Science's Drupal declares for its event fields. Matched by URI,
#: never by the ``events:`` prefix, since a prefix is only a local alias.
EVENTS_NS = "https://www.cs.princeton.edu"

_NS = {"events": EVENTS_NS}

#: One endpoint of ``events:date``: ``Thursday, October 8, 2026 - 16:15``, the time
#: optional for an all-day event. Whitespace is collapsed before matching, because the feed
#: puts a newline before the separating dash.
_STAMP = (
    r"(?P<{p}weekday>[A-Z][a-z]+day), (?P<{p}month>[A-Z][a-z]+) (?P<{p}day>\d{{1,2}}), "
    r"(?P<{p}year>\d{{4}})(?: - (?P<{p}hour>\d{{1,2}}):(?P<{p}minute>\d{{2}}))?"
)
_DATE_RE = re.compile(_STAMP.format(p="s_") + r"(?: - " + _STAMP.format(p="e_") + r")?")

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _text(item: ET.Element, path: str) -> str:
    """One child's text, whitespace-collapsed; empty when the element is absent."""
    return " ".join((item.findtext(path, default="", namespaces=_NS) or "").split())


def _all_text(item: ET.Element, path: str) -> list[str]:
    """Every matching child's text, collapsed, keeping empties so positions still pair."""
    return [" ".join((el.text or "").split()) for el in item.findall(path, _NS)]


def _endpoint(match: re.Match[str], prefix: str, where: str, slug: str) -> str:
    """One parsed endpoint as a floating ICS value: ``20261008T161500``, or ``20261008``.

    The weekday is checked against the date rather than ignored. It is redundant only while
    both halves agree, and a disagreement means this module has misread the format -- or the
    publisher has mistyped the day -- and either way the event would publish on the wrong
    date with nothing else noticing.
    """
    month_name = match.group(f"{prefix}month")
    if month_name not in _MONTHS:
        raise SourceFatal(slug, f"{where}: {month_name!r} is not a month name")
    try:
        day = date(
            int(match.group(f"{prefix}year")), _MONTHS[month_name], int(match.group(f"{prefix}day"))
        )
    except ValueError as exc:
        raise SourceFatal(slug, f"{where}: not a calendar date ({exc})") from exc

    weekday = match.group(f"{prefix}weekday")
    if weekday != _WEEKDAYS[day.weekday()]:
        raise SourceFatal(
            slug,
            f"{where}: says {weekday} but {day.isoformat()} is a {_WEEKDAYS[day.weekday()]}. "
            f"Refusing rather than guessing which half is right.",
        )

    if match.group(f"{prefix}hour") is None:
        return day.strftime("%Y%m%d")
    hour, minute = int(match.group(f"{prefix}hour")), int(match.group(f"{prefix}minute"))
    if hour > 23 or minute > 59:
        raise SourceFatal(slug, f"{where}: {hour:02d}:{minute:02d} is not a time of day")
    return f"{day.strftime('%Y%m%d')}T{hour:02d}{minute:02d}00"


def parse_event_dates(value: str, *, where: str, slug: str) -> tuple[str, str]:
    """``events:date`` text to a ``(dtstart, dtend)`` pair of floating ICS values.

    ``dtend`` is empty when the feed gives a single endpoint, which the transform reads as
    an event ending when it starts -- exactly as for an ICS event with no ``DTEND``.
    """
    text = " ".join(value.split())
    match = _DATE_RE.fullmatch(text)
    if not match:
        raise SourceFatal(
            slug,
            f"{where}: cannot read events:date {text!r}. Expected "
            f"'Thursday, October 8, 2026 - 16:15 - Thursday, October 8, 2026 - 17:15'.",
        )
    start = _endpoint(match, "s_", where, slug)
    end = _endpoint(match, "e_", where, slug) if match.group("e_year") else ""
    return start, end


def _speakers(item: ET.Element) -> tuple[tuple[str, str], ...]:
    """Pair each ``events:speaker`` with its ``events:speakerOrg``.

    Paired by position only when the two lists are the same length. When they are not,
    there is no telling which organisation belongs to whom, so every name is kept and every
    affiliation is dropped: attributing someone to the wrong institution is worse than
    attributing them to none.
    """
    names = _all_text(item, "events:speaker")
    orgs = _all_text(item, "events:speakerOrg")
    if len(orgs) != len(names):
        orgs = [""] * len(names)
    return tuple((name, org) for name, org in zip(names, orgs, strict=True) if name)


def parse_rss(text: str, slug: str) -> tuple[RawEvent, ...]:
    """Decode every ``<item>`` in ``text``, in the order the feed lists them.

    The item's ``link`` is its UID when it has no ``guid``. Drupal emits no ``guid`` here,
    and the link is the node's own path, so it is stable for exactly as long as the event
    page is -- which is as long as anything downstream could use it anyway.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SourceFatal(slug, f"feed is not well-formed XML ({exc})") from exc

    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise SourceFatal(
            slug,
            f"feed is not RSS: the root element is <{root.tag}>. A login page or an error "
            f"page served with status 200 looks exactly like this.",
        )

    events: list[RawEvent] = []
    for ordinal, item in enumerate(channel.findall("item")):
        title = _text(item, "title")
        where = f"item {ordinal} ({title or 'untitled'})"
        dates = _text(item, "events:date")
        if not dates:
            raise SourceFatal(slug, f"{where}: has no events:date")
        dtstart, dtend = parse_event_dates(dates, where=where, slug=slug)

        link = _text(item, "link")
        host_org = _text(item, "events:hostOrg")
        properties = {
            f"EVENTS:{child.tag.rsplit('}', 1)[-1].upper()}": " ".join((child.text or "").split())
            for child in item
            if child.tag.startswith(f"{{{EVENTS_NS}}}")
        }

        events.append(
            RawEvent(
                ordinal=ordinal,
                uid=_text(item, "guid") or link,
                summary=title,
                description=_text(item, "description"),
                location=_text(item, "events:location"),
                url=link,
                # The organising group is the closest thing this feed has to a series, and
                # it is what lets a source decline events another source already publishes.
                # Not a category: these are group names, not topics, and none of them has
                # a canonical tag. ``events:type`` is kept in `properties` only -- on most
                # items it reads "Event", which names nothing.
                series=host_org,
                dtstart=dtstart,
                dtend=dtend,
                speakers=_speakers(item),
                properties=properties,
            )
        )
    return tuple(events)


__all__ = ["EVENTS_NS", "parse_event_dates", "parse_rss"]
