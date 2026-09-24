"""Publishing: the tree, and the promise that a failure is never silent.

The behaviour under test is not "does it write files" -- it is what the tree looks like when
something goes wrong. A source that fails keeps serving its last good feed, every combined
feed built from that copy says so, and ``status.json`` reports it. The predecessor has no
equivalent: a consumer polling its feed cannot tell fresh from frozen.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from tests.support import FIXTURES, REPO_ROOT
from upcoming.build import BuildResult, build_from_file, load_pronunciation
from upcoming.combine import combine, load_combos
from upcoming.fetch import FetchOutcome, fetch_feed, fetch_feeds
from upcoming.model import Event, from_wire
from upcoming.publish import (
    STATUS_DISABLED,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_OK,
    PublishedFeed,
    assemble,
    due,
    markdown_summary,
    previous_payload,
    status_document,
    utc_now,
    write,
)
from upcoming.serialize import (
    dump_feed,
    escape_ics_text,
    unescape_ics_text,
    wire_format_for,
)
from upcoming.tags import load_tags

FIXED_TIME = "2026-09-11T12:00:00Z"


@pytest.fixture
def built(registry):  # type: ignore[no-untyped-def]
    """Every live source built from its committed fixture, plus every enabled combo."""
    load_pronunciation()
    results = {
        source.slug: build_from_file(FIXTURES / "feeds" / source.slug / "feed.ics", source)
        for source in registry.live
    }
    tags = load_tags(REPO_ROOT / "config" / "tags.yaml")
    combos = load_combos(
        known_sources=[s.slug for s in registry.sources],
        tags=tags,
        purposes=tuple(registry.purposes),
    )
    by_source = {slug: r.events for slug, r in results.items() if r.ok}
    return (
        results,
        {c.name: combine(c, by_source) for c in combos if c.enabled},
        {c.name: c for c in combos},
    )


def build_tree(registry, built, root):  # type: ignore[no-untyped-def]
    results, combos, config = built
    return assemble(registry, results, combos, config, root=root, generated_at=FIXED_TIME)


# --------------------------------------------------------------------------------------
# The healthy tree
# --------------------------------------------------------------------------------------


def test_every_live_source_and_enabled_combo_gets_a_file(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    tree = build_tree(registry, built, tmp_path)
    for source in registry.live:
        assert f"feeds/{source.slug}/events.json" in tree.files
    for name in built[1]:
        assert f"combos/{name}/events.json" in tree.files


def test_an_unavailable_source_is_declared_not_omitted(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """A source we cannot read must appear in status.json with the reason.

    Omitting it would make "we chose not to" and "we forgot" indistinguishable, which is
    the state the predecessor's five unavailable departments are in today.
    """
    tree = build_tree(registry, built, tmp_path)
    records = {f.path: f for f in tree.feeds}
    for source in registry.sources:
        if source.is_live:
            continue
        record = records[f"feeds/{source.slug}/events.json"]
        assert record.status == STATUS_DISABLED
        assert record.detail, f"{source.slug} is disabled with no stated reason"
        # Declared but not written: nothing should be served at a disabled path.
        assert f"feeds/{source.slug}/events.json" not in tree.files


def test_a_disabled_combo_is_declared_too(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    tree = build_tree(registry, built, tmp_path)
    nextg = next(f for f in tree.feeds if f.path == "combos/nextg/events.json")
    assert nextg.status == STATUS_DISABLED
    assert "no feed of its own" in nextg.detail
    assert "combos/nextg/events.json" not in tree.files


def test_an_empty_feed_is_not_a_failure(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """kellercenter serves a well-formed calendar with no events.

    The payload is byte-identical to a broken source's. Only the source's own declaration
    separates them, so the status must too.
    """
    tree = build_tree(registry, built, tmp_path)
    keller = next(f for f in tree.feeds if f.path == "feeds/kellercenter/events.json")
    assert keller.status == STATUS_EMPTY
    assert keller.events == 0
    assert not keller.stale
    assert json.loads(tree.files["feeds/kellercenter/events.json"]) == []


def test_published_feeds_are_valid_json_arrays(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    tree = build_tree(registry, built, tmp_path)
    feeds = [p for p in tree.files if p.startswith(("feeds/", "combos/"))]
    assert len(feeds) == 19  # fourteen live sources, five enabled combos
    for path in feeds:
        assert isinstance(json.loads(tree.files[path]), list), path


# --------------------------------------------------------------------------------------
# Failure: the reason this layer exists
# --------------------------------------------------------------------------------------


def test_a_failed_source_keeps_serving_its_last_good_feed(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    write(build_tree(registry, built, tmp_path), tmp_path)
    good = (tmp_path / "feeds" / "orfe" / "events.json").read_text()

    results, combos, config = built
    broken = dict(results)
    broken["orfe"] = BuildResult("orfe", "failed", diagnostics=("upstream returned 503",))
    tree = assemble(registry, broken, combos, config, root=tmp_path, generated_at=FIXED_TIME)

    record = next(f for f in tree.feeds if f.path == "feeds/orfe/events.json")
    assert record.status == STATUS_FAILED
    assert record.stale is True
    assert "503" in record.detail
    assert record.events == 14
    # Byte-identical: a stale feed is the previous bytes, not a re-render of stale data.
    assert tree.files["feeds/orfe/events.json"] == good


def test_a_source_that_has_never_published_serves_nothing(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """Failing on the first run cannot invent a feed.

    The record must say so rather than reporting zero events, which would look like a
    quiet empty calendar.
    """
    results, combos, config = built
    broken = dict(results)
    broken["orfe"] = BuildResult("orfe", "failed", diagnostics=("upstream returned 503",))
    tree = assemble(registry, broken, combos, config, root=tmp_path, generated_at=FIXED_TIME)

    record = next(f for f in tree.feeds if f.path == "feeds/orfe/events.json")
    assert record.status == STATUS_FAILED
    assert record.stale is False
    assert "Never published" in record.detail
    assert "feeds/orfe/events.json" not in tree.files


def test_one_failed_source_does_not_block_the_others(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    results, combos, config = built
    broken = dict(results)
    broken["orfe"] = BuildResult("orfe", "failed", diagnostics=("upstream returned 503",))
    tree = assemble(registry, broken, combos, config, root=tmp_path, generated_at=FIXED_TIME)

    healthy = [f for f in tree.feeds if f.path.startswith("feeds/") and f.status == STATUS_OK]
    assert len(healthy) == 12  # the thirteen non-empty live sources, less orfe
    assert tree.files["feeds/mae/events.json"]


def test_a_combo_built_from_a_stale_input_says_which_input(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """The staleness has to propagate, and it has to name the source.

    A consumer of ``combos/all`` cannot see the per-source feeds' status from their own
    payload; if the combo did not carry the reason, a partially stale union would be
    indistinguishable from a current one.
    """
    write(build_tree(registry, built, tmp_path), tmp_path)
    results, combos, config = built
    broken = dict(results)
    broken["orfe"] = BuildResult("orfe", "failed", diagnostics=("upstream returned 503",))
    tree = assemble(registry, broken, combos, config, root=tmp_path, generated_at=FIXED_TIME)

    records = {f.path: f for f in tree.feeds}
    union = records["combos/all/events.json"]
    assert union.stale is True
    assert union.detail == "built from the last good copy of orfe"
    # engineering does not include orfe, so it must NOT be marked stale.
    assert records["combos/engineering/events.json"].stale is False


def test_a_combos_staleness_comes_from_its_own_inputs_only(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """A regression guard.

    An earlier version scanned sibling records for staleness and picked up other combos'
    input lists, reporting ``orfe, orfe, orfe`` on a feed with one stale input. Combos are
    assembled in order, so a combo must read the per-source pass, never its siblings.
    """
    write(build_tree(registry, built, tmp_path), tmp_path)
    results, combos, config = built
    broken = dict(results)
    broken["orfe"] = BuildResult("orfe", "failed", diagnostics=("boom",))
    tree = assemble(registry, broken, combos, config, root=tmp_path, generated_at=FIXED_TIME)

    for record in tree.feeds:
        if record.path.startswith("combos/") and record.stale:
            assert record.detail.count("orfe") == 1, record.detail


def test_a_published_feed_reads_back_as_the_events_that_produced_it(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """The round trip that stops a failed source from silently shrinking every combo.

    Re-rendering the restored events must reproduce the published bytes exactly. That is
    the invariant rather than event equality, because ``collapse_fields`` is applied on
    write and is not reversible -- it is, however, idempotent, so a second pass is a
    fixed point, which is precisely what this asserts.
    """
    tree = build_tree(registry, built, tmp_path)
    write(tree, tmp_path)
    source = next(s for s in registry.live if s.slug == "orfe")
    fmt = wire_format_for(source.escape_fields)

    payload = previous_payload(tmp_path, "feeds/orfe/events.json", fmt)
    assert payload is not None
    restored = tuple(from_wire(record) for record in payload)
    assert len(restored) == 14
    assert dump_feed(restored, fmt) == tree.files["feeds/orfe/events.json"]


def test_reading_a_feed_back_reverses_the_sources_own_escaping(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """Escaping is a property of the format, so it must not survive into the model.

    orfe escapes commas in ``speaker`` and ``content``. If a restored event kept the
    backslashes, a combined feed built from orfe's last good copy would hold escaped
    lookalikes -- which compare as different from a live build, suppressing a merge and
    counting as a divergence instead.
    """
    tree = build_tree(registry, built, tmp_path)
    write(tree, tmp_path)
    source = next(s for s in registry.live if s.slug == "orfe")

    published = previous_payload(tmp_path, "feeds/orfe/events.json")
    assert published is not None
    assert published[0]["speaker"] == "Elynn Chen\\, New York University"
    assert "\\," in published[0]["content"]

    restored = previous_payload(
        tmp_path, "feeds/orfe/events.json", wire_format_for(source.escape_fields)
    )
    assert restored is not None
    assert restored[0]["speaker"] == "Elynn Chen, New York University"
    assert "\\," not in restored[0]["content"]
    assert from_wire(restored[0]).speakers[0].name == "Elynn Chen"


# --------------------------------------------------------------------------------------
# status.json
# --------------------------------------------------------------------------------------


def test_status_counts_every_category(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    tree = build_tree(registry, built, tmp_path)
    document = json.loads(tree.files["status.json"])
    assert document["generatedAt"] == FIXED_TIME
    summary = document["summary"]
    assert summary["ok"] == 18  # 13 non-empty sources + 5 enabled combos
    assert summary["empty"] == 1
    assert summary["disabled"] == 6  # 5 unavailable sources + nextg's combo
    assert summary["stale"] == 0
    # `events` counts sources only: summing the combos too would double-count every event.
    assert summary["events"] == 143


def test_status_is_the_only_file_carrying_a_clock(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """Feeds must be byte-stable across runs, or drift detection is impossible.

    A timestamp in a feed would make every run differ from the last, so "did anything
    change" could never be answered by comparing bytes.
    """
    first = build_tree(registry, built, tmp_path)
    second = assemble(*_args(registry, built), root=tmp_path, generated_at="2027-01-01T00:00:00Z")
    for path, body in first.files.items():
        if path == "status.json":
            assert body != second.files[path]
        else:
            assert body == second.files[path], path


def _args(registry, built):  # type: ignore[no-untyped-def]
    results, combos, config = built
    return registry, results, combos, config


def test_status_lists_feeds_in_a_stable_order(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    document = json.loads(build_tree(registry, built, tmp_path).files["status.json"])
    paths = [f["path"] for f in document["feeds"]]
    assert paths == sorted(paths)


def test_status_omits_absent_fields_rather_than_writing_falsey_ones(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """``stale: false`` on every healthy feed would bury the handful that matter."""
    document = json.loads(build_tree(registry, built, tmp_path).files["status.json"])
    for record in document["feeds"]:
        assert "stale" not in record
        assert record.get("detail") != ""


def test_status_records_a_merge_on_the_combo_that_made_it(tmp_path):  # type: ignore[no-untyped-def]
    """Nothing in today's real data merges, so the note is proven on constructed input."""
    from upcoming.combine import Combo, combine

    shared = {
        "platform": "princeton-site-builder",
        "starts_at": "2026-10-02T16:15:00Z",
        "ends_at": "2026-10-02T17:15:00Z",
        "start_time": "2026-10-02T12:15:00",
        "end_time": "2026-10-02T13:15:00",
        "timezone": "America/New_York",
        "title": "Joint Colloquium",
        "url": "https://example.edu/events/joint",
    }
    twins = {
        "orfe": (Event(id="orfe:1", guid="1", sources=("orfe",), **shared),),
        "citp": (Event(id="citp:2", guid="2", sources=("citp",), **shared),),
    }
    combo = Combo(name="pair", label="Pair", include=("orfe", "citp"), key=("starts_at", "title"))
    result = combine(combo, twins)
    assert result.merged == 1
    assert result.events[0].sources == ("citp", "orfe")

    tree = assemble(
        _EmptyRegistry(),
        {},
        {"pair": result},
        {"pair": combo},
        root=tmp_path,
        generated_at=FIXED_TIME,
    )
    record = next(f for f in tree.feeds if f.path == "combos/pair/events.json")
    assert record.notes and "merged into one record" in record.notes[0]


