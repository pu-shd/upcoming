"""The source registry: every feed, what its fields mean, and how to fetch it.

This module is the **only** place in the package that reads the environment. That is not a
style preference. The predecessor reads ``os.getenv`` inside leaf functions in its
enrichment and transform modules -- 34 call sites across the package, 21 in enrichment
alone -- which makes per-source configuration impossible in one process, because two
sources would need two different values of the same process-global variable at the same
time. That impossibility is the root cause of the fork this project replaces. A test
asserts the rule holds.

Layering follows the predecessor's newsletter-schedule loader, which is the one config
system there that validates, raises on bad input, and hashes its resolved form:

    defaults  ->  per-source entry  ->  environment overrides (allowlisted)

Objects merge key-wise; **lists replace rather than append**, because a rule inherited into
the wrong position is harder to reason about than a missing one.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import locate, serialize
from .config import read_mapping
from .errors import ConfigFatal
from .model import PLATFORM_SITE_BUILDER
from .patterns import Vocabulary, load_vocabulary
from .predicate import validate as validate_predicate
from .rules import load_chain
from .tags import TagVocabulary, load_tags

#: Source lifecycle. ``unavailable`` sources are never fetched and never published -- they
#: exist so a gap is visible and reviewable instead of being forgotten.
STATUS_LIVE = "live"
STATUS_UNAVAILABLE = "unavailable"
VALID_STATUSES = frozenset({STATUS_LIVE, STATUS_UNAVAILABLE})

#: What a feed's SUMMARY field carries. There is deliberately **no default**: every default
#: is wrong for roughly half these feeds, and wrong *silently*, because the output still
#: validates with the title and speaker transposed. The only fix is to refuse to have one.
#:
#: ``rules`` means "it varies -- see summary.rules". It replaces an earlier `composite` and
#: `mixed` pair that both meant the same thing and that I had already misapplied: citp was
#: declared composite while its 24 events are 13 plain titles, 6 speaker-dash-title and 5
#: bare names. A label that cannot be checked against data, and is wrong in its first two
#: uses, is not documentation.
SUMMARY_ROLES = frozenset({"speaker", "title", "rules"})

#: The role whose mapping is declared rather than implied.
ROLE_RULES = "rules"

#: Environment overrides are a closed allowlist. Mapping decisions, selectors, and rules
#: are repo config only -- putting the most failure-prone decision in the system into an
#: invisible, unreviewed, untested repository variable is what made the fork's central bug
#: possible.
#: Suffixes a cadence may use. Minutes, hours, days -- nothing finer, because the
#: publishing schedule itself never ticks faster than every twenty minutes.
_CADENCE_UNITS = {"m": 1, "h": 60, "d": 1440}


def parse_cadence(value: str, default: int = 60) -> int:
    """Minutes for a cadence string like ``30m``, ``2h`` or ``1d``."""
    text = str(value).strip().lower()
    if len(text) < 2 or text[-1] not in _CADENCE_UNITS:
        return default
    try:
        count = int(text[:-1])
    except ValueError:
        return default
    return count * _CADENCE_UNITS[text[-1]] if count > 0 else default


ENV_OVERRIDABLE = frozenset({"feed_url", "status", "enrich_enabled", "cadence"})

#: Fields a scrape may write. Validated at load, so a typo is a config error rather than a
#: target that silently matches nothing.
ENRICHABLE_FIELDS = frozenset({"title", "speakers", "content", "raw_details", "abstract", "bio"})

#: Published but computed from another field, so scraping into them would be writing to a
#: value that is recomputed on read. Maps the wrong target to the right one.
DERIVED_FIELDS = {"speaker": "speakers", "affiliation": "speakers"}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class HttpPolicy:
    """How to talk to one host."""

    headers: Mapping[str, str] = field(default_factory=dict)
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    retries: int = 2
    #: A 403 here is a bot challenge, not a transient fault. Retrying burns the budget
    #: while the actual fix is a credential, so 403 is excluded from retryable statuses.
    retry_on: tuple[int, ...] = (429, 500, 502, 503, 504)


@dataclass(frozen=True)
class EnrichTarget:
    """One scrape: which field, from which selectors, and what to refuse."""

    field_name: str
    #: Tried **left to right**, not handed to CSS as one comma group -- a group would
    #: resolve in document order instead of the priority order written here. mae needs
    #: this: its FPO pages and its seminar pages carry different elements.
    selectors: tuple[str, ...]
    mode: str = "fill"
    #: Only ever ``"enriched"`` when ``field_name`` is ``"title"``. A value scraped into
    #: ``speaker`` says nothing about where the title came from, and stamping it would
    #: report a still-unknown title as real.
    provenance: str | None = None
    #: Values this target refuses. materials' subtitle reads "Hosted by Alice Kunin" -- a
    #: real person, correctly scraped, and the wrong one. A decline is counted, so a
    #: selector that always rejects surfaces rather than quietly yielding nothing.
    reject_patterns: tuple[re.Pattern[str], ...] = ()
    #: Whether every match is wanted rather than the first. bioengineering lists four
    #: speakers in four elements; taking one would drop three silently.
    plural: bool = False
    #: Whether a scraped value may carry an inline affiliation. mae's seminar pages give
    #: "Dr. Rebecca Ciez, Purdue University" while its FPO pages give a bare "Jun Eshima".
    split_affiliation: bool = False


@dataclass(frozen=True)
class Expectations:
    """The per-source band of normal. Breaching it is what makes a bad run loud.

    Every threshold is per source on purpose. A global constant would sit permanently
    breached for the sources whose normal is unusual -- kellercenter is legitimately empty,
    and quantum legitimately carries a placeholder title in 14 of 18 events -- and a gate
    that always fires trains everyone to ignore it.
    """

    summary_role: str
    min_events: int = 1
    allow_empty: bool = False
    require_nonempty: tuple[str, ...] = ()
    #: Success rate, not an error count. The predecessor's fetch helper catches its own
    #: ``raise_for_status`` and returns an empty string, so a run where every page 403s
    #: reports zero errors -- identical to a run where every page was fine and simply had
    #: no such element. Only a success rate can tell those apart.
    min_enrich_success_rate: float = 0.8
    #: Expected share of synthesized titles. High is normal for some sources; what should
    #: alert is a *change*, so this is a band plus drift rather than a ceiling.
    max_placeholder_title_rate: float = 1.0
    max_location_declined_rate: float = 0.3
    #: How much of the previously published feed may disappear in one build. The
    #: gate that catches a truncated upstream response, which is otherwise
    #: schema-valid and passes every other check.
    max_removed_ratio: float = 0.5


@dataclass(frozen=True)
class SourceConfig:
    """One fully resolved source."""

    slug: str
    label: str
    status: str
    platform: str
    timezone: str
    expectations: Expectations
    feed_url: str | None = None
    host: str = ""
    #: How often this source wants to be refetched, as "30m", "2h" or "1d". Declared per
    #: source because these servers differ: ORFE posts several times a week, cee posted
    #: once this term. Refetching a quiet departmental server every twenty minutes is
    #: seventy-two requests a day to be told nothing changed.
    cadence: str = "1h"
    #: Predicates over the source's own feed. ``publish_where`` keeps only matching events;
    #: ``publish_unless`` drops matching ones. Both use the same vocabulary as a combined
    #: feed's ``where`` / ``exclude_where``, because a department writing one after reading
    #: about the other should not be learning a second dialect.
    #:
    #: This makes a per-source feed a *declared* view of its upstream rather than an
    #: unconditional mirror, which is a real change in what the feed promises -- so the
    #: count it dropped is published in status.json rather than left for a consumer to
    #: notice by subtracting.
    publish_where: Mapping[str, Any] | None = None
    publish_unless: Mapping[str, Any] | None = None

    @property
    def publishes_everything(self) -> bool:
        """Whether this feed is an unfiltered mirror of its upstream."""
        return self.publish_where is None and self.publish_unless is None

    @property
    def cadence_minutes(self) -> int:
        """``cadence`` as minutes. Unparseable falls back to an hour rather than to zero.

        Zero would mean "always refetch", so a typo would silently turn the politeness this
        setting exists for into the maximum possible load on someone else's server.
        """
        return parse_cadence(self.cadence)

    #: Ordered location rule chain. Order is load-bearing and asserted by test.
    location_rules: tuple[str, ...] = ()
    enrich: tuple[EnrichTarget, ...] = ()
    http: HttpPolicy = field(default_factory=HttpPolicy)
    #: Asserted at runtime rather than assumed, so a platform whose UID scheme we have not
    #: seen cannot inherit Site Builder's.
    uid_pattern: str | None = None
    #: Why an ``unavailable`` source is unavailable. Required when status is unavailable so
    #: the investigation is recorded and nobody repeats it.
    reason: str = ""
    #: Rebuild even when the feed hash is unchanged, once the last build is this old. This
    #: bounds any wedged skip-gate to hours without human action, and it is the only thing
    #: that re-runs enrichment when an abstract is posted to the event page *after* the
    #: feed settles -- a defect both predecessors have and neither documents.
    rebuild_after_hours: int = 24
    #: Ordered mapping rules, for a source whose summary_role is `rules`. None otherwise.
    summary_rules: Any = None
    #: The shared pattern and predicate vocabulary the rules select from.
    vocabulary: Any = None
    #: The shared canonical-tag vocabulary. Shared, not per source: the point of a
    #: canonical tag is that every source reaches the same one.
    tags: Any = None
    #: Fields whose commas and semicolons are re-escaped on output. Per field rather than
    #: one per-source boolean: ORFE's ingest wants an escaped speaker, and the same
    #: boolean in the predecessor also governs MAE's title, where it mangles
    #: "Winds, Waves, and Wakes".
    escape_fields: tuple[str, ...] = ()
    #: Template for a synthesized title, e.g. "{a_an} {series} Talk by". Empty means fall
    #: straight through to the speaker, then the series.
    title_template: str = ""
    #: Whether a synthesized title may name the speaker. ORFE wants it; a source whose
    #: speaker field is unreliable can turn it off.
    fallback_include_speaker: bool = True
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return self.status == STATUS_LIVE

    @property
    def enrichment_enabled(self) -> bool:
        return bool(self.enrich)

    def fingerprint(self) -> str:
        """SHA-256 over the resolved config.

        Part of the rebuild gate alongside the feed hash, so editing a mapping rule forces
        a rebuild even when upstream has not changed. Keying the gate on the feed hash
        alone -- as the predecessor does -- means config edits can silently never take
        effect.

        ``raw`` holds the config as written, so secret *references* are hashed and secret
        *values* never are. That matters because the fingerprint is published in build
        state: it must change when the configuration changes, not when a credential
        rotates.
        """
        blob = json.dumps(dict(self.raw), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Registry:
    """Every source, resolved."""

    sources: tuple[SourceConfig, ...]

    def __post_init__(self) -> None:
        seen: dict[str, str] = {}
        for source in self.sources:
            if source.feed_url:
                if source.feed_url in seen:
                    # Two sources on one feed is clone-and-retarget in config form: the
                    # same events would publish twice under different source names,
                    # manufacturing the cross-source duplicates this design works to
                    # represent honestly.
                    raise ConfigFatal(
                        f"{source.slug} and {seen[source.feed_url]} share feed_url "
                        f"{source.feed_url}; a derived view belongs in combos, not a "
                        f"second source"
                    )
                seen[source.feed_url] = source.slug

    def by_slug(self, slug: str) -> SourceConfig:
        for source in self.sources:
            if source.slug == slug:
                return source
        raise ConfigFatal(f"unknown source {slug!r}")

    @property
    def live(self) -> tuple[SourceConfig, ...]:
        return tuple(s for s in self.sources if s.is_live)

    @property
    def slugs(self) -> tuple[str, ...]:
        return tuple(s.slug for s in self.sources)

    def fingerprint(self) -> str:
        blob = json.dumps(
            {s.slug: s.fingerprint() for s in self.sources}, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-merge ``overlay`` onto ``base``.

    Objects merge key-wise. Lists replace wholesale: appending an inherited rule chain to a
    source's own would put inherited rules in an arbitrary position, and rule order decides
    which rule wins. ``None`` explicitly unsets an inherited value.
    """
    out = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def parse_header_secret(body: str, *, secret_name: str, where: str) -> tuple[str, str]:
    """Parse a secret whose body is a whole header line, ``Name: value``.

    The secret carries the header *name* as well as its value, so neither appears in this
    repository. That is stricter than holding just the value: the header name
    ``x-wdsoit-bot-bypass`` is itself a detail of someone else's bot-protection
    arrangement, and a config file naming it invites a reader to hardcode a plausible
    value beside it -- which is exactly how the predecessors ended up sending ``1``.

    Accepts ``Name: value`` and ``Name:value``. Splits on the first colon only, since a
    header value may legitimately contain one.
    """
    text = body.strip()
    name, separator, value = text.partition(":")
    name, value = name.strip(), value.strip()
    if not separator or not name or not value:
        raise ConfigFatal(
            f"{where}: secret {secret_name} must hold a whole header line as "
            f'"Name: value" (for example "x-wdsoit-bot-bypass: true"). Got '
            f"{len(text)} character(s) that do not parse as one."
        )
    return name, value


