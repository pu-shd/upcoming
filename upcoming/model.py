"""The canonical event.

Three decisions here carry most of the design's weight.

**Identity is source-scoped.** ``ps_events`` UIDs are per-site sequences, not global
identifiers -- measured, not assumed: ``ps_events:4056:delta:0`` is ai's "ORFE Colloquium"
*and* materials' "Materials Institute Symposium", two unrelated events, and 12 of 125 nids
collide across these feeds. So ``id`` is ``"{source}:{guid}"`` and ``guid`` alone is never
an identity. A dedupe key of ``guid`` by itself would silently merge unrelated events.

**``sources`` is always a sequence**, length 1 in a per-source feed and length N in a
combo. A shape that changed between the two output kinds would force
``Array.isArray(...) ? ... : [...]`` into every consumer, and the branch exercised least is
the one that breaks in production.

**Text in the model is clean; escaping happens at serialization.** The predecessor kept
``escape_name_commas`` and friends as model-level config, which is why retargeting to a
second department required flipping a per-source boolean: one knob governed both a speaker
name (where re-escaping is right) and a talk title (where it mangles
``Winds\\, Waves\\, and Wakes``). Escaping is a wire-format concern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

#: Platform identifiers. The parse and enrichment layers must not assume Site Builder:
#: kellercenter serves ``PRODID:-//Drupal iCal API//EN`` from a stable-looking URL.
PLATFORM_SITE_BUILDER = "princeton-site-builder"
PLATFORM_DRUPAL_ICAL = "drupal-ical-api"

#: The compact form the feeds actually publish, e.g. ``20260915T203000Z``.
_ICS_UTC_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$")


def normalize_instant(value: str) -> str:
    """Return an absolute instant as canonical UTC, ``YYYY-MM-DDTHH:MM:SSZ``.

    Instants are compared as strings throughout -- for ordering, for the all-details-match
    dedupe check, and for byte-stable output -- so the string form has to be canonical or
    string comparison stops meaning instant comparison. ``2026-09-15T16:30:00-04:00`` and
    ``2026-09-15T15:30:00-05:00`` are the same moment; left as written they sort apart and
    would defeat a merge that should succeed.

    A value with no offset is rejected rather than assumed to be UTC or local. Guessing
    shifts an event by hours, and the predecessor's time helper refuses the mirror-image
    case for exactly this reason.
    """
    text = value.strip()
    if match := _ICS_UTC_RE.match(text):
        year, month, day, hour, minute, second = (int(g) for g in match.groups())
        parsed = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    else:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{value!r} is not a recognized instant") from exc
        if parsed.tzinfo is None:
            raise ValueError(
                f"{value!r} carries no UTC offset, so it is a wall clock rather than an "
                f"instant. Reading it as UTC or as local time would shift the event by "
                f"hours in one direction or the other."
            )
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class Location:
    """A venue, split only when the split is unambiguous.

    ``name`` is the venue and ``detail`` the room. When a value cannot be split with
    confidence the whole string belongs in ``name`` -- guessing a split is worse than
    declining to, because the schema calls ``name`` the venue and a wrong guess puts a
    building into the room field. ``id`` is reserved for a canonical venue id and is
    normally empty.
    """

    name: str = ""
    id: str = ""
    detail: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.name or self.detail)


@dataclass(frozen=True, slots=True)
class Event:
    """One event, after mapping and before serialization."""

    # --- identity -----------------------------------------------------------------
    #: ``"{source}:{guid}"``. THE identifier: unique within every feed and every combo.
    id: str
    #: The upstream UID, verbatim. Source-scoped, NOT global -- see the module docstring.
    #: Published because it is what the downstream ingest keys on and what a site's own
    #: Drupal recognizes, but never used alone as an identity.
    guid: str
    #: Every registered source whose feed carried this exact event.
    sources: tuple[str, ...]
    platform: str

    # --- when ---------------------------------------------------------------------
    #: Absolute instant, ISO-8601 with offset. The comparison key for dedupe and ordering.
    #: Never compare wall-clock times across sources: it silently merges events an hour
    #: apart the moment one source is not in America/New_York.
    starts_at: str
    ends_at: str
    #: Local wall clock without offset -- the predecessor's published shape, retained
    #: because the campus ingest parses it.
    start_time: str
    end_time: str
    timezone: str

    # --- what ---------------------------------------------------------------------
    title: str
    url: str
    location: Location = field(default_factory=Location)
    speaker: str = ""
    affiliation: str = ""
    #: Raw categories joined for the predecessor's consumers.
    series: str = ""
    #: Canonical tags -- what combo predicates match on. Sorted for stable output.
    tags: tuple[str, ...] = ()
    #: Categories exactly as the feed spelled them, so an unmapped vocabulary is visible
    #: rather than dropped.
    raw_categories: tuple[str, ...] = ()
    #: Clean, unescaped, normalized text. Escaping is applied when writing.
    content: str = ""

    # --- enrichment ---------------------------------------------------------------
    raw_details: str = ""
    abstract: str = ""
    bio: str = ""

    # --- placeholders the existing ingest contract expects ------------------------
    cancelled: str = ""
    banner_image: str = ""
    item_type: str = "advertisement"

    # --- audit --------------------------------------------------------------------
    #: The untouched SUMMARY, so a mapping decision can be re-examined from output alone.
    summary_raw: str = ""
    location_raw: str = ""
    #: Ids of the mapping rules that wrote fields, in the order they fired.
    mapping_rules: tuple[str, ...] = ()
    #: Which location rule matched, or ``"declined"``.
    location_rule: str = ""
    #: Text a rule parked rather than guessing which field it belonged to.
    summary_rest: str = ""
    #: ``TitleSource`` value, or None when not yet resolved.
    title_source: str | None = None
    title_is_placeholder: bool = False
    #: True when an enrichment comparison disagreed with the feed-derived value.
    mapping_conflict: bool = False

    @staticmethod
    def make_id(source: str, guid: str) -> str:
        """Build the namespaced identifier.

        Kept as one function so nothing reconstructs the convention by string formatting.
        """
        return f"{source}:{guid}"

    @property
    def source(self) -> str:
        """The first source, for a single-source feed.

        Raises when the event carries more than one source: in a combo there is no single
        source, and silently returning ``sources[0]`` there would be a small lie that
        anything sorting or labelling by it would propagate.
        """
        if len(self.sources) != 1:
            raise ValueError(
                f"{self.id} carries {len(self.sources)} sources; use .sources, not .source"
            )
        return self.sources[0]

    def with_sources(self, sources: tuple[str, ...]) -> Event:
        """Return a copy carrying a different source set, for combo merging."""
        return replace(self, sources=sources)

    def sort_key(self) -> tuple[str, str]:
        """Total ordering: ``(starts_at, id)``.

        The tiebreaker is not optional. The predecessor sorts on start time alone, and
        several of these departments hold 4:30pm seminars, so equal-start events can emit
        in different orders between runs -- which churns the published file, defeats the
        watchdog's byte-for-byte comparison, and defeats any hash-based skip gate.
        """
        return (self.starts_at, self.id)


#: Wire key order. Explicit rather than derived from the dataclass so that reordering
#: fields for readability cannot change published bytes.
WIRE_FIELDS: tuple[tuple[str, str], ...] = (
    ("id", "id"),
    ("guid", "guid"),
    ("sources", "sources"),
    ("platform", "platform"),
    ("startsAt", "starts_at"),
    ("endsAt", "ends_at"),
    ("startTime", "start_time"),
    ("endTime", "end_time"),
    ("timezone", "timezone"),
    ("title", "title"),
    ("speaker", "speaker"),
    ("affiliation", "affiliation"),
    ("series", "series"),
    ("tags", "tags"),
    ("rawCategories", "raw_categories"),
    ("location", "location"),
    ("urlRef", "url"),
    ("content", "content"),
    ("rawEventDetails", "raw_details"),
    ("rawExtractAbstract", "abstract"),
    ("rawExtractBio", "bio"),
    ("cancelled", "cancelled"),
    ("bannerImage", "banner_image"),
    ("itemType", "item_type"),
    ("summaryRaw", "summary_raw"),
    ("locationRaw", "location_raw"),
    ("mappingRules", "mapping_rules"),
    ("locationRule", "location_rule"),
    ("summaryRest", "summary_rest"),
    ("titleSource", "title_source"),
    ("titleIsPlaceholder", "title_is_placeholder"),
    ("mappingConflict", "mapping_conflict"),
)


def to_wire(event: Event) -> dict[str, Any]:
    """Render an event as the published JSON object.

    Key order is fixed by ``WIRE_FIELDS``; sequences become lists. No timestamp of *this
    run* appears anywhere -- that absence is what makes byte-for-byte verification of the
    published feed possible, so every generated-at value belongs in the health manifest
    instead.
    """
    out: dict[str, Any] = {}
    for wire_key, attr in WIRE_FIELDS:
        value = getattr(event, attr)
        if isinstance(value, Location):
            out[wire_key] = {"name": value.name, "id": value.id, "detail": value.detail}
        elif isinstance(value, tuple):
            out[wire_key] = list(value)
        else:
            out[wire_key] = value
    return out