class _EmptyRegistry:
    """A registry with no sources, for testing the combo half of assemble in isolation."""

    sources: tuple[()] = ()
    purposes: ClassVar[dict[str, object]] = {}
    templates: ClassVar[dict[str, object]] = {}


# --------------------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------------------


def test_write_creates_the_tree_and_reports_what_it_wrote(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    tree = build_tree(registry, built, tmp_path)
    written = write(tree, tmp_path)
    assert written == sorted(tree.files)
    for path in written:
        assert (tmp_path / path).is_file()


def test_write_is_idempotent(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    tree = build_tree(registry, built, tmp_path)
    write(tree, tmp_path)
    before = {p: (tmp_path / p).read_bytes() for p in tree.files}
    write(tree, tmp_path)
    assert {p: (tmp_path / p).read_bytes() for p in tree.files} == before


def test_previous_payload_declines_a_corrupt_file(tmp_path):  # type: ignore[no-untyped-def]
    """A truncated publish must not be mistaken for an empty feed.

    Returning ``[]`` here would make the diff gate see a total wipe, and returning a
    partial parse would seed a combo with half a department.
    """
    target = tmp_path / "feeds" / "orfe" / "events.json"
    target.parent.mkdir(parents=True)
    target.write_text('[{"id": "orfe:1"')
    assert previous_payload(tmp_path, "feeds/orfe/events.json") is None
    target.write_text('{"not": "an array"}')
    assert previous_payload(tmp_path, "feeds/orfe/events.json") is None
    assert previous_payload(tmp_path, "feeds/nothing/events.json") is None


def test_utc_now_is_a_zulu_second_stamp():  # type: ignore[no-untyped-def]
    stamp = utc_now()
    assert stamp.endswith("Z")
    assert len(stamp) == 20


def test_status_document_is_ascii_and_newline_terminated(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    body = status_document(build_tree(registry, built, tmp_path), generated_at=FIXED_TIME)
    assert body.endswith("\n")
    body.encode("ascii")


# --------------------------------------------------------------------------------------
# The escaping inverse
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "Elynn Chen, New York University",
        "Winds, Waves, and Wakes",
        "a; b, c",
        "nothing to escape",
        "",
    ],
)
def test_unescaping_inverts_escaping(value: str) -> None:
    assert unescape_ics_text(escape_ics_text(value)) == value


def test_escaping_is_idempotent_so_a_republish_does_not_compound_it() -> None:
    """A stale feed is republished from bytes that were already escaped once.

    If escaping compounded, a source failing for a week would accumulate a backslash a day.
    """
    once = escape_ics_text("A, B")
    assert escape_ics_text(once) == once


# --------------------------------------------------------------------------------------
# Fetching the ICS
# --------------------------------------------------------------------------------------


@dataclass
class _FeedTransport:
    """Answers each source's feed_url from the committed fixtures, or fails on demand."""

    fail: Mapping[str, FetchOutcome] = field(default_factory=dict)
    by_url: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, Mapping[str, str]]] = field(default_factory=list)

    def __call__(self, url, *, headers, timeout):  # type: ignore[no-untyped-def]
        self.calls.append((url, dict(headers)))
        if url in self.fail:
            return self.fail[url]
        return FetchOutcome("ok", url, code=200, body=self.by_url[url])