def _resolve_secret_headers(names: Any, env: Mapping[str, str], *, where: str) -> dict[str, str]:
    """Resolve each named secret into one request header.

    A missing secret is an error, never an empty string and never a default. The
    predecessors ship ``os.getenv("BOT_BYPASS_HEADER_VALUE", "1")`` and an inline
    ``|| '1'`` in CI, so when the secret is absent the pipeline sends a placeholder, gets
    403 on every event page, scrapes nothing, and reports success. Failing here is the
    whole point.
    """
    if isinstance(names, str) or not isinstance(names, list | tuple):
        raise ConfigFatal(f"{where} must be a list of secret names, got {names!r}")

    headers: dict[str, str] = {}
    for entry in names:
        if not isinstance(entry, str) or not entry:
            raise ConfigFatal(f"{where}: {entry!r} is not a secret name")
        body = env.get(entry)
        if not body:
            raise ConfigFatal(
                f"{where} needs secret {entry}, which is unset or empty. It has no "
                f"default on purpose: a placeholder value would let every event page "
                f"fetch 403 while the run reported success."
            )
        name, value = parse_header_secret(body, secret_name=entry, where=where)
        headers[name] = value
    return headers


def _env_overrides(slug: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Collect ``UPCOMING_<SLUG>_<KEY>`` overrides for one source.

    Restricted to ``ENV_OVERRIDABLE``: quarantining a broken source or repointing it at a
    fixture are operational actions that should not need a commit. Changing what a field
    *means* is not.
    """
    prefix = f"UPCOMING_{slug.replace('-', '_').upper()}_"
    out: dict[str, Any] = {}
    for name, value in env.items():
        if not name.startswith(prefix):
            continue
        key = name[len(prefix) :].lower()
        if key not in ENV_OVERRIDABLE:
            raise ConfigFatal(
                f"{name} is not an overridable key. Allowed: "
                f"{', '.join(sorted(ENV_OVERRIDABLE))}. Mapping rules and selectors are "
                f"repo config so they stay reviewable and testable."
            )
        if key == "enrich_enabled":
            out[key] = value.strip().lower() in {"1", "true", "yes", "on"}
        else:
            out[key] = value
    return out


def _build_expectations(raw: Mapping[str, Any], slug: str, status: str) -> Expectations:
    exp = dict(raw.get("expectations") or {})
    role = exp.get("summary_role")

    if status == STATUS_UNAVAILABLE:
        # An unavailable source is never fetched, so it has no field semantics to declare.
        role = role or "title"
    if role is None:
        raise ConfigFatal(
            f"{slug} does not declare expectations.summary_role. It has no default: "
            f"SUMMARY carries the speaker on some of these feeds and the talk title on "
            f"others, both satisfy the schema, and guessing produces valid output with "
            f"the two fields transposed and no error anywhere. "
            f"One of: {', '.join(sorted(SUMMARY_ROLES))}."
        )
    if role not in SUMMARY_ROLES:
        raise ConfigFatal(
            f"{slug}: summary_role {role!r} is not one of {', '.join(sorted(SUMMARY_ROLES))}"
        )

    return Expectations(
        summary_role=role,
        min_events=int(exp.get("min_events", 1)),
        allow_empty=bool(exp.get("allow_empty", False)),
        require_nonempty=tuple(exp.get("require_nonempty") or ()),
        min_enrich_success_rate=float(exp.get("min_enrich_success_rate", 0.8)),
        max_placeholder_title_rate=float(exp.get("max_placeholder_title_rate", 1.0)),
        max_location_declined_rate=float(exp.get("max_location_declined_rate", 0.3)),
        max_removed_ratio=float(exp.get("max_removed_ratio", 0.5)),
    )


def _build_http(
    raw: Mapping[str, Any], extra_headers: Mapping[str, str] | None = None
) -> HttpPolicy:
    http = dict(raw.get("http") or {})
    timeout = dict(http.get("timeout") or {})
    headers = dict(http.get("headers") or {})
    # Secret-derived headers are merged last so a config file cannot shadow one with a
    # literal of the same name.
    headers.update(extra_headers or {})
    return HttpPolicy(
        headers=headers,
        connect_timeout=float(timeout.get("connect", 5.0)),
        read_timeout=float(timeout.get("read", 15.0)),
        retries=int(http.get("retries", 2)),
        retry_on=tuple(int(s) for s in (http.get("retry_on") or (429, 500, 502, 503, 504))),
    )


def _build_enrich(raw: Mapping[str, Any], slug: str) -> tuple[EnrichTarget, ...]:
    targets: list[EnrichTarget] = []
    for index, entry in enumerate(raw.get("enrich") or ()):
        if not isinstance(entry, dict):
            raise ConfigFatal(f"{slug}: enrich[{index}] must be a mapping")
        field_name = entry.get("field")
        if not field_name:
            raise ConfigFatal(f"{slug}: enrich[{index}] has no field")
        if field_name in DERIVED_FIELDS:
            raise ConfigFatal(
                f"{slug}: enrich[{index}] targets {field_name!r}, which is derived from "
                f"{DERIVED_FIELDS[field_name]!r} rather than stored. Scraping into the "
                f"scalar would keep one value and drop the rest -- bioengineering lists "
                f"four speakers on one page. Target {DERIVED_FIELDS[field_name]!r}."
            )
        if field_name not in ENRICHABLE_FIELDS:
            raise ConfigFatal(
                f"{slug}: enrich[{index}] targets unknown field {field_name!r}. "
                f"One of: {', '.join(sorted(ENRICHABLE_FIELDS))}."
            )
        selectors = tuple(entry.get("selectors") or ())
        if not selectors:
            raise ConfigFatal(
                f"{slug}: enrich[{index}] ({field_name}) has no selectors. Enrichment is "
                f"opt-in per source precisely so an unverified selector cannot silently "
                f"yield empty fields."
            )
        reject_patterns = []
        for raw_pattern in entry.get("reject") or ():
            try:
                # Case-insensitive always: these match page prose, where the
                # capitalisation is the publisher's and not worth encoding.
                reject_patterns.append(re.compile(str(raw_pattern), re.IGNORECASE))
            except re.error as exc:
                raise ConfigFatal(
                    f"{slug}: enrich[{index}] reject pattern {raw_pattern!r} does not "
                    f"compile: {exc}"
                ) from exc

        provenance = entry.get("provenance")
        if provenance is not None and field_name != "title":
            raise ConfigFatal(
                f"{slug}: enrich[{index}] targets {field_name!r} but claims provenance "
                f"{provenance!r}. Provenance describes the title; stamping it for another "
                f"field would report a still-unknown title as real."
            )
        targets.append(
            EnrichTarget(
                field_name=field_name,
                selectors=selectors,
                mode=entry.get("mode", "fill"),
                provenance=provenance,
                reject_patterns=tuple(reject_patterns),
                plural=bool(entry.get("plural", field_name == "speakers")),
                split_affiliation=bool(entry.get("split_affiliation", False)),
            )
        )
    return tuple(targets)


def _build_source(
    slug: str,
    raw: Mapping[str, Any],
    vocabulary: Vocabulary,
    tag_vocabulary: TagVocabulary,
) -> SourceConfig:
    if not _SLUG_RE.match(slug):
        raise ConfigFatal(f"source slug {slug!r} must be lowercase alphanumeric with hyphens")

    status = str(raw.get("status", STATUS_LIVE))
    if status not in VALID_STATUSES:
        raise ConfigFatal(
            f"{slug}: status {status!r} is not one of {', '.join(sorted(VALID_STATUSES))}"
        )

    feed_url = raw.get("feed_url")
    host = str(raw.get("host", ""))
    if not feed_url and host and raw.get("url_template") and status == STATUS_LIVE:
        feed_url = str(raw["url_template"]).format(host=host)

    reason = str(raw.get("reason", "")).strip()
    if status == STATUS_UNAVAILABLE and not reason:
        raise ConfigFatal(
            f"{slug} is unavailable but records no reason. The reason is the point: it "
            f"stops the next person repeating the investigation."
        )
    if status == STATUS_LIVE and not feed_url:
        raise ConfigFatal(f"{slug} is live but has no feed_url")

    expectations = _build_expectations(raw, slug, status)

    location_rules = tuple(raw.get("location_rules") or ())
    if status == STATUS_LIVE and not location_rules:
        raise ConfigFatal(f"{slug} is live but declares no location_rules")
    if unknown := locate.unknown_rules(location_rules):
        raise ConfigFatal(
            f"{slug} names unknown location rule(s) {list(unknown)}. A typo here fires no "
            f"rule and silently degrades every location. Available: "
            f"{', '.join(sorted(locate.RULES))}."
        )
    # A chain may deliberately omit `whole` -- a source can prefer publishing no location
    # to publishing an unsplit venue -- but `whole` always matches, so listing it early
    # makes every later rule dead.
    if location_rules[:-1].count("whole"):
        raise ConfigFatal(
            f"{slug} lists `whole` before the end of its location chain, so every "
            f"later rule is unreachable: `whole` always matches."
        )
    if "unambiguous_room" in location_rules:
        index = location_rules.index("unambiguous_room")
        misordered = [
            name
            for name in locate.MUST_PRECEDE_UNAMBIGUOUS
            if name in location_rules and location_rules.index(name) > index
        ]
        if misordered:
            raise ConfigFatal(
                f"{slug} lists {misordered} after `unambiguous_room`, which would claim "
                f"their values first. '101 - Sherrerd Hall' has a room token in its "
                f"leading position, so the room rule matches it and leaves "
                f"'- Sherrerd Hall' as the venue."
            )

    summary_rules = None
    if expectations.summary_role == ROLE_RULES:
        summary_rules = load_chain(raw.get("summary") or {}, vocabulary, slug=slug)
    elif raw.get("summary"):
        raise ConfigFatal(
            f"{slug} declares summary rules but its summary_role is "
            f"{expectations.summary_role!r}, so they would never run. Either set the role "
            f"to {ROLE_RULES!r} or remove the rules."
        )

    publish_where = raw.get("publish_where") or None
    publish_unless = raw.get("publish_unless") or None
    for clause, node in (("publish_where", publish_where), ("publish_unless", publish_unless)):
        if node is not None:
            validate_predicate(node, tag_vocabulary, where=f"{slug}.{clause}")

    escape_fields = tuple((raw.get("wire") or {}).get("escape") or ())
    if unknown_escape := set(escape_fields) - serialize.ESCAPABLE_FIELDS:
        raise ConfigFatal(
            f"{slug}: wire.escape names unknown field(s) {sorted(unknown_escape)}. An escaping "
            f"rule on a field that does not exist never applies. Available: "
            f"{', '.join(sorted(serialize.ESCAPABLE_FIELDS))}."
        )

    return SourceConfig(
        slug=slug,
        label=str(raw.get("label", slug)),
        status=status,
        platform=str(raw.get("platform", PLATFORM_SITE_BUILDER)),
        timezone=str(raw.get("timezone", "America/New_York")),
        expectations=expectations,
        feed_url=feed_url,
        host=host,
        cadence=str(raw.get("cadence", "1h")),
        publish_where=publish_where,
        publish_unless=publish_unless,
        location_rules=location_rules,
        enrich=_build_enrich(raw, slug),
        http=_build_http(raw),
        uid_pattern=raw.get("uid_pattern"),
        reason=reason,
        rebuild_after_hours=int(raw.get("rebuild_after_hours", 24)),
        escape_fields=escape_fields,
        summary_rules=summary_rules,
        vocabulary=vocabulary,
        tags=tag_vocabulary,
        title_template=str((raw.get("fallback") or {}).get("title_template", "")),
        fallback_include_speaker=bool((raw.get("fallback") or {}).get("include_speaker", True)),
        raw=raw,
    )


def load_registry(
    path: str | os.PathLike[str] = "config/sources.yaml",
    *,
    env: Mapping[str, str] | None = None,
    patterns: str | os.PathLike[str] = "config/patterns.yaml",
    tags: str | os.PathLike[str] = "config/tags.yaml",
) -> Registry:
    """Load, layer, resolve and validate every source.

    Raises ``ConfigFatal`` on anything wrong rather than defaulting, which is the
    predecessor's central config bug: its loader reads 7 of 12 declared fields and ignores
    the rest without a word, so a knob set in the config file silently does nothing.
    """
    env = os.environ if env is None else env
    vocabulary = load_vocabulary(patterns)
    tag_vocabulary = load_tags(tags)
    config_path = Path(path)
    document = read_mapping(config_path, what="source registry", allow={"defaults", "sources"})

    defaults = document.get("defaults") or {}
    entries = document.get("sources")
    if not entries:
        raise ConfigFatal(f"{config_path} declares no sources")

    known = {"defaults", "sources"}
    if unknown := set(document) - known:
        raise ConfigFatal(f"{config_path}: unknown top-level keys {sorted(unknown)}")

    sources: list[SourceConfig] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigFatal(f"{config_path}: sources[{index}] must be a mapping")
        slug = entry.get("slug")
        if not slug:
            raise ConfigFatal(f"{config_path}: sources[{index}] has no slug")
        if slug in seen:
            raise ConfigFatal(f"{config_path}: duplicate source slug {slug!r}")
        seen.add(slug)

        merged = _merge(defaults, entry)
        merged = _merge(merged, _env_overrides(str(slug), env))
        source = _build_source(str(slug), merged, vocabulary, tag_vocabulary)

        # Resolve secret headers only for sources that actually scrape. The bypass
        # credential is needed for event *pages*, not for the feed -- measured: every ICS
        # endpoint answers a bare request, every event page 403s without the header. So a
        # source with no enrichment must load fine without the credential, while a source
        # that scrapes must refuse to start rather than send a placeholder and report
        # success.
        secret_header_names = (merged.get("http") or {}).get("secret_headers")
        if secret_header_names and source.enrichment_enabled and source.is_live:
            resolved = _resolve_secret_headers(
                secret_header_names, env, where=f"sources.{slug}.http.secret_headers"
            )
            source = replace(source, http=_build_http(merged, resolved))

        sources.append(source)

    return Registry(sources=tuple(sources))
