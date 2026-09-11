"""Applying a source's scrape targets to its events.

Ordering matters and is fixed here rather than left to a caller. Enrichment runs **after**
mapping and **before** the title fallback, so a real title scraped off the page is used in
preference to a synthesized one -- and the fallback only fires for what is still missing.

Provenance is stamped only when the field written is the field the flag describes. A value
scraped into ``speakers`` says nothing about where the title came from, and marking it
``enriched`` would report a still-unknown title as real. That rule comes from the
predecessor's retargeting commit and it is the difference between a useful flag and a
misleading one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from .fetch import PageCache
from .model import Event, Speaker
from .provenance import TitleSource, is_missing
from .registry import EnrichTarget, SourceConfig
from .scrape import ScrapeStats, scrape_target
from .transform import split_speaker


def _apply(event: Event, target: EnrichTarget, values: Sequence[str]) -> Event:
    """Write scraped values onto one event, per the target's declared field."""
    field = target.field_name

    if field == "speakers":
        speakers = tuple(
            split_speaker(value) if target.split_affiliation else Speaker(name=value)
            for value in values
        )
        # `fill` never overwrites what the feed already supplied: the publisher's own
        # structured data outranks anything read off a rendered page.
        if event.speakers and target.mode == "fill":
            return event
        return replace(event, speakers=speakers)

    value = values[0]
    if field == "title":
        if not is_missing(event.title) and target.mode == "fill":
            return event
        if is_missing(value):
            return event
        return replace(
            event,
            title=value,
            # Only here. See the module docstring.
            title_source=(
                TitleSource.ENRICHED.value
                if target.provenance == "enriched"
                else event.title_source
            ),
            title_is_placeholder=False,
        )

    # Everything else is a plain text field. Written explicitly rather than through
    # dynamic kwargs so a typo in a target name cannot silently write nowhere -- the
    # registry already restricts the field list, and this is the second half of that.
    current = getattr(event, field, "")
    if current and target.mode == "fill":
        return event
    if field == "raw_details":
        return replace(event, raw_details=value)
    if field == "abstract":
        return replace(event, abstract=value)
    if field == "bio":
        return replace(event, bio=value)
    if field == "content":
        return replace(event, content=value)
    return event


def carry(event: Event, held: Event, targets: Sequence[EnrichTarget]) -> Event:
    """Copy an earlier run's scraped values onto this run's event.

    Writes exactly the fields ``_apply`` writes, provenance included. Copying ``title``
    without ``title_source`` and ``title_is_placeholder`` would republish the same title
    while claiming it was synthesized -- and because the feeds are compared byte-for-byte
    to detect drift, that disagreement would show up as a change on every run.
    """
    out = event
    for target in targets:
        field = target.field_name
        if field == "speakers":
            if held.speakers:
                out = replace(out, speakers=held.speakers)
        elif field == "title":
            if held.title_source == TitleSource.ENRICHED.value:
                out = replace(
                    out,
                    title=held.title,
                    title_source=held.title_source,
                    title_is_placeholder=False,
                )
        elif value := getattr(held, field, ""):
            out = _apply(out, target, [value])
    return out


def enrich(
    events: Sequence[Event],
    source: SourceConfig,
    cache: PageCache,
    held: Mapping[str, Event] | None = None,
) -> tuple[tuple[Event, ...], dict[str, ScrapeStats]]:
    """Scrape every declared target for every event, and report what happened per target.

    Stats are kept **per target** rather than pooled. A source whose raw-details scrape
    succeeds everywhere and whose speaker scrape fails everywhere has a real problem that a
    combined rate would average away.

    ``held`` maps guid to an event from the previously published feed. Any event found
    there is satisfied from it and **not fetched**; anything else is scraped as usual. So a
    run inside a source's ``rebuild_after_hours`` window still enriches events that are new
    since the last scrape -- which is the case that makes an all-or-nothing window wrong,
    since a seminar added this morning would otherwise publish with no title until the
    window elapsed.
    """
    if not source.enrich:
        return tuple(events), {}

    stats = {target.field_name: ScrapeStats() for target in source.enrich}
    out = list(events)
    remembered = held or {}

    for index, event in enumerate(out):
        if previous := remembered.get(event.guid):
            out[index] = carry(event, previous, source.enrich)
            for target in source.enrich:
                stats[target.field_name].carried += 1
            continue
        for target in source.enrich:
            values = scrape_target(event.url, target, cache, stats[target.field_name])
            if values:
                out[index] = _apply(out[index], target, values)

    return tuple(out), stats


def breaches(stats: dict[str, ScrapeStats], source: SourceConfig) -> tuple[str, ...]:
    """Targets whose success rate falls below what the source declares acceptable.

    A **success rate**, never an error count. The predecessor's fetch helper returns an
    empty string for a 403, so a run where every page is blocked reports zero errors and
    reads exactly like a clean run that found nothing -- the number that would have caught
    it does not exist there.

    A source's whole enrichment being blocked is the common shape of this failure: the
    bypass credential is wrong, and every page 403s at once.
    """
    floor = source.expectations.min_enrich_success_rate
    out = []
    for field_name, stat in stats.items():
        if stat.attempted == 0:
            continue
        if stat.success_rate < floor:
            detail = (
                f"{field_name}: reached {stat.reachable} of {stat.attempted} pages "
                f"({stat.success_rate:.0%}, floor {floor:.0%}); "
                f"{stat.http_errors} HTTP error(s), {stat.network_errors} network error(s)"
            )
            if stat.http_errors == stat.attempted:
                detail += (
                    ". Every request failed, which is the shape of a bot challenge rather "
                    "than a content problem -- check the bypass credential for this host."
                )
            out.append(detail)
    return tuple(out)


__all__ = ["breaches", "enrich"]