def _feed_transport(registry, **fail):  # type: ignore[no-untyped-def]
    bodies = {
        s.feed_url: (FIXTURES / "feeds" / s.slug / "feed.ics").read_text(encoding="utf-8")
        for s in registry.live
    }
    failures = {
        next(s.feed_url for s in registry.live if s.slug == slug): outcome
        for slug, outcome in fail.items()
    }
    return _FeedTransport(fail=failures, by_url=bodies)


def test_fetch_feeds_writes_each_source_to_its_own_path(registry, tmp_path):  # type: ignore[no-untyped-def]
    outcomes = fetch_feeds(registry.live, tmp_path, _feed_transport(registry))
    assert set(outcomes) == {s.slug for s in registry.live}
    for source in registry.live:
        assert (tmp_path / source.slug / "feed.ics").is_file()
        assert outcomes[source.slug].ok


def test_fetch_uses_the_sources_own_headers(registry, tmp_path):  # type: ignore[no-untyped-def]
    """The bypass header is per-source, resolved from the secret, and must reach the wire.

    The predecessor's pipeline 403'd on every page while reporting success because the
    credential never arrived. Asserting the header is sent is the cheap half of not
    repeating that.
    """
    transport = _feed_transport(registry)
    fetch_feeds(registry.live, tmp_path, transport)
    orfe = next(s for s in registry.live if s.slug == "orfe")
    sent = dict(next(headers for url, headers in transport.calls if url == orfe.feed_url))
    assert sent == dict(orfe.http.headers)


