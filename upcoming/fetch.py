"""Fetching, with the distinction the predecessor's fetch layer loses.

A page that returns 403 and a page that simply has no such element are **different
outcomes**, and conflating them is how a total scrape failure comes to look like a clean
run. The predecessor's ``fetch_subtitle`` catches its own ``raise_for_status`` and returns
``""``, so a run where every page 403s reports ``attempted=125 updated=0 errors=0`` --
byte-identical to a run where every page was fetched fine and legitimately lacked the
element. Its caller's ``except`` clause can never fire, because the helper is written never
to raise.

That is why ``FetchOutcome`` carries a status rather than a string, and why the health gate
downstream reads a **success rate** instead of an error count.

Transports are injectable so tests run offline against committed page fixtures. That is the
predecessor's one genuinely good testing decision, carried forward.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .registry import SourceConfig

FetchStatus = Literal["ok", "http_error", "network_error", "not_modified"]


@dataclass(frozen=True)
class FetchOutcome:
    """What happened when we asked for a page.

    ``ok`` with an empty body is a real answer -- the page existed and was empty. It is not
    the same as ``http_error``, and nothing downstream may treat them alike.
    """

    status: FetchStatus
    url: str
    code: int | None = None
    body: str = ""
    error: str = ""
    #: Cache validators the server sent, to be replayed on the next request. Stored rather
    #: than acted on here, because deciding what to do with them is the caller's business.
    etag: str = ""
    last_modified: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def failed(self) -> bool:
        """A request that did not reach the content. Never merely an empty page."""
        return self.status in {"http_error", "network_error"}

    @property
    def unchanged(self) -> bool:
        """The server said what we already have is current.

        Deliberately neither ``ok`` nor ``failed``: there is no body to build from, and
        nothing is wrong. Folding it into either would be a bug -- into ``ok`` and we would
        publish an empty feed, into ``failed`` and a healthy source would be marked stale
        every time it answered correctly.
        """
        return self.status == "not_modified"


class Transport(Protocol):
    """Anything that can answer a request. Injectable so tests need no network."""

    def __call__(
        self, url: str, *, headers: Mapping[str, str], timeout: tuple[float, float]
    ) -> FetchOutcome: ...


@dataclass
class HttpTransport:
    """The real one. Retries transient statuses; never retries a 403.

    A 403 on these hosts is a bot challenge, not a transient fault: retrying burns the
    request budget while the actual fix is a credential. The predecessor has no retry at
    all, so a single 502 loses an event's title until the next scheduled run.

    304 is returned immediately too, since ``retry_on`` does not name it -- retrying a
    conditional request that answered correctly would be absurd.
    """

    retries: int = 2
    retry_on: tuple[int, ...] = (429, 500, 502, 503, 504)
    backoff: float = 0.5
    _sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def __call__(
        self, url: str, *, headers: Mapping[str, str], timeout: tuple[float, float]
    ) -> FetchOutcome:
        import requests  # imported here so the stdlib-only paths stay import-light

        last: FetchOutcome | None = None
        for attempt in range(self.retries + 1):
            try:
                response = requests.get(url, headers=dict(headers), timeout=timeout)
            except Exception as exc:
                last = FetchOutcome("network_error", url, error=f"{type(exc).__name__}: {exc}")
            else:
                etag = response.headers.get("ETag", "")
                modified = response.headers.get("Last-Modified", "")
                if response.status_code == 200:
                    return FetchOutcome(
                        "ok",
                        url,
                        code=200,
                        body=response.text,
                        etag=etag,
                        last_modified=modified,
                    )
                if response.status_code == 304:
                    # A conditional request answered: what we hold is current. The
                    # validators are echoed back so the next request can replay them even
                    # though this response carried no body.
                    return FetchOutcome(
                        "not_modified", url, code=304, etag=etag, last_modified=modified
                    )
                last = FetchOutcome(
                    "http_error",
                    url,
                    code=response.status_code,
                    error=f"HTTP {response.status_code}",
                )
                if response.status_code not in self.retry_on:
                    return last

            if attempt < self.retries:
                self._sleep(self.backoff * (2**attempt))

        assert last is not None
        return last


def file_transport(
    url: str, *, headers: Mapping[str, str], timeout: tuple[float, float]
) -> FetchOutcome:
    """Read a ``file://`` URL, for building from a local capture."""
    path = Path(urlparse(url).path)
    try:
        return FetchOutcome("ok", url, code=200, body=path.read_text(encoding="utf-8"))
    except OSError as exc:
        return FetchOutcome("network_error", url, error=str(exc))


