"""The newsletter simulator: its JavaScript, and what it predicts for a real edition.

The edition logic is JavaScript by decision, so it is exercised through Node rather than
left unchecked. `site/simulator.js` exports its pure functions under `module.exports` for
exactly this, and guards the DOM wiring behind a `typeof document` check so requiring it
outside a browser does not crash.

The test that matters most is the last one. The simulator exists to predict what a
newsletter edition would contain, and the only way to know whether it does is to point it
at an edition that already went out --
`tests/fixtures/newsletter/2026-09-08-edition.json`, transcribed from the Mailchimp export
of the real 7-14 September 2026 issue, with every field verified against that file.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.support import FIXTURES, REPO_ROOT
from upcoming.build import build_from_file, load_pronunciation
from upcoming.publish import assemble

SITE = REPO_ROOT / "site"
SIMULATOR_JS = SITE / "simulator.js"
EDITION = json.loads(
    (FIXTURES / "newsletter" / "2026-09-08-edition.json").read_text(encoding="utf-8")
)

#: Node is required, not optional. A test that skips itself when a tool is missing reports
#: green while checking nothing, which is the failure this repository is built around --
#: so the workflow installs Node and this raises rather than skips.
NODE = shutil.which("node")


def node(script: str, *, cwd: Path = REPO_ROOT) -> dict:
    """Run a snippet against the simulator's exports and return what it prints as JSON."""
    assert NODE, (
        "node is not on PATH. The simulator's logic is JavaScript, so Node is a test "
        "dependency rather than a convenience; the tests workflow installs it."
    )
    body = textwrap.dedent(script)
    result = subprocess.run(
        [NODE, "-e", f'const S = require("./site/simulator.js");\n{body}'],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def tree(tmp_path_factory):  # type: ignore[no-untyped-def]
    """A published tree built from the committed fixtures, for Node to read."""
    from tests.support import TEST_ENV
    from upcoming.registry import load_registry

    load_pronunciation()
    registry = load_registry(REPO_ROOT / "config" / "sources.yaml", env=TEST_ENV)
    results = {
        s.slug: build_from_file(FIXTURES / "feeds" / s.slug / "feed.ics", s) for s in registry.live
    }
    root = tmp_path_factory.mktemp("tree")
    built = assemble(registry, results, {}, {}, root=root, generated_at="2026-09-11T12:00:00Z")
    for path, body in built.files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


# --------------------------------------------------------------------------------------
# The file itself
# --------------------------------------------------------------------------------------


def test_the_javascript_parses() -> None:
    assert NODE, "node is a test dependency; the workflow installs it"
    result = subprocess.run(
        [NODE, "--check", str(SIMULATOR_JS)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_it_loads_outside_a_browser() -> None:
    """The DOM wiring is guarded, which is what lets every test below exist at all."""
    assert node("console.log(JSON.stringify(Object.keys(S).length > 10));") is True


def test_no_source_is_named_in_the_javascript(registry) -> None:  # type: ignore[no-untyped-def]
    """Feed discovery must never quietly become a list written into the page.

    The two predecessor repositories ship byte-identical simulators with the department
    baked in; that is how one file came to be maintained twice. Slugs are allowed in
    comments, where they are illustration rather than configuration.
    """
    code = "\n".join(
        line
        for line in SIMULATOR_JS.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith(("*", "/*", "//"))
    )
    named = [s.slug for s in registry.sources if f'"{s.slug}"' in code or f"'{s.slug}'" in code]
    assert named == []


# --------------------------------------------------------------------------------------
# Dates, which are where a calendar tool goes wrong quietly
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("date", "monday"),
    [
        ("2026-09-07", "2026-09-07"),  # a Monday is its own week start
        ("2026-09-08", "2026-09-07"),
        ("2026-09-13", "2026-09-07"),  # Sunday belongs to the week that began
        ("2026-09-14", "2026-09-14"),
        ("2026-03-08", "2026-03-02"),  # the US DST transition
        ("2026-11-01", "2026-10-26"),  # and back again
        ("2027-01-01", "2026-12-28"),  # across a year boundary
    ],
)
def test_the_week_anchor_is_monday(date: str, monday: str) -> None:
    """Sunday is the case that catches a naive implementation, and DST the one that
    catches doing this arithmetic on local-midnight Date objects."""
    assert node(f'console.log(JSON.stringify(S.weekStartFor("{date}")));') == monday


def test_date_arithmetic_survives_a_dst_boundary() -> None:
    """`addDays` works in UTC precisely so 23- and 25-hour local days cannot shift a date."""
    assert node('console.log(JSON.stringify(S.addDays("2026-03-07", 1)));') == "2026-03-08"
    assert node('console.log(JSON.stringify(S.addDays("2026-11-01", -1)));') == "2026-10-31"


def test_the_deadline_is_the_tuesday_before_the_week() -> None:
    assert node('console.log(JSON.stringify(S.defaultDeadlineDate("2026-09-07")));') == "2026-09-01"
    # A shifted publication still derives from the week, not from the publication date.
    assert node('console.log(JSON.stringify(S.defaultDeadlineDate("2026-09-08")));') == "2026-09-01"


def test_the_clock_is_written_the_way_the_newsletter_writes_it() -> None:
    cases = node("""
        console.log(JSON.stringify([
          S.prettyClock("2026-09-08T12:15:00"),
          S.prettyClock("2026-09-08T16:30:00"),
          S.prettyClock("2026-09-09T10:00:00"),
          S.prettyClock("2026-09-09T00:30:00"),
          S.prettyClock("2026-09-09T12:00:00")
        ]));
    """)
    assert cases == ["12:15 p.m.", "4:30 p.m.", "10:00 a.m.", "12:30 a.m.", "12:00 p.m."]


# --------------------------------------------------------------------------------------
# The edition
# --------------------------------------------------------------------------------------


def test_an_unshifted_edition_covers_its_whole_week() -> None:
    edition = node('console.log(JSON.stringify(S.resolveEdition({publicationDate:"2026-09-07"})));')
    assert edition["coverageStart"] == "2026-09-07T00:00:00"
    assert edition["coverageEnd"] == "2026-09-13T23:59:59"
    assert edition["shifted"] is False


def test_a_shifted_edition_loses_the_days_before_publication() -> None:
    """Coverage start follows the publication date while the end stays pinned to the week.

    That mixed anchoring is what the real 7 September edition needs: publication moved to
    the Tuesday for Labor Day, and the edition covers Tuesday to Sunday rather than running
    a day past the week it belongs to.
    """
    edition = node('console.log(JSON.stringify(S.resolveEdition({publicationDate:"2026-09-08"})));')
    assert edition["id"] == "2026-09-07"
    assert edition["coverageStart"] == "2026-09-08T00:00:00"
    assert edition["coverageEnd"] == "2026-09-13T23:59:59"
    assert edition["shifted"] is True


def test_the_window_is_inclusive_at_both_ends() -> None:
    checks = node("""
        const e = S.resolveEdition({publicationDate:"2026-09-07"});
        console.log(JSON.stringify([
          S.inWindow("2026-09-07T00:00:00", e),
          S.inWindow("2026-09-13T23:59:59", e),
          S.inWindow("2026-09-06T23:59:59", e),
          S.inWindow("2026-09-14T00:00:00", e),
          S.inWindow("not a stamp", e)
        ]));
    """)
    assert checks == [True, True, False, False, False]


def test_a_missing_publication_date_is_refused_not_guessed() -> None:
    assert (
        node("""
        try { S.resolveEdition({}); console.log(JSON.stringify(null)); }
        catch (e) { console.log(JSON.stringify(e.message)); }
    """)
        == "Pick a publication date."
    )


def test_the_phase_reads_from_the_deadline_and_publication() -> None:
    phases = node("""
        const e = S.resolveEdition({publicationDate:"2026-09-07"});
        console.log(JSON.stringify([
          S.phaseAt("2026-08-25T09:00:00", e),
          S.phaseAt("2026-09-01T12:00:00", e),
          S.phaseAt("2026-09-07T12:00:00", e),
          S.phaseAt("", e)
        ]));
    """)
    assert phases == ["open", "closed", "published", None]


# --------------------------------------------------------------------------------------
# Rendering the fields the editors write
# --------------------------------------------------------------------------------------


def test_a_room_is_only_called_a_room_when_it_looks_like_one() -> None:
    """ "Sherrerd Hall, Room 306" is what the newsletter says. A detail that is not a room
    number is joined plainly rather than mislabelled."""
    rendered = node("""
        const L = (name, detail) => S.locationText({location: {name, detail}});
        console.log(JSON.stringify([
          L("Sherrerd Hall", "306"),
          L("Bowen Hall", "222"),
          L("SEAS-CBE", "F204"),
          L("Maeder Hall", "Auditorium"),
          L("Friend Center", ""),
          L("", "101")
        ]));
    """)
    assert rendered == [
        "Sherrerd Hall, Room 306",
        "Bowen Hall, Room 222",
        "SEAS-CBE, Room F204",
        "Maeder Hall, Auditorium",
        "Friend Center",
        "101",
    ]


def test_the_field_label_pluralises_like_the_editors_do() -> None:
    """Their own file writes `Speaker:` and `Speakers:`, `Sponsor:` and `Sponsors:`."""
    assert node("""
        console.log(JSON.stringify([
          S.plural("Speaker", ["a"]), S.plural("Speaker", ["a", "b"]),
          S.plural("Sponsor", ["a"]), S.plural("Sponsor", ["a", "b", "c"])
        ]));
    """) == ["Speaker:", "Speakers:", "Sponsor:", "Sponsors:"]


def test_only_days_with_events_get_a_heading() -> None:
    """The real edition carries Tuesday and Wednesday and nothing else, though its window
    runs to Sunday."""
    days = node("""
        const at = (t) => ({startTime: t, title: t, placeholder: false, names: []});
        console.log(JSON.stringify(S.groupByDay([
          at("2026-09-08T12:15:00"), at("2026-09-08T16:30:00"), at("2026-09-10T15:00:00")
        ]).map((g) => [g.day, g.items.length])));
    """)
    assert days == [["2026-09-08", 2], ["2026-09-10", 1]]


def test_a_repeat_is_matched_on_the_speaker_when_the_title_is_synthesized() -> None:
    """Two units listing one talk synthesize *different* placeholder titles from their own
    templates, so comparing titles finds no duplicate where there plainly is one.

    Found by pointing the simulator at a real week: `ai` and `materials` both list Rafael
    Gomez-Bombarelli at 12:05, and the editors merged them into a single entry with three
    sponsors.
    """
    keys = node("""
        const mk = (title, placeholder, name) => S.collisionKey({
          startTime: "2026-09-09T12:05:00", title, placeholder, names: name ? [name] : []
        });
        const who = "Rafael Gomez-Bombarelli";
        console.log(JSON.stringify({
          synthesized:
            mk("A PMI/PCCM Seminar Series Talk by " + who, true, who)
            === mk("An AI for Accelerating Invention Talk by " + who, true, who),
          real: mk("The Bittersweet Lesson", false, "X") === mk("Something Else", false, "X")
        }));
    """)
    assert keys["synthesized"] is True, "the same talk must collide"
    assert keys["real"] is False, "two real titles at one time are two talks"


# --------------------------------------------------------------------------------------
# Against the edition that actually went out
# --------------------------------------------------------------------------------------


def edition_result(tree: Path, publication: str, purpose: str | None = None) -> dict:
    """What the simulator produces for one edition over the whole published tree."""
    purpose_arg = f'"{purpose}"' if purpose else "null"
    return node(f"""
        const fs = require("fs");
        const root = {json.dumps(str(tree))};
        const manifest = JSON.parse(fs.readFileSync(root + "/status.json"));
        const feeds = {{}};
        manifest.feeds.filter((f) => f.path.indexOf("feeds/") === 0)
          .forEach((f) => {{ feeds[f.path.split("/")[1]] = f; }});
        let events = [];
        Object.keys(feeds).forEach((slug) => {{
          if (feeds[slug].status === "disabled") return;
          const file = root + "/feeds/" + slug + "/events.json";
          events = events.concat(JSON.parse(fs.readFileSync(file)));
        }});
        const edition = S.resolveEdition({{publicationDate: "{publication}"}});
        const r = S.partition(events, edition, {purpose_arg}, feeds);
        console.log(JSON.stringify({{
          edition: edition,
          collisions: r.collisions,
          placeholders: r.placeholders,
          included: r.included.map((i) => ({{
            day: S.prettyDate(i.startTime.slice(0, 10)),
            time: S.prettyClock(i.startTime),
            title: i.title,
            series: i.series,
            location: i.location,
            sponsors: i.sponsors,
            speakers: i.speakers,
            sources: i.sources,
            placeholder: i.placeholder
          }}))
        }}));
    """)


@pytest.fixture(scope="module")
def real_edition(tree):  # type: ignore[no-untyped-def]
    return edition_result(tree, "2026-09-08", "engineering-newsletter")


def test_the_simulated_window_matches_the_real_edition(real_edition) -> None:  # type: ignore[no-untyped-def]
    """Publication on the Tuesday, covering Tuesday through Sunday."""
    assert real_edition["edition"]["coverageStart"] == "2026-09-08T00:00:00"
    assert real_edition["edition"]["coverageEnd"] == "2026-09-13T23:59:59"


@pytest.mark.parametrize(
    "expected",
    [e for e in EDITION["events"] if e["expect"]["inFixtures"]],
    ids=lambda e: e["expect"]["source"],
)
def test_each_event_the_fixtures_hold_is_predicted(real_edition, expected) -> None:  # type: ignore[no-untyped-def]
    """Three of the real edition's seven events are in the committed feed snapshot.

    The other four are stated in the fixture with the reason: three are ORFE events absent
    from its snapshot, and one is published by a unit we have no feed for at all.
    """
    slug = expected["expect"]["source"]
    ours = [e for e in real_edition["included"] if slug in e["sources"]]
    assert ours, f"{slug} produced nothing for this edition"

    match = [e for e in ours if e["day"] == expected["day"] and e["time"] == expected["time"]]
    assert match, (
        f"nothing from {slug} at {expected['day']} {expected['time']}; "
        f"got {[(e['day'], e['time']) for e in ours]}"
    )
    got = match[0]
    # The sponsor is the one field the editors write by hand that we can now generate.
    assert expected["sponsors"][0] in got["sponsors"]


def test_the_sponsor_of_every_event_is_its_feeds_declared_name(real_edition, tree) -> None:  # type: ignore[no-untyped-def]
    """Sponsor is derived, not transcribed. Deriving it from the slug is impossible --
    nothing turns `citp` into "Center for Information Technology Policy" but the registry."""
    manifest = json.loads((tree / "status.json").read_text(encoding="utf-8"))
    labels = {
        f["path"].split("/")[1]: f.get("label")
        for f in manifest["feeds"]
        if f["path"].startswith("feeds/")
    }
    assert labels["citp"] == "Center for Information Technology Policy"
    for event in real_edition["included"]:
        assert event["sponsors"] == [labels[s] for s in event["sources"]]


def test_the_talk_two_units_both_list_is_counted_as_a_repeat(real_edition) -> None:
    """The editors merged it into one entry with three sponsors; our feeds publish it
    twice, because a per-source feed reproduces its own upstream. The simulator has to say
    so rather than leaving an editor to notice."""
    assert real_edition["collisions"] >= 1
    pair = [e for e in real_edition["included"] if e["time"] == "12:05 p.m."]
    assert {s for e in pair for s in e["sources"]} == {"ai", "materials"}


def test_a_title_still_awaiting_announcement_is_flagged(real_edition) -> None:
    """An editor has to replace it, so passing it through unmarked is the one unhelpful
    thing the export could do."""
    assert real_edition["placeholders"] >= 1
    for event in real_edition["included"]:
        if event["placeholder"]:
            assert " Talk by " in event["title"] or event["title"]


def test_the_unshifted_monday_does_not_reproduce_the_edition(tree) -> None:
    """The documented consequence of implementing the weekly rule only.

    Publishing on Monday the 7th includes a day the real edition does not cover, which is
    why the page tells you to override the publication date for a holiday week.
    """
    shifted = edition_result(tree, "2026-09-08", "engineering-newsletter")
    unshifted = edition_result(tree, "2026-09-07", "engineering-newsletter")
    assert unshifted["edition"]["coverageStart"] == "2026-09-07T00:00:00"
    assert len(unshifted["included"]) >= len(shifted["included"])


def test_a_purpose_nothing_declares_yields_nothing(tree) -> None:
    """An empty result with a reason, never a silent full listing."""
    result = edition_result(tree, "2026-09-08", "no-such-purpose")
    assert result["included"] == []


def test_dropping_the_purpose_filter_can_only_widen_the_edition(tree) -> None:
    with_purpose = edition_result(tree, "2026-09-08", "engineering-newsletter")
    without = edition_result(tree, "2026-09-08", None)
    assert len(without["included"]) >= len(with_purpose["included"])


# --------------------------------------------------------------------------------------
# The listing an editor actually pastes
# --------------------------------------------------------------------------------------

#: A document just rich enough for `buildListing`. It takes its document as a parameter
#: precisely so the export -- the part that leaves this repository and goes into a
#: newsletter -- is not the one thing nothing checks.
DOC_STUB = """
const doc = { createElement: (tag) => ({
  tagName: tag, className: "", textContent: "", children: [],
  appendChild(c) { this.children.push(c); return c; }
}) };
const flat = (n, out) => {
  out.push({tag: n.tagName, cls: n.className, text: n.textContent});
  (n.children || []).forEach((c) => flat(c, out));
  return out;
};
"""


def listing(tree: Path, publication: str, purpose: str | None = None) -> list[dict]:
    purpose_arg = f'"{purpose}"' if purpose else "null"
    return node(f"""
        const fs = require("fs");
        {DOC_STUB}
        const root = {json.dumps(str(tree))};
        const manifest = JSON.parse(fs.readFileSync(root + "/status.json"));
        const feeds = {{}};
        manifest.feeds.filter((f) => f.path.indexOf("feeds/") === 0)
          .forEach((f) => {{ feeds[f.path.split("/")[1]] = f; }});
        let events = [];
        Object.keys(feeds).forEach((slug) => {{
          if (feeds[slug].status === "disabled") return;
          const file = root + "/feeds/" + slug + "/events.json";
          events = events.concat(JSON.parse(fs.readFileSync(file)));
        }});
        const edition = S.resolveEdition({{publicationDate: "{publication}"}});
        const r = S.partition(events, edition, {purpose_arg}, feeds);
        console.log(JSON.stringify(flat(S.buildListing(edition, r.included, doc), [])));
    """)


@pytest.fixture(scope="module")
def real_listing(tree):  # type: ignore[no-untyped-def]
    return listing(tree, "2026-09-08", "engineering-newsletter")


def test_the_listing_is_headed_with_the_coverage_range(real_listing) -> None:  # type: ignore[no-untyped-def]
    head = next(n for n in real_listing if n["tag"] == "h2")
    assert head["text"] == "Events Newsletter: September 8 - September 13, 2026"


def test_the_listing_groups_by_day_and_skips_empty_ones(real_listing, real_edition) -> None:  # type: ignore[no-untyped-def]
    """The real edition's window runs to Sunday but carries only the days with events."""
    days = [n["text"] for n in real_listing if n["cls"] == "day"]
    assert days == sorted(set(days), key=days.index), "days must not repeat"
    assert len(days) == len({e["day"] for e in real_edition["included"]})
    for day in days:
        assert day.split(",")[0] in {
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        }


def test_every_event_becomes_one_block(real_listing, real_edition) -> None:  # type: ignore[no-untyped-def]
    blocks = [n for n in real_listing if n["cls"] == "ev"]
    assert len(blocks) == len(real_edition["included"])


def test_the_listing_uses_the_editors_own_field_labels(real_listing) -> None:  # type: ignore[no-untyped-def]
    """Their file writes these exactly, colon included, so a paste needs no re-labelling."""
    labels = {n["text"] for n in real_listing if n["tag"] == "dt"}
    assert "Sponsor:" in labels
    assert labels <= {"Speaker:", "Speakers:", "Sponsor:", "Sponsors:", "Series:", "Location:"}


def test_a_placeholder_title_is_marked_in_the_listing(real_listing) -> None:  # type: ignore[no-untyped-def]
    """An editor has to replace it. Passing it through unmarked is the one unhelpful
    thing the export could do, so it is the one thing asserted about it."""
    flags = [n for n in real_listing if n["cls"] == "placeholder-flag"]
    assert flags
    assert "replace before sending" in flags[0]["text"]


def test_a_comma_joined_series_is_spaced_for_reading(real_listing) -> None:
    """Presentation, not a change to what the feed says: the feed publishes "A,B" because
    that is the upstream spelling, and a human composing a listing should not have to."""
    values = [n["text"] for n in real_listing if n["tag"] == "dd"]
    assert not [v for v in values if re.search(r",\S", v)], "a comma with no space after it"


def test_an_empty_edition_says_so_rather_than_rendering_nothing(tree) -> None:  # type: ignore[no-untyped-def]
    nodes = listing(tree, "2026-09-08", "no-such-purpose")
    texts = [n["text"] for n in nodes]
    assert "No events fall in this edition." in texts
    assert not [n for n in nodes if n["cls"] == "ev"]