def test_a_failed_fetch_writes_nothing_and_leaves_the_previous_capture(registry, tmp_path):  # type: ignore[no-untyped-def]
    """Neither a partial body nor a deletion. A blip must not become data loss.

    The build layer already knows how to serve a source's last good feed; overwriting or
    removing the capture here would take that option away.
    """
    fetch_feeds(registry.live, tmp_path, _feed_transport(registry))
    before = (tmp_path / "orfe" / "feed.ics").read_bytes()

    outcomes = fetch_feeds(
        registry.live,
        tmp_path,
        _feed_transport(registry, orfe=FetchOutcome("http_error", "u", code=503, error="HTTP 503")),
    )
    assert outcomes["orfe"].failed
    assert (tmp_path / "orfe" / "feed.ics").read_bytes() == before


def test_fetch_reports_a_missing_feed_url_as_configuration_not_network(registry):  # type: ignore[no-untyped-def]
    """The two have different fixes, so they must not read alike."""
    unavailable = next(s for s in registry.sources if not s.is_live and not s.feed_url)
    outcome = fetch_feed(unavailable, _FeedTransport())
    assert outcome.failed
    assert "declares no feed_url" in outcome.error


# --------------------------------------------------------------------------------------
# The run summary
# --------------------------------------------------------------------------------------