@dataclass
class PageCache:
    """Fetch and parse each URL at most once per run.

    The predecessor issues a separate request per *field* against the same page -- one for
    the subtitle, one for the content, one for the raw details -- and the ``session_cache``
    parameter that would have prevented it is never passed by its caller, so it defaults to
    a fresh empty dict on every call. A production run therefore fetches every event page
    two or three times.

    The cache also holds failures. A URL that 403s once will 403 again, and re-asking is
    both slower and ruder to a host that has already said no.
    """

    transport: Transport
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: tuple[float, float] = (5.0, 15.0)
    _pages: dict[str, FetchOutcome] = field(default_factory=dict, repr=False)
    _soups: dict[str, object] = field(default_factory=dict, repr=False)

    #: How many distinct URLs were actually requested, for the health report.
    requests_made: int = 0

    def fetch(self, url: str) -> FetchOutcome:
        if url in self._pages:
            return self._pages[url]
        outcome = self.transport(url, headers=self.headers, timeout=self.timeout)
        self._pages[url] = outcome
        self.requests_made += 1
        return outcome

    def soup(self, url: str) -> object | None:
        """The parsed page, or None when the fetch failed.

        Parsed once and kept: every field scraped from one page reads the same tree.
        """
        if url in self._soups:
            return self._soups[url]

        outcome = self.fetch(url)
        if not outcome.ok:
            self._soups[url] = None
            return None

        from bs4 import BeautifulSoup

        # `html.parser` rather than lxml: it is stdlib, and changing parsers changes
        # sibling structure, which would change what the abstract/bio walker sees.
        parsed = BeautifulSoup(outcome.body, "html.parser")
        self._soups[url] = parsed
        return parsed

    @property
    def outcomes(self) -> Mapping[str, FetchOutcome]:
        return dict(self._pages)


__all__ = [
    "FetchOutcome",
    "FetchStatus",
    "HttpTransport",
    "PageCache",
    "Transport",
    "file_transport",
]


@dataclass(frozen=True)
class Validators:
    """What a server told us last time, replayed to ask "has it changed?"

    Sending these turns a full download into a 304 with no body on the common case, which
    is every run where a department has not touched its calendar. Politeness with a
    measurable number attached: these twelve feeds are fetched a few hundred times a day
    between them.
    """

    etag: str = ""
    last_modified: str = ""

    @property
    def empty(self) -> bool:
        return not (self.etag or self.last_modified)

    def headers(self) -> dict[str, str]:
        """The conditional request headers, omitting whichever validator we lack."""
        out: dict[str, str] = {}
        if self.etag:
            out["If-None-Match"] = self.etag
        if self.last_modified:
            out["If-Modified-Since"] = self.last_modified
        return out


def fetch_feed(
    source: SourceConfig, transport: Transport, validators: Validators | None = None
) -> FetchOutcome:
    """Fetch one source's ICS, conditionally when we hold validators for it.

    The source's own HTTP policy applies -- its headers, its timeouts, its retry count --
    because the departments differ: some sit behind the bot challenge that needs the bypass
    header, others do not, and one has been slow enough to need a longer read timeout.

    A source with no ``feed_url`` is a configuration error rather than a fetch failure, and
    says so, because the two have different fixes.
    """
    if not source.feed_url:
        return FetchOutcome("network_error", "", error=f"{source.slug} declares no feed_url")
    headers = dict(source.http.headers)
    if validators is not None:
        # The source's own headers win a collision: a hand-set If-None-Match in config
        # would be deliberate, and silently overwriting it would be the surprising choice.
        headers = {**validators.headers(), **headers}
    return transport(
        source.feed_url,
        headers=headers,
        timeout=(source.http.connect_timeout, source.http.read_timeout),
    )


def fetch_feeds(
    sources: Sequence[SourceConfig],
    dest: Path,
    transport: Transport,
    validators: Mapping[str, Validators] | None = None,
) -> dict[str, FetchOutcome]:
    """Fetch every source into ``dest/<slug>/feed.ics``, returning what happened to each.

    A failed fetch writes nothing and leaves any previous capture untouched, so the caller
    can tell "we got new bytes" from "there are bytes here". Overwriting with a partial
    body, or deleting on failure, would both turn a transient upstream blip into a data
    loss -- and the build layer already knows how to serve a source's last good feed.

    A ``not_modified`` writes nothing for the same reason and for a better one: the server
    has just confirmed that what is on disk is current.
    """
    outcomes: dict[str, FetchOutcome] = {}
    for source in sources:
        held = (validators or {}).get(source.slug)
        # Only ask conditionally when there is actually a capture to validate. Sending
        # If-None-Match with no local copy invites a 304 we cannot build from.
        capture = dest / source.slug / "feed.ics"
        ask_conditionally = held if (held and capture.is_file()) else None

        outcome = fetch_feed(source, transport, ask_conditionally)
        outcomes[source.slug] = outcome
        if outcome.ok:
            capture.parent.mkdir(parents=True, exist_ok=True)
            capture.write_text(outcome.body, encoding="utf-8")
    return outcomes
