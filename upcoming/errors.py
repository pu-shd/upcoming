"""Error taxonomy.

The distinction that matters is the blast radius, because it decides what still publishes:

- ``ConfigFatal``  -- the registry is wrong, so we do not know what correct output *is*.
                      Nothing publishes.
- ``SourceFatal``  -- one source cannot produce trustworthy output. The other sources still
                      publish; this one retains its last good feed, marked stale.
- ``RunFatal``     -- too much failed at once for a partial publish to be honest.

Nothing in this package catches bare ``Exception`` except the single per-source boundary in
the build. That rule exists because the predecessor caught ``Exception`` in seven places in
its enrichment module, which is why a total enrichment failure there is indistinguishable
from success.
"""

from __future__ import annotations


class UpcomingError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigFatal(UpcomingError):
    """A source, combo, or vocabulary file is invalid.

    Fatal for the whole run by design: a broken registry means we cannot know what correct
    output looks like, so publishing anything would be a guess.
    """


class SourceFatal(UpcomingError):
    """One source cannot produce trustworthy output.

    Carries the source id so the build boundary can attribute it without inspecting the
    message.
    """

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message


class RunFatal(UpcomingError):
    """Too many sources failed for a partial publish to be honest."""
