"""Reading a value off an event page, per the source's declared targets.

The same selector means different things on different sites. Measured across the live
hosts, ``div.event-subtitle`` carries:

* the **talk title** on orfe
* the **speaker and affiliation** on mae's seminar pages
* the **host** -- "Hosted by Alice Kunin" -- on materials

and shape varies inside a single source too: mae's FPO pages carry a speaker field with a
bare name and no subtitle, while its seminar pages carry the subtitle and no speaker field.

So three things are per source and declared rather than inferred:

1. **which targets** to scrape at all -- enrichment is opt-in, and a source with no
   verified selectors scrapes nothing
2. **which selectors**, in priority order -- tried left to right, because a page may carry
   more than one shape
3. **what to reject** -- materials' host line is a wrong-but-plausible value, and a target
   that declines it is the difference between an empty field and a published falsehood

Every decline is counted. A selector that always rejects is a signal, not a silence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from soupsieve.util import SelectorSyntaxError

from .fetch import PageCache
from .registry import EnrichTarget, SourceConfig


@dataclass
class ScrapeStats:
    """What enrichment actually did, in enough detail to tell success from silence."""

    #: Events with a URL to scrape.
    attempted: int = 0
    #: Values written.
    filled: int = 0
    #: Page fetched fine, selector matched nothing. Legitimate and expected.
    selector_misses: int = 0
    #: Page fetched fine, selector matched, value rejected by a declared pattern.
    rejected: int = 0
    #: Non-2xx. Never legitimate.
    http_errors: int = 0
    #: Transport failures.
    network_errors: int = 0
    #: Events with no URL at all.
    no_url: int = 0

    @property
    def reachable(self) -> int:
        """Attempts that reached a page, whatever the selector then found."""
        return self.attempted - self.http_errors - self.network_errors

    @property
    def success_rate(self) -> float:
        """Share of attempts that reached the page.

        **Reaching** the page, not finding a value -- plenty of event pages legitimately
        carry no abstract. This is the number the health gate reads, because an error
        *count* cannot see the failure that matters: the predecessor's fetch helper returns
        an empty string for a 403, so a total blackout reports zero errors and looks exactly
        like a clean run that found nothing.
        """
        return 1.0 if self.attempted == 0 else self.reachable / self.attempted

    @property
    def total_failures(self) -> int:
        return self.http_errors + self.network_errors

    def as_counts(self) -> dict[str, int]:
        return {
            "attempted": self.attempted,
            "filled": self.filled,
            "selector_misses": self.selector_misses,
            "rejected": self.rejected,
            "http_errors": self.http_errors,
            "network_errors": self.network_errors,
            "no_url": self.no_url,
        }


@dataclass(frozen=True)
class Scraped:
    """One value read off a page, and which selector produced it."""

    value: str
    selector: str


def select_first(soup: object, selectors: Sequence[str]) -> tuple[str, str] | None:
    """First selector that matches, in the order given, with its text.

    Tried left to right rather than handed to one call as a comma group: CSS resolves a
    group in *document* order, which would answer with whichever shape happens to appear
    first in the markup rather than the one this source prefers. mae needs exactly that --
    its FPO pages and its seminar pages carry different elements.

    Selectors stay a list rather than a comma-joined string. The predecessor splits on
    ``","`` with a plain ``str.split``, which shreds any selector containing ``:not(a, b)``
    or ``[data-x="a,b"]``.
    """
    if soup is None:
        return None
    for selector in selectors:
        try:
            found = soup.select_one(selector)  # type: ignore[attr-defined]
        except SelectorSyntaxError:
            # A malformed selector is that one target's problem, not the source's. The
            # registry cannot catch it, because validity depends on the CSS dialect.
            continue
        if found is None:
            continue
        text = " ".join(found.get_text(separator=" ", strip=True).split())
        if text:
            return text, selector
    return None


def select_all(soup: object, selectors: Sequence[str]) -> tuple[list[str], str] | None:
    """Every match for the first selector that matches at all.

    For fields that are genuinely plural. bioengineering's Rising Stars symposium lists
    four speakers in four separate elements, and taking only the first would publish one
    and drop three with nothing reporting it.
    """
    if soup is None:
        return None
    for selector in selectors:
        try:
            found = soup.select(selector)  # type: ignore[attr-defined]
        except SelectorSyntaxError:
            continue
        values = []
        for element in found:
            text = " ".join(element.get_text(separator=" ", strip=True).split())
            if text and text not in values:
                values.append(text)
        if values:
            return values, selector
    return None


def rejected_by(value: str, patterns: Sequence[re.Pattern[str]]) -> re.Pattern[str] | None:
    """The pattern that rejects ``value``, if any.

    materials' subtitle reads "Hosted by Alice Kunin" -- a real person, correctly scraped,
    and the wrong person. Publishing it as the speaker would be schema-valid and false,
    which is the failure this whole project is organised against.
    """
    for pattern in patterns:
        if pattern.search(value):
            return pattern
    return None


def scrape_target(
    url: str, target: EnrichTarget, cache: PageCache, stats: ScrapeStats
) -> list[str] | None:
    """Read one target off one page, or return None and record why.

    Returning None covers three different situations, and the stats keep them apart:
    the page could not be reached, the selectors found nothing, or the value was rejected.
    """
    if not url:
        stats.no_url += 1
        return None

    stats.attempted += 1
    outcome = cache.fetch(url)
    if outcome.status == "http_error":
        stats.http_errors += 1
        return None
    if outcome.status == "network_error":
        stats.network_errors += 1
        return None

    soup = cache.soup(url)
    picked = (
        select_all(soup, target.selectors)
        if target.plural
        else (lambda r: ([r[0]], r[1]) if r else None)(select_first(soup, target.selectors))
    )
    if picked is None:
        stats.selector_misses += 1
        return None

    values, _selector = picked
    kept = []
    for value in values:
        if rejected_by(value, target.reject_patterns):
            stats.rejected += 1
            continue
        kept.append(value)

    if not kept:
        return None

    stats.filled += 1
    return kept


def build_cache(source: SourceConfig, transport: object) -> PageCache:
    """A cache carrying this source's HTTP policy."""
    return PageCache(
        transport=transport,  # type: ignore[arg-type]
        headers=dict(source.http.headers),
        timeout=(source.http.connect_timeout, source.http.read_timeout),
    )


__all__ = [
    "ScrapeStats",
    "Scraped",
    "build_cache",
    "rejected_by",
    "scrape_target",
    "select_all",
    "select_first",
]