def test_the_summary_names_every_feed_and_its_state(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    tree = build_tree(registry, built, tmp_path)
    body = markdown_summary(tree, generated_at=FIXED_TIME)
    assert FIXED_TIME in body
    for record in tree.feeds:
        assert f"`{record.path}`" in body


def test_the_summary_makes_a_stale_feed_impossible_to_skim_past(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """A stale source among sixteen healthy rows is exactly what a reader misses."""
    write(build_tree(registry, built, tmp_path), tmp_path)
    results, combos, config = built
    broken = dict(results)
    broken["orfe"] = BuildResult("orfe", "failed", diagnostics=("upstream returned 503",))
    tree = assemble(registry, broken, combos, config, root=tmp_path, generated_at=FIXED_TIME)

    body = markdown_summary(tree, generated_at=FIXED_TIME)
    assert "**5 stale**" in body
    assert "| `feeds/orfe/events.json` | **stale** |" in body
    assert "upstream returned 503" in body


def test_the_summary_cell_survives_a_reason_containing_a_pipe(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """An unescaped pipe silently splits a row into extra columns."""
    tree = build_tree(registry, built, tmp_path)
    tree.feeds.append(
        PublishedFeed(path="feeds/x/events.json", status="failed", events=0, detail="a | b\nc")
    )
    row = next(
        line
        for line in markdown_summary(tree, generated_at=FIXED_TIME).splitlines()
        if "feeds/x" in line
    )
    # Only unescaped pipes delimit, so counting those is what proves the row keeps its
    # four columns. The escaped one is content.
    assert len(re.findall(r"(?<!\\)\|", row)) == 5
    assert "\\|" in row
    assert "\n" not in row


def test_the_summary_truncates_a_long_reason_rather_than_wrapping(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """The disabled sources carry paragraph-long reasons; a table must stay readable."""
    tree = build_tree(registry, built, tmp_path)
    for line in markdown_summary(tree, generated_at=FIXED_TIME).splitlines():
        assert len(line) < 400


# --------------------------------------------------------------------------------------
# lastSuccessAt, which turns staleness from a yes/no into a duration
# --------------------------------------------------------------------------------------


def test_every_feed_record_carries_its_units_own_name(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """Published because a consumer composing a listing has to credit the publisher.

    Nothing turns the slug `citp` into "Center for Information Technology Policy" but the
    registry, and the newsletter's editors write exactly that string by hand today.
    """
    document = json.loads(build_tree(registry, built, tmp_path).files["status.json"])
    by_path = {f["path"]: f for f in document["feeds"]}
    for source in registry.sources:
        record = by_path[f"feeds/{source.slug}/events.json"]
        assert record["label"] == source.label, source.slug
    assert by_path["feeds/citp/events.json"]["label"] == "Center for Information Technology Policy"
    # A combined feed is not one unit's, so it carries no label rather than a misleading one.
    assert "label" not in by_path["combos/all/events.json"]


def test_an_unavailable_feed_is_still_named(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """So a consumer can say what is missing, not just that something is."""
    document = json.loads(build_tree(registry, built, tmp_path).files["status.json"])
    cs = next(f for f in document["feeds"] if f["path"] == "feeds/cs/events.json")
    assert cs["status"] == "disabled"
    assert cs["label"]


def test_a_successful_feed_records_when_it_was_built(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    document = json.loads(build_tree(registry, built, tmp_path).files["status.json"])
    for record in document["feeds"]:
        if record["status"] in {"ok", "empty"}:
            assert record["lastSuccessAt"] == FIXED_TIME


def test_the_stamp_survives_a_failure(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """Without carrying it forward, every run of an outage looks like its first.

    Erasing it on the first failure would leave nothing to measure, and the watchdog could
    never tell a twenty-minute blip from a week.
    """
    write(build_tree(registry, built, tmp_path), tmp_path)
    results, combos, config = built
    broken = dict(results)
    broken["orfe"] = BuildResult("orfe", "failed", diagnostics=("boom",))
    later = "2026-09-12T09:00:00Z"
    tree = assemble(registry, broken, combos, config, root=tmp_path, generated_at=later)

    document = json.loads(tree.files["status.json"])
    orfe = next(f for f in document["feeds"] if f["path"] == "feeds/orfe/events.json")
    assert document["generatedAt"] == later
    assert orfe["lastSuccessAt"] == FIXED_TIME  # the earlier run, not this one


def test_a_combo_reports_its_oldest_input_not_its_own_build_time(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """A union built from a week-old copy of one department is a week old.

    Stamping it with the run time would report it as fresh, which is the lie the whole
    staleness mechanism exists to prevent.
    """
    write(build_tree(registry, built, tmp_path), tmp_path)
    results, combos, config = built
    broken = dict(results)
    broken["orfe"] = BuildResult("orfe", "failed", diagnostics=("boom",))
    tree = assemble(
        registry, broken, combos, config, root=tmp_path, generated_at="2026-09-12T09:00:00Z"
    )

    records = {f.path: f for f in tree.feeds}
    assert records["combos/all/events.json"].last_success == FIXED_TIME
    # engineering excludes orfe, so every one of its inputs is current.
    assert records["combos/engineering/events.json"].last_success == "2026-09-12T09:00:00Z"


def test_a_corrupt_previous_status_does_not_stop_a_publish(registry, built, tmp_path):  # type: ignore[no-untyped-def]
    """Losing the stamps is a degradation; refusing to publish would be an outage."""
    (tmp_path / "status.json").write_text("{ truncated")
    tree = build_tree(registry, built, tmp_path)
    assert json.loads(tree.files["status.json"])["feeds"]


# --------------------------------------------------------------------------------------
# The declared cadence, which nothing read until now
# --------------------------------------------------------------------------------------


def test_a_source_never_published_is_always_due(registry):  # type: ignore[no-untyped-def]
    orfe = next(s for s in registry.live if s.slug == "orfe")
    is_due, why = due(orfe, {}, now=datetime(2026, 9, 11, 12, tzinfo=UTC))
    assert is_due
    assert "never published" in why


def test_a_source_fetched_within_its_cadence_is_not_due(registry):  # type: ignore[no-untyped-def]
    """`cee` published one event this term; asking it 72 times a day is not politeness."""
    cee = next(s for s in registry.live if s.slug == "cee")
    assert cee.cadence_minutes == 360
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    was = {"feeds/cee/events.json": {"lastSuccessAt": "2026-09-11T10:00:00Z"}}
    is_due, why = due(cee, was, now=now)
    assert is_due is False
    assert "cadence is 360m" in why


def test_a_source_past_its_cadence_is_due(registry):  # type: ignore[no-untyped-def]
    cee = next(s for s in registry.live if s.slug == "cee")
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    was = {"feeds/cee/events.json": {"lastSuccessAt": "2026-09-11T05:00:00Z"}}
    assert due(cee, was, now=now)[0] is True


def test_the_slack_stops_a_cadence_slipping_a_whole_tick(registry):  # type: ignore[no-untyped-def]
    """A 60-minute cadence checked every 20 minutes must not become 80.

    The tick at 60 minutes lands a few seconds early, so a strict comparison defers to the
    next one — and every source with an hourly cadence would quietly run at 80 minutes.
    """
    mae = next(s for s in registry.live if s.slug == "mae")
    assert mae.cadence_minutes == 60
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    was = {"feeds/mae/events.json": {"lastSuccessAt": "2026-09-11T11:00:02Z"}}
    assert due(mae, was, now=now)[0] is True


@pytest.mark.parametrize("stamp_value", ["", "not a time", None])
def test_an_unreadable_stamp_means_fetch_rather_than_skip(registry, stamp_value):  # type: ignore[no-untyped-def]
    """Never let missing data park a source indefinitely."""
    cee = next(s for s in registry.live if s.slug == "cee")
    record: dict = {} if stamp_value is None else {"lastSuccessAt": stamp_value}
    assert due(cee, {"feeds/cee/events.json": record}, now=datetime(2026, 9, 11, tzinfo=UTC))[0]


def test_a_future_stamp_means_fetch_rather_than_trust_it(registry):  # type: ignore[no-untyped-def]
    cee = next(s for s in registry.live if s.slug == "cee")
    was = {"feeds/cee/events.json": {"lastSuccessAt": "2027-01-01T00:00:00Z"}}
    is_due, why = due(cee, was, now=datetime(2026, 9, 11, tzinfo=UTC))
    assert is_due is True
    assert "future" in why


def test_every_live_source_declares_a_cadence_that_parses(registry):  # type: ignore[no-untyped-def]
    """A typo falls back to an hour, so a silent fallback must not be hiding in config."""
    from upcoming.registry import parse_cadence

    for source in registry.live:
        assert parse_cadence(source.cadence, default=-1) > 0, source.slug
