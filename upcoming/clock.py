"""The published instant format, defined once.

Four modules wrote ``"%Y-%m-%dT%H:%M:%SZ"`` independently: the model normalizing event
times, publishing stamping ``status.json``, the heartbeat stamping its keepalive, and the
watchdog *parsing* what publishing wrote. That last pairing is why this module exists --
the producer and the consumer of the same string were separately hardcoded, so a change to
one would make the watchdog report a perfectly healthy site as broken, with a message
saying the timestamp is not a timestamp.

Everything here is explicit about the clock. ``now()`` is the only reader of it in the
package, and every other function takes the instant it should use, so a caller can make a
run reproducible and nothing reaches for the wall clock behind its back.
"""

from __future__ import annotations

from datetime import UTC, datetime

#: Zulu seconds. No sub-second component and no offset spelling, because these strings are
#: sorted and compared as text in published feeds -- a mixture of "+00:00" and "Z", or of
#: present and absent milliseconds, would order two equal instants inconsistently.
INSTANT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: The exact width of a formatted instant, for the cheap shape check callers want.
INSTANT_WIDTH = 20


def now() -> datetime:
    """The one place this package reads the clock."""
    return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    """Format an instant the way everything published spells it.

    Converts to UTC first: a caller holding a New York datetime means the same instant, and
    silently writing its local wall time under a ``Z`` would be a lie in the one format
    whose whole job is being unambiguous.
    """
    return moment.astimezone(UTC).strftime(INSTANT_FORMAT)


def parse(value: str) -> datetime | None:
    """Read one back, or ``None`` if it is not one.

    ``None`` rather than an exception because every caller is checking something it does
    not control -- a published file, an API response -- and "this is not a timestamp" is an
    answer those callers report rather than a condition they crash on.
    """
    try:
        return datetime.strptime(value, INSTANT_FORMAT).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


__all__ = ["INSTANT_FORMAT", "INSTANT_WIDTH", "now", "parse", "stamp"]
