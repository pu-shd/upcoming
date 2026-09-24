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


#: Reading the manifest the way the page does, including the schedule.
#:
#: The schedule comes from `status.json`, never from a literal here, because that is the
#: claim being tested: a publication's weekday, hour and coverage window are configuration
#: the page is handed, not something either the page or this file knows.
MANIFEST_STUB = """
const fs = require("fs");
const manifest = JSON.parse(fs.readFileSync(root + "/status.json"));
const feeds = {};
manifest.feeds.filter((f) => f.path.indexOf("feeds/") === 0)
  .forEach((f) => { feeds[f.path.split("/")[1]] = f; });
let events = [];
Object.keys(feeds).forEach((slug) => {
  if (feeds[slug].status === "disabled") return;
  events = events.concat(JSON.parse(fs.readFileSync(root + "/feeds/" + slug + "/events.json")));
});
const declared = (manifest.purposes || {})[purpose] || {};
const edition = S.resolveEdition({publicationDate: publication, schedule: declared.schedule});
"""


def edition_result(tree: Path, publication: str, purpose: str | None = None) -> dict:
    """What the simulator produces for one edition over the whole published tree."""
    purpose_arg = f'"{purpose}"' if purpose else "null"
    return node(f"""
        const root = {json.dumps(str(tree))};
        const publication = "{publication}";
        const purpose = {purpose_arg};
        {MANIFEST_STUB}
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
            sponsors: i.sponsors.map(function (x) {{ return x.label; }}),
            sponsorLinks: i.sponsors.map(function (x) {{ return x.home; }}),
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
    """Four of the real edition's seven events are in the committed feed snapshot.

    The other three are ORFE events absent from its snapshot, stated in the fixture with
    that reason. The fourth used to be there too, for a different reason -- "AI Resource
    Workshop" was published by a unit we had no feed for at all. Registering `dais` closed
    that gap, which is the only kind of progress this fixture can record.
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
    feeds = {
        f["path"].split("/")[1]: f for f in manifest["feeds"] if f["path"].startswith("feeds/")
    }
    assert feeds["citp"]["label"] == "Center for Information Technology Policy"
    for event in real_edition["included"]:
        assert event["sponsors"] == [feeds[s]["label"] for s in event["sources"]]


def test_each_sponsor_links_to_its_own_unit(real_edition, tree) -> None:  # type: ignore[no-untyped-def]
    """Taken from the feed, not from the event's URL.

    A talk two units both list carries one URL and needs two links, so a sponsor's site
    cannot be derived from the event it appears on.
    """
    manifest = json.loads((tree / "status.json").read_text(encoding="utf-8"))
    homes = {
        f["path"].split("/")[1]: f.get("home")
        for f in manifest["feeds"]
        if f["path"].startswith("feeds/")
    }
    assert homes["citp"] == "https://citp.princeton.edu"
    for event in real_edition["included"]:
        assert event["sponsorLinks"] == [homes[s] for s in event["sources"]]
        assert all(link.startswith("https://") for link in event["sponsorLinks"])


def test_the_talk_several_units_all_list_is_counted_as_a_repeat(real_edition) -> None:
    """The editors merged it into one entry with three sponsors; our feeds publish it
    once per unit, because a per-source feed reproduces its own upstream. The simulator
    has to say so rather than leaving an editor to notice.

    Three units publish this talk and only two are paired, which is a real limit rather
    than a tuning problem. `ai` and `materials` both synthesize a placeholder title and
    both name the speaker, so they key on the speaker and match. `dais` carries the real
    title -- "The Bittersweet Lesson of Scaling in AI for Materials" -- and **no speaker
    at all**, so it shares no field with them: a title key misses it and a speaker key
    misses it. Their locations disagree too ("Bowen Hall 222" against "Bowen").

    Matching it would mean fuzzy entity resolution across sources, which this project
    refuses on principle -- it never merges and never guesses. The note is a convenience
    for an editor, not a guarantee, and the third row is visible in the table regardless.
    """
    assert real_edition["collisions"] >= 1
    group = [e for e in real_edition["included"] if e["time"] == "12:05 p.m."]
    assert {s for e in group for s in e["sources"]} == {"ai", "dais", "materials"}, (
        "all three are in the edition; only two of them are paired as a repeat"
    )


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

#: One DOM for every test here. An earlier second stub could not set an attribute, so it
#: broke the moment the listing grew links -- two shims is the same duplication this page
#: exists to argue against.
DOC_STUB = """
const D = require("./tests/fixtures/newsletter/tiny-dom.js");
const doc = D.document;
const flat = (n, out) => {
  out.push({
    tag: (n.tagName || "").toLowerCase(), cls: n.className, text: n.textContent,
    href: n.getAttribute ? n.getAttribute("href") : null
  });
  (n.childNodes || []).forEach((c) => flat(c, out));
  return out;
};
"""


def listing(tree: Path, publication: str, purpose: str | None = None) -> list[dict]:
    """The rendered listing, with the template taken from the manifest like the schedule."""
    purpose_arg = f'"{purpose}"' if purpose else "null"
    return node(f"""
        {DOC_STUB}
        const root = {json.dumps(str(tree))};
        const publication = "{publication}";
        const purpose = {purpose_arg};
        {MANIFEST_STUB}
        const r = S.partition(events, edition, {purpose_arg}, feeds);
        const tree_ = S.buildListing(edition, r.included, doc, declared.template);
        console.log(JSON.stringify(flat(tree_, [])));
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


# --------------------------------------------------------------------------------------
# The export buttons, which shipped broken
# --------------------------------------------------------------------------------------

#: A DOM that models the one thing the bug turned on: appendChild and replaceChildren
#: *move* a node rather than copying it. The earlier stub could not reparent anything, so
#: it could not have caught this -- see tests/fixtures/newsletter/tiny-dom.js.
TINY_DOM = 'const D = require("./tests/fixtures/newsletter/tiny-dom.js");'

RENDER = (
    TINY_DOM
    + """
const edition = S.resolveEdition({publicationDate: "2026-09-08"});
const feeds = {citp: {
  label: "Center for Information Technology Policy",
  home: "https://citp.princeton.edu"
}};
const items = [S.decorate({
  startTime: "2026-09-08T12:15:00", title: "AI Agents and the Augmentation Agenda",
  sources: ["citp"], series: "CITP Seminars",
  urlRef: "https://citp.princeton.edu/events/2026/ai-agents",
  speakers: [{name: "Arvind Narayanan", affiliation: "Princeton University"}],
  location: {name: "Sherrerd Hall", detail: "306"}
}, feeds)];

// Exactly what renderExport does: build a tree, then hand its children to the page.
const listing = S.buildListing(edition, items, D.document);
const exportEl = D.makeElement("div");
exportEl.replaceChildren.apply(exportEl, listing.childNodes.slice());
"""
)


def test_handing_the_listing_to_the_page_empties_the_tree_it_came_from() -> None:
    """The mechanism behind the bug, pinned so the fix cannot be undone by accident.

    `replaceChildren` moves nodes. Anything that keeps a second reference to the built
    tree and reads from it afterwards gets an empty document -- which is what both export
    buttons did: a `<title>Events</title>` and nothing else.
    """
    result = node(
        RENDER
        + """
        console.log(JSON.stringify({
          movedFrom: listing.innerHTML.length,
          arrivedAt: exportEl.innerHTML.length,
          headingGone: listing.querySelector("h2") === null,
          headingHere: exportEl.querySelector("h2") !== null
        }));
    """
    )
    assert result["movedFrom"] == 0, "the source tree is emptied -- this is the trap"
    assert result["arrivedAt"] > 0
    assert result["headingGone"] is True
    assert result["headingHere"] is True


def test_the_download_carries_the_listing_and_not_just_a_title() -> None:
    """The exact failure reported: a file containing a doctype, a charset and nothing."""
    doc = node(RENDER + "console.log(JSON.stringify(S.exportDocument(exportEl)));")
    assert doc.startswith("<!doctype html>")
    assert "<title>Events Newsletter: September 8 - September 13, 2026</title>" in doc
    assert "AI Agents and the Augmentation Agenda" in doc
    assert "Center for Information Technology Policy" in doc
    assert "Sherrerd Hall, Room 306" in doc
    # The title must come from the listing, never the fallback, when a heading exists.
    assert "<title>Events</title>" not in doc


def test_the_download_is_read_from_the_element_the_reader_can_see() -> None:
    """One tree, in the page. What is shown is what is exported, so they cannot diverge."""
    doc = node(
        RENDER
        + """
        console.log(JSON.stringify({
          fromPage: S.exportDocument(exportEl).length,
          fromTheEmptiedTree: S.exportDocument(listing).length
        }));
    """
    )
    assert doc["fromPage"] > 200
    assert doc["fromTheEmptiedTree"] == 0


def test_nothing_is_downloaded_when_there_is_nothing_to_download() -> None:
    """An empty string, so the handler returns before offering an empty file."""
    assert (
        node(
            TINY_DOM
            + """
        console.log(JSON.stringify(S.exportDocument(D.makeElement("div"))));
    """
        )
        == ""
    )
    assert node("console.log(JSON.stringify(S.exportDocument(null)));") == ""


def test_the_copy_payload_is_the_same_content_as_the_download() -> None:
    """Copy reads `innerHTML` and `innerText` off the same element, so a fix to one path
    cannot leave the other silently empty -- which is how this went unnoticed at all."""
    payload = node(
        RENDER
        + """
        console.log(JSON.stringify({
          html: exportEl.innerHTML,
          text: exportEl.innerText
        }));
    """
    )
    assert "AI Agents and the Augmentation Agenda" in payload["html"]
    assert "AI Agents and the Augmentation Agenda" in payload["text"]
    assert "Sponsor:" in payload["text"]


def test_both_export_buttons_read_from_the_element_on_the_page() -> None:
    """The guard that actually catches this bug, and the only one that does.

    Everything above tests the payload builders against an element a test constructs. The
    defect was never in those -- it was in the *wiring*, in a click handler that read from
    a variable instead of the page, and a handler inside `start()` is not reachable from
    here. So the wiring is checked structurally: duplicate state was the bug, and the
    durable fix is that there is none.

    Verified by reintroducing the holding variable, which fails this and nothing else.
    """
    code = (SITE / "simulator.js").read_text(encoding="utf-8")
    assert "lastListing" not in code, "a second copy of the listing is the trap itself"
    assert "exportDocument(exportEl, selectedTemplate())" in code, (
        "download must read from the page, in the style the page is showing"
    )
    assert "exportEl.innerHTML" in code, "copy must read from the page"
    assert "exportEl.innerText" in code


# --------------------------------------------------------------------------------------
# Styling that survives leaving this site
# --------------------------------------------------------------------------------------


def test_the_download_carries_a_stylesheet_for_reading_it() -> None:
    """So the file renders as a listing when opened, not as unstyled text."""
    doc = node(RENDER + "console.log(JSON.stringify(S.exportDocument(exportEl)));")
    assert "<style>" in doc
    assert "h3.day {" in doc
    assert "</style>" in doc


def test_every_element_also_carries_the_style_inline() -> None:
    """The stylesheet alone is not enough for the job this export exists for.

    Mailchimp and the clients it sends to strip `<style>` and honour only `style=""`, so
    anything that has to survive the paste is applied per element.
    """
    doc = node(RENDER + "console.log(JSON.stringify(S.exportDocument(exportEl)));")
    unstyled = re.findall(r"<(h2|h3|dl|dt|dd|p|span|div)(?![^>]*style=)[^>]*>", doc)
    assert unstyled == [], f"tags with no inline style: {unstyled}"


def test_the_inline_rules_and_the_stylesheet_are_the_same_rules() -> None:
    """One definition. Two renderings of it that disagreed would be worse than either."""
    both = node("""
        const sheet = {};
        Object.keys(S.EXPORT_STYLES).forEach(function (name) {
          sheet[name] = S.exportStylesheet(name);
        });
        console.log(JSON.stringify({sheet: sheet, styles: S.EXPORT_STYLES}));
    """)
    for template, rules in both["styles"].items():
        for selector, declarations in rules.items():
            assert f"{selector} {{ {declarations} }}" in both["sheet"][template], template


def test_the_export_styling_is_email_safe() -> None:
    """No grid, no flex, no custom properties.

    Grid and flex are unsupported across most email clients, and a custom property
    resolves to nothing once the file leaves this site, where `--dim` is defined. A `dl`
    losing its grid falls back to label-then-value on separate lines, which is how the
    editors' own edition already reads.
    """
    styles = node("console.log(JSON.stringify(S.EXPORT_STYLES));")
    assert len(styles) >= 2, "both layouts, or this checks half of what ships"
    for template, rules in styles.items():
        declarations = " ".join(rules.values())
        for banned in ("grid", "flex", "var(--", "currentColor"):
            assert banned not in declarations, (
                f"{template}: {banned} does not survive an email client"
            )


def test_the_page_itself_is_not_restyled_by_the_export() -> None:
    """The preview keeps using the site's stylesheet; only the exported copy is weighed
    down. A rewrite of the string rather than a mutation of the DOM is what keeps that
    true, and it is why the preview still follows the reader's dark or light theme."""
    result = node(
        RENDER
        + """
        const before = exportEl.innerHTML;
        S.exportDocument(exportEl);
        console.log(JSON.stringify({unchanged: exportEl.innerHTML === before,
                                    hadStyleAttr: before.indexOf('style="') !== -1}));
    """
    )
    assert result["unchanged"] is True
    assert result["hadStyleAttr"] is False


def test_the_copied_markup_is_styled_too() -> None:
    """Copy is the Mailchimp path, so it is the one that most needs the inline rules."""
    code = (SITE / "simulator.js").read_text(encoding="utf-8")
    assert "inlineStyles(exportEl.innerHTML, selectedTemplate())" in code


def test_the_downloaded_file_is_readable_rather_than_one_long_line() -> None:
    """Someone will open it in an editor; a single 8kB line helps nobody."""
    doc = node(RENDER + "console.log(JSON.stringify(S.exportDocument(exportEl)));")
    lines = doc.splitlines()
    assert len(lines) > 8
    assert max(len(line) for line in lines) < 3000


def test_text_from_a_feed_is_escaped_in_the_export() -> None:
    """Titles and series come from someone else's calendar and reach the markup as text."""
    doc = node(
        TINY_DOM
        + """
        const edition = S.resolveEdition({publicationDate: "2026-09-08"});
        const items = [S.decorate({
          startTime: "2026-09-08T12:15:00",
          title: "Tags <script>alert(1)</script> & ampersands",
          sources: ["x"], location: {name: "A", detail: "1"}
        }, {x: {label: "Unit & Co"}})];
        const listing = S.buildListing(edition, items, D.document);
        const el = D.makeElement("div");
        el.replaceChildren.apply(el, listing.childNodes.slice());
        console.log(JSON.stringify(S.exportDocument(el)));
    """
    )
    assert "<script>" not in doc
    assert "&lt;script&gt;" in doc
    assert "Unit &amp; Co" in doc


# --------------------------------------------------------------------------------------
# Links in the generated listing
# --------------------------------------------------------------------------------------


def test_every_event_title_links_to_its_own_page(real_listing) -> None:  # type: ignore[no-untyped-def]
    """Attaching these by hand is the part of composing an edition that takes the time."""
    links = [n for n in real_listing if n["cls"] == "ev-link"]
    titles = [n for n in real_listing if n["cls"] == "ev-title"]
    assert len(links) == len(titles)
    for link in links:
        assert link["href"].startswith("https://")
        assert link["text"]


def test_every_sponsor_links_to_its_unit_not_to_the_event(real_listing) -> None:  # type: ignore[no-untyped-def]
    """A talk two units both list carries one URL and needs two links, so a sponsor's
    site comes from its feed rather than from the event it appears on."""
    sponsors = [n for n in real_listing if n["cls"] == "sponsor-link"]
    assert sponsors
    events = {n["href"] for n in real_listing if n["cls"] == "ev-link"}
    for sponsor in sponsors:
        assert sponsor["href"].startswith("https://")
        # A unit's home, not a deep link into one of its events.
        assert sponsor["href"].count("/") == 2, sponsor["href"]
        assert sponsor["href"] not in events


def test_a_merged_event_credits_each_sponsor_separately() -> None:
    """The editors merged one talk into a single entry with three sponsors. Whenever we
    have several, each gets its own link rather than one link over the joined string."""
    nodes = node(
        DOC_STUB
        + """
        const edition = S.resolveEdition({publicationDate: "2026-09-08"});
        const feeds = {
          ai: {label: "Princeton AI Lab", home: "https://ai.princeton.edu"},
          materials: {label: "Princeton Materials Institute",
                      home: "https://materials.princeton.edu"}
        };
        const items = [S.decorate({
          startTime: "2026-09-09T12:05:00", title: "One Talk",
          sources: ["ai", "materials"], urlRef: "https://ai.princeton.edu/events/1",
          location: {name: "Bowen Hall", detail: "222"}
        }, feeds)];
        console.log(JSON.stringify(flat(S.buildListing(edition, items, doc), [])));
    """
    )
    sponsors = [n for n in nodes if n["cls"] == "sponsor-link"]
    assert [n["href"] for n in sponsors] == [
        "https://ai.princeton.edu",
        "https://materials.princeton.edu",
    ]
    labels = [n["text"] for n in nodes if n["cls"] == "ev-label"]
    assert "Sponsors:" in labels, "the label pluralises when there are several"


def test_an_event_with_no_url_still_renders_its_title() -> None:
    """A missing URL is a feed's problem, not a reason to drop the title."""
    nodes = node(
        DOC_STUB
        + """
        const edition = S.resolveEdition({publicationDate: "2026-09-08"});
        const items = [S.decorate({
          startTime: "2026-09-08T12:15:00", title: "No Link Here", sources: ["x"],
          location: {name: "A", detail: "1"}
        }, {x: {label: "Unit"}})];
        console.log(JSON.stringify(flat(S.buildListing(edition, items, doc), [])));
    """
    )
    assert not [n for n in nodes if n["cls"] == "ev-link"]
    title = next(n for n in nodes if n["cls"] == "ev-title")
    assert title["text"] == "No Link Here"


def test_a_sponsor_with_no_site_is_named_without_a_link() -> None:
    nodes = node(
        DOC_STUB
        + """
        const edition = S.resolveEdition({publicationDate: "2026-09-08"});
        const items = [S.decorate({
          startTime: "2026-09-08T12:15:00", title: "T", sources: ["x"],
          location: {name: "A", detail: "1"}
        }, {x: {label: "Unit With No Site"}})];
        console.log(JSON.stringify(flat(S.buildListing(edition, items, doc), [])));
    """
    )
    assert not [n for n in nodes if n["cls"] == "sponsor-link"]
    assert "Unit With No Site" in [n["text"] for n in nodes]


def test_the_links_survive_into_the_exported_document() -> None:
    """The export is a string rewrite, so a link that renders on the page but is lost on
    the way out would be the easy failure here."""
    doc = node(RENDER + "console.log(JSON.stringify(S.exportDocument(exportEl)));")
    assert 'class="ev-link"' in doc
    assert 'href="https://citp.princeton.edu' in doc
    assert 'class="sponsor-link"' in doc
    # And they are styled inline, like everything else that has to survive a paste.
    assert re.search(r'<a class="ev-link"[^>]*style="', doc)
    assert re.search(r'<a class="sponsor-link"[^>]*style="', doc)


def test_a_url_from_a_feed_is_escaped_into_the_href() -> None:
    """The URL comes from someone else's calendar and lands in an attribute."""
    doc = node(
        DOC_STUB
        + """
        const edition = S.resolveEdition({publicationDate: "2026-09-08"});
        const items = [S.decorate({
          startTime: "2026-09-08T12:15:00", title: "T", sources: ["x"],
          urlRef: 'https://x.edu/e?a=1&b=2"><script>alert(1)</script>',
          location: {name: "A", detail: "1"}
        }, {x: {label: "U", home: "https://x.edu"}})];
        const listing = S.buildListing(edition, items, doc);
        const el = D.makeElement("div");
        el.replaceChildren.apply(el, listing.childNodes.slice());
        console.log(JSON.stringify(S.exportDocument(el)));
    """
    )
    assert "<script>" not in doc
    assert "&quot;" in doc or "&lt;script&gt;" in doc


# --------------------------------------------------------------------------------------
# The deadline, as something you can put in a calendar
# --------------------------------------------------------------------------------------

EDITION_JS = 'const e = S.resolveEdition({publicationDate: "2026-09-14"});'


@pytest.mark.parametrize(
    ("wall", "utc"),
    [
        ("2026-09-01T12:00:00", "20260901T160000Z"),  # EDT, four hours
        ("2026-12-01T12:00:00", "20261201T170000Z"),  # EST, five
        ("2026-03-07T12:00:00", "20260307T170000Z"),  # the day before spring forward
        ("2026-03-09T12:00:00", "20260309T160000Z"),  # and the day after
        ("2026-11-02T12:00:00", "20261102T170000Z"),  # the day after falling back
    ],
)
def test_the_deadline_resolves_to_the_right_instant(wall: str, utc: str) -> None:
    """The deadline is Eastern wall time and a calendar entry needs an instant.

    Four hours in September, five in December -- hardcoding either is wrong for half the
    year, and an hour out on a deadline is the kind of error nobody notices until it
    matters.
    """
    assert (
        node(f"""
        console.log(JSON.stringify(
          S.utcStamp(S.zonedToUTC("{wall}", "America/New_York"))
        ));
    """)
        == utc
    )


def test_the_reminder_says_enough_to_be_useful_later() -> None:
    """A bare "deadline" in a calendar a fortnight from now tells nobody anything."""
    event = node(EDITION_JS + "console.log(JSON.stringify(S.deadlineEvent(e, 7)));")
    assert event["title"] == "Newsletter submissions close"
    assert "publishing Monday, September 14" in event["details"]
    assert "covers Monday, September 14 through Sunday, September 20" in event["details"]
    assert "7 event(s)" in event["details"]


def test_the_reminder_does_not_end_a_sentence_in_two_full_stops() -> None:
    """A time ends in "p.m." and the sentence would read "12:00 p.m..".

    The same slip had to be fixed on the page itself, which is why it is a helper now
    rather than care taken twice.
    """
    details = node(EDITION_JS + "console.log(JSON.stringify(S.deadlineEvent(e, 7).details));")
    assert ".." not in details
    assert node('console.log(JSON.stringify(S.sentence("ends at 12:00 p.m.")));') == (
        "ends at 12:00 p.m."
    )
    assert node('console.log(JSON.stringify(S.sentence("no stop yet")));') == "no stop yet."


def test_the_reminder_is_not_a_zero_length_event() -> None:
    """Several clients render one oddly or drop it from an agenda view."""
    span = node(
        EDITION_JS
        + """
        const ev = S.deadlineEvent(e, 7);
        console.log(JSON.stringify((ev.end - ev.start) / 60000));
    """
    )
    assert span == 30


# --------------------------------------------------------------------------------------
# The calendar file, which leaves this site
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ics() -> str:
    return node(EDITION_JS + 'console.log(JSON.stringify(S.deadlineIcs(e, 7, "https://x/y")));')


def test_the_calendar_file_is_a_well_formed_vcalendar(ics: str) -> None:
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.endswith("END:VCALENDAR\r\n")
    for required in ("VERSION:2.0", "PRODID:", "BEGIN:VEVENT", "UID:", "DTSTAMP:", "END:VEVENT"):
        assert required in ics, required


def test_every_line_ends_crlf(ics: str) -> None:
    """RFC 5545 says CRLF, and a bare LF is the classic file some clients refuse."""
    assert "\n" in ics
    for line in ics.split("\r\n"):
        assert "\n" not in line


def test_no_line_exceeds_seventy_five_octets(ics: str) -> None:
    """Folded per the spec. The description is long enough to need it, which is the point
    of testing with a real one rather than a short stub."""
    lines = ics.split("\r\n")
    assert max(len(line.encode("utf-8")) for line in lines) <= 75
    assert [line for line in lines if line.startswith(" ")], "expected a folded line"


def test_the_times_are_the_deadline_not_the_publication(ics: str) -> None:
    """Publication is Monday the 14th; submissions close the Tuesday before."""
    assert "DTSTART:20260908T160000Z" in ics
    assert "DTEND:20260908T163000Z" in ics


def test_adding_the_same_deadline_twice_updates_one_entry(ics: str) -> None:
    """A random UID would leave a duplicate behind every time somebody clicked."""
    assert "UID:newsletter-deadline-2026-09-14@" in ics
    again = node(EDITION_JS + 'console.log(JSON.stringify(S.deadlineIcs(e, 7, "https://x/y")));')
    assert again == ics


def test_text_is_escaped_per_rfc_5545() -> None:
    """Commas and semicolons separate values in this format; an unescaped one splits the
    field and the entry lands mangled or is rejected."""
    assert node(r'console.log(JSON.stringify(S.icsText("a,b;c\\d\ne")));') == ("a\\,b\\;c\\\\d\\ne")
    # Backslash first, or the escaping would escape its own output.
    assert node(r'console.log(JSON.stringify(S.icsText("\\")));') == "\\\\"


def test_the_reminder_fires_before_the_deadline_not_after(ics: str) -> None:
    assert "BEGIN:VALARM" in ics
    assert "TRIGGER:-PT24H" in ics


def test_the_google_link_carries_the_same_instant_and_text() -> None:
    """Two routes to one reminder; they must not disagree about when it is."""
    url = node(
        EDITION_JS + 'console.log(JSON.stringify(S.googleCalendarUrl(e, 7, "https://x/y")));'
    )
    assert url.startswith("https://calendar.google.com/calendar/render?")
    assert "action=TEMPLATE" in url
    assert "dates=20260908T160000Z%2F20260908T163000Z" in url
    assert "Newsletter+submissions+close" in url


def test_the_link_back_is_included_so_the_reminder_can_be_acted_on(ics: str) -> None:
    """Somebody opening this a week later wants the edition it came from."""
    assert "URL:https://x/y" in ics
    assert "https://x/y" in ics.replace("\r\n ", "")


# --------------------------------------------------------------------------------------
# How ready an event is to go into an edition
# --------------------------------------------------------------------------------------

GRADE = """
const grade = (o) => S.readiness(S.decorate(
  Object.assign({startTime: "2026-09-08T12:00:00", sources: ["x"]}, o),
  {x: {label: "A Unit", home: "https://x.edu"}}
));
"""


def test_a_complete_event_is_ready() -> None:
    result = node(
        GRADE
        + """
        console.log(JSON.stringify(grade({
          title: "A Real Title", speakers: [{name: "A", affiliation: "B"}],
          series: "A Series", content: "An abstract.",
          location: {name: "Somewhere", detail: "101"}
        })));
    """
    )
    assert result["state"] == "ready"
    assert result["label"] == "ready"
    assert result["reasons"] == []
    assert result["gaps"] == []


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (
            {
                "title": "A Talk by X",
                "titleIsPlaceholder": True,
                "location": {"name": "L", "detail": "1"},
            },
            "no title announced yet",
        ),
        ({"title": "Real"}, "no location"),
    ],
)
def test_what_an_editor_must_fix_first(event: dict, reason: str) -> None:
    """Not sendable as it stands: nothing to call it, or nowhere to say it is."""
    result = node(GRADE + f"console.log(JSON.stringify(grade({json.dumps(event)})));")
    assert result["state"] == "fix"
    assert result["label"] == "fix first"
    assert reason in result["reasons"]


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        ({"title": "T", "cancelled": True, "location": {"name": "L"}}, "cancelled"),
        (
            {"title": "T", "mappingConflict": "x", "location": {"name": "L"}},
            "the mapping conflicted",
        ),
        (
            {"title": "T", "unmappedTags": ["Some Series"], "location": {"name": "L"}},
            "a category nothing recognises: Some Series",
        ),
    ],
)
def test_what_needs_a_human_to_look(event: dict, reason: str) -> None:
    """Rare, and exactly when somebody needs telling. All three sit at zero in today's
    data, which is why they are graded rather than left to be noticed."""
    result = node(GRADE + f"console.log(JSON.stringify(grade({json.dumps(event)})));")
    assert result["state"] == "check"
    assert reason in result["reasons"]


def test_a_missing_speaker_is_a_gap_not_a_blocker() -> None:
    """Two events in five have none, and most legitimately so — a reading group has no
    one speaker. Grading on it would put two fifths of an edition in amber, and a signal
    that fires that often is one everybody learns to scroll past.
    """
    result = node(
        GRADE
        + """
        console.log(JSON.stringify(grade({
          title: "Bias in AI Reading Group", series: "A Series",
          content: "x", location: {name: "Sherrerd Hall", detail: "008"}
        })));
    """
    )
    assert result["state"] == "ready"
    assert result["gaps"] == ["no speaker"]


def test_something_wrong_outranks_something_missing() -> None:
    """A cancelled event with no title is a cancellation first."""
    result = node(
        GRADE
        + """
        console.log(JSON.stringify(grade({
          title: "A Talk by X", titleIsPlaceholder: true, cancelled: true
        })));
    """
    )
    assert result["state"] == "check"
    assert result["reasons"][0] == "cancelled"


def test_the_grading_discriminates_on_a_real_edition(tree) -> None:  # type: ignore[no-untyped-def]
    """A grade that put everything in one tier would be decoration.

    Measured against the committed feeds: most of an edition is ready, a substantial
    minority needs a title, and nothing is currently wrong.
    """
    states = node(f"""
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
        const edition = S.resolveEdition({{publicationDate: "2026-09-08"}});
        const r = S.partition(events, edition, "engineering-newsletter", feeds);
        const out = {{}};
        r.included.forEach((i) => {{
          const g = S.readiness(i);
          out[g.state] = (out[g.state] || 0) + 1;
        }});
        console.log(JSON.stringify(out));
    """)
    assert states.get("ready", 0) > 0, "nothing graded ready"
    assert states.get("fix", 0) > 0, "nothing graded as needing a fix"
    # Pinned rather than derived, so a source silently dropping out of the edition fails
    # here. It moves when sources are added, and that is a change worth looking at.
    assert sum(states.values()) == 12


def test_the_export_still_marks_a_placeholder_title(real_listing) -> None:  # type: ignore[no-untyped-def]
    """The badge is additional to the decorator, not a replacement for it.

    The listing leaves this site; a badge in a table does not travel with it.
    """
    flags = [n for n in real_listing if n["cls"] == "placeholder-flag"]
    assert flags
    assert "replace before sending" in flags[0]["text"]


# --------------------------------------------------------------------------------------
# Which edition the reset offers
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected", "why"),
    [
        ("2026-09-07T09:00:00", "2026-09-07", "Monday morning: today's edition is still to come"),
        ("2026-09-07T11:59:00", "2026-09-07", "a minute before it publishes"),
        ("2026-09-07T12:00:00", "2026-09-14", "the moment it publishes, the next one is next"),
        ("2026-09-09T15:00:00", "2026-09-14", "midweek"),
        ("2026-09-12T10:00:00", "2026-09-14", "Saturday"),
        ("2026-09-13T23:59:00", "2026-09-14", "the last minute of the week"),
        ("2026-12-28T09:00:00", "2026-12-28", "across a year boundary"),
    ],
)
def test_the_reset_offers_the_next_edition_not_the_current_week(
    now: str, expected: str, why: str
) -> None:
    """It used to offer this week's Monday.

    On a Saturday that is an edition published five days ago covering a week that ends
    tomorrow — the one edition nobody is composing.
    """
    assert node(f'console.log(JSON.stringify(S.nextEditionDate("{now}")));') == expected, why


def plan_js(weekday: str, time: str) -> str:
    """A schedule literal, for driving the model from a test."""
    return (
        '{publication: {weekday: "' + weekday + '", time: "' + time + '"}, '
        'coverage: {start: {anchor: "publication"}, '
        'end: {anchor: "week_start", offset_days: 6}}}'
    )


def test_the_reset_respects_the_publications_own_hour() -> None:
    """The hour it flips over comes from the schedule, not a hardcoded noon.

    Passing a schedule rather than a bare time is the point: the weekday comes from the
    same place, so a Thursday publication resets to a Thursday.
    """
    early = plan_js("MON", "09:00")
    late = plan_js("MON", "16:00")
    assert (
        node(f'console.log(JSON.stringify(S.nextEditionDate("2026-09-07T10:30:00", {early})));')
        == "2026-09-14"
    ), "10:30 is past a 09:00 publication"
    assert (
        node(f'console.log(JSON.stringify(S.nextEditionDate("2026-09-07T10:30:00", {late})));')
        == "2026-09-07"
    ), "10:30 is before a 16:00 one"


def test_the_reset_follows_the_publications_weekday() -> None:
    """Selecting the DAIS newsletter resets to the next Thursday, not the next Monday."""
    thursday = plan_js("THU", "14:00")
    for now, expected in (
        ("2026-09-21T09:00:00", "2026-09-24"),  # Monday: Thursday is still to come
        ("2026-09-24T13:59:00", "2026-09-24"),  # a minute before it publishes
        ("2026-09-24T14:00:00", "2026-10-01"),  # the moment it does
        ("2026-09-27T12:00:00", "2026-10-01"),  # Sunday
    ):
        assert (
            node(f'console.log(JSON.stringify(S.nextEditionDate("{now}", {thursday})));')
            == expected
        ), now


# --------------------------------------------------------------------------------------
# Saying which events look like the same talk
# --------------------------------------------------------------------------------------


def test_a_repeat_comes_back_as_the_events_not_just_a_count(real_edition, tree) -> None:  # type: ignore[no-untyped-def]
    """A count tells an editor a duplicate exists and not where to look for it, which
    leaves them scanning the table — the work the note was meant to save."""
    groups = node(f"""
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
        const edition = S.resolveEdition({{publicationDate: "2026-09-08"}});
        const r = S.partition(events, edition, "engineering-newsletter", feeds);
        console.log(JSON.stringify(r.collisionGroups.map((g) => g.map((i) => ({{
          startTime: i.startTime, title: i.title, placeholder: i.placeholder,
          sponsors: i.sponsors.map((x) => x.label)
        }})))));
    """)
    assert len(groups) == 1, "the ai/materials pair"
    pair = groups[0]
    assert len(pair) == 2
    assert {s for item in pair for s in item["sponsors"]} == {
        "Princeton AI Lab",
        "Princeton Materials Institute",
    }
    assert len({item["startTime"] for item in pair}) == 1, "one time, two listings"
    assert all(item["placeholder"] for item in pair), "both titles are synthesized"


def test_the_repeat_count_and_the_groups_agree(real_edition) -> None:  # type: ignore[no-untyped-def]
    """The count is derived from the groups rather than tallied separately, so the
    summary cannot say two while the list shows three."""
    assert real_edition["collisions"] == 1


def test_an_edition_with_no_repeats_offers_nothing_to_expand(tree) -> None:  # type: ignore[no-untyped-def]
    groups = node(f"""
        const fs = require("fs");
        const root = {json.dumps(str(tree))};
        const events = JSON.parse(fs.readFileSync(root + "/feeds/orfe/events.json"));
        const edition = S.resolveEdition({{publicationDate: "2026-09-08"}});
        const r = S.partition(events, edition, null, {{orfe: {{label: "ORFE"}}}});
        console.log(JSON.stringify({{groups: r.collisionGroups.length, count: r.collisions}}));
    """)
    assert groups == {"groups": 0, "count": 0}


# --------------------------------------------------------------------------------------
# A second publication, with a different schedule and a different shape
# --------------------------------------------------------------------------------------
#
# The engineering differential proves the simulator reproduces one edition. On its own
# that is consistent with the engineering shape being hardcoded behind a label, which is
# how the predecessor repositories ended up with one page maintained twice.
#
# `tests/fixtures/newsletter/2026-09-24-dais-edition.json` is the DaIS edition of
# Thursday 24 September 2026, transcribed from the `.eml` with every quoted string
# verified against it. It differs from the engineering edition in both axes the mechanism
# claims to carry: a Thursday publication covering the *following* week, and a layout with
# no day headings, prose attribution and one inline when-and-where line.

DAIS = json.loads(
    (FIXTURES / "newsletter" / "2026-09-24-dais-edition.json").read_text(encoding="utf-8")
)

#: Walks the rendered listing into one record per event block.
#:
#: `flat` is enough for the day-grouped listing, whose assertions are about the sequence.
#: This layout's assertions are about what belongs together -- which line sits under which
#: title -- so the block structure has to survive.
BLOCKS_JS = """
const pick = (node, cls) => {
  const out = [];
  const walk = (n) => {
    if (n.className === cls) out.push(n);
    (n.childNodes || []).forEach(walk);
  };
  walk(node);
  return out;
};
/* Text of a whole subtree. `textContent` on the stub is only what was assigned to that
   one node, and this layout builds its prose out of text nodes and links -- so reading it
   directly returns "" for exactly the lines worth asserting. */
const deepText = (node) =>
  (node.textContent || "") +
  (node.childNodes || []).map(deepText).join("");
const textOf = (node, cls) => {
  const found = pick(node, cls);
  return found.length ? deepText(found[0]) : "";
};
const hrefs = (node) => {
  const out = [];
  const walk = (n) => {
    if (n.getAttribute && n.getAttribute("href")) out.push(n.getAttribute("href"));
    (n.childNodes || []).forEach(walk);
  };
  walk(node);
  return out;
};
const blocks = (root) => pick(root, "ev").map((b) => ({
  title: textOf(b, "ev-title"),
  hosted: textOf(b, "ev-hosted"),
  speaker: textOf(b, "ev-speaker"),
  blurb: textOf(b, "ev-blurb"),
  when: textOf(b, "ev-when"),
  more: textOf(b, "ev-more"),
  hrefs: hrefs(b)
}));
"""


def dais_blocks(tree: Path, publication: str = "2026-09-24") -> dict:
    """The DaIS listing as blocks, plus the headings that should not be there."""
    return node(f"""
        {DOC_STUB}
        {BLOCKS_JS}
        const root = {json.dumps(str(tree))};
        const publication = "{publication}";
        const purpose = "dais-newsletter";
        {MANIFEST_STUB}
        const r = S.partition(events, edition, purpose, feeds);
        const rendered = S.buildListing(edition, r.included, doc, declared.template);
        console.log(JSON.stringify({{
          template: declared.template,
          label: declared.label,
          edition: edition,
          collisions: r.collisions,
          heading: (flat(rendered, []).filter((n) => n.tag === "h2")[0] || {{}}).text,
          dayHeadings: flat(rendered, []).filter((n) => n.tag === "h3").map((n) => n.text),
          blocks: blocks(rendered)
        }}));
    """)


@pytest.fixture(scope="module")
def dais(tree):  # type: ignore[no-untyped-def]
    return dais_blocks(tree)


# ---- the schedule -------------------------------------------------------------------


def test_the_dais_window_is_the_week_after_a_thursday_publication(dais) -> None:  # type: ignore[no-untyped-def]
    """The engineering edition covers the week it appears in; this one covers the next.

    Both come from `status.json`, so a single hardcoded weekly rule cannot satisfy them
    at once -- which is the property being asserted, more than the dates themselves.
    """
    assert dais["edition"]["publicationAt"] == "2026-09-24T14:00:00"
    assert dais["edition"]["coverageStart"] == "2026-09-28T00:00:00"
    assert dais["edition"]["coverageEnd"] == "2026-10-04T23:59:59"
    assert dais["edition"]["coverageStart"] > dais["edition"]["publicationAt"]


def test_the_dais_edition_is_not_reported_as_shifted(dais) -> None:  # type: ignore[no-untyped-def]
    """A Thursday publication is not a shifted Monday one.

    `shifted` is measured against the schedule's own weekday. Measuring it against Monday
    would mark every DaIS edition as an exception to a rule it never followed, and the
    page says so on screen.
    """
    assert dais["edition"]["shifted"] is False


def test_no_deadline_is_invented_for_a_publication_that_states_none(dais) -> None:  # type: ignore[no-untyped-def]
    """DaIS's edition says only to send an email. An empty string, not a date."""
    assert dais["edition"]["deadlineAt"] == ""


def test_the_window_matches_the_edition_that_went_out(dais) -> None:  # type: ignore[no-untyped-def]
    assert dais["edition"]["coverageStart"] == DAIS["_edition"]["coverageStart"]
    assert dais["edition"]["coverageEnd"] == DAIS["_edition"]["coverageEnd"]


# ---- the events ---------------------------------------------------------------------


def label_of(tree: Path, slug: str) -> str:
    manifest = json.loads((tree / "status.json").read_text(encoding="utf-8"))
    feeds = {
        f["path"].split("/")[1]: f for f in manifest["feeds"] if f["path"].startswith("feeds/")
    }
    return str(feeds[slug]["label"])


@pytest.mark.parametrize(
    "expected",
    [e for e in DAIS["events"] if e["expect"]["inFixtures"]],
    ids=lambda e: e["theirTitle"][:40],
)
def test_each_dais_event_the_fixtures_hold_is_predicted(dais, tree, expected) -> None:  # type: ignore[no-untyped-def]
    """Six of the edition's eight events, each from every source that publishes it.

    The two absent ones are recorded in the fixture with their reasons: one is in the live
    robotics feed but not the committed snapshot, and one is ORFE's colloquium, where the
    editor wrote the series name and linked the flyers page while our feed carries the
    actual talk.
    """
    title = expected.get("ourTitle", expected["theirTitle"])
    lines = expected.get("ourLineBySource", {})
    for slug in expected["expect"]["sources"]:
        hosted = "Hosted by " + label_of(tree, slug)
        mine = [b for b in dais["blocks"] if b["hosted"] == hosted and title in b["title"]]
        assert mine, f"nothing from {slug} titled {title!r}"
        assert lines.get(slug, expected["ourLine"]) in [b["when"] for b in mine], (
            f"{slug} rendered {[b['when'] for b in mine]}"
        )


def test_the_when_and_where_line_is_reproduced_exactly(dais) -> None:  # type: ignore[no-untyped-def]
    """The strongest single claim in this file: one line, character for character.

    Their own edition is inconsistent -- a comma after the meridiem on some entries and
    not others, the year on one, a hyphen where the next line has an em dash. This one
    entry is written the same way in both, so it is the one that can be asserted whole
    rather than modulo punctuation.
    """
    emergent = next(e for e in DAIS["events"] if e["theirTitle"].startswith("Emergent"))
    assert emergent["theirLine"] == emergent["ourLine"], "the fixture's own claim"
    assert emergent["ourLine"] in [b["when"] for b in dais["blocks"]]


def test_the_talk_two_selected_units_both_publish_is_counted_as_a_repeat(dais) -> None:  # type: ignore[no-untyped-def]
    """The editor credited DARK MANSIONS to CITP alone and Emergent Symbol Processing to
    NAM alone. Our feeds publish each twice, because a per-source feed reproduces its own
    upstream, and choosing between them is the editor's call rather than ours.

    They also disagree about the venue -- citp writes `Computer Science Building`, dais
    writes `Computer Science` -- and neither is corrected.
    """
    assert dais["collisions"] == 2
    venues = {b["when"].split(" in ")[-1] for b in dais["blocks"] if "DARK MANSIONS" in b["title"]}
    assert venues == {"Computer Science Building 105", "Computer Science 105"}


def test_what_the_edition_left_out_is_still_offered(dais) -> None:  # type: ignore[no-untyped-def]
    """The simulator offers what the feeds carry; it does not decide an edition.

    Two events fall in the window and declare the purpose without appearing in the
    edition. Filtering them out would mean encoding an editor's judgement as a rule, and
    the per-row include checkboxes exist so it does not have to be.
    """
    titles = " | ".join(b["title"] for b in dais["blocks"])
    assert "Bias in AI Reading Group" in titles
    assert "Northeast Robotics Colloquium" in titles


# ---- the shape ----------------------------------------------------------------------


def test_the_two_purposes_render_different_shapes(dais, real_listing) -> None:  # type: ignore[no-untyped-def]
    """Same events, same code path, different template -- the claim `purpose` makes."""
    assert dais["template"] == "inline-date"
    assert dais["dayHeadings"] == [], "chronological, with no day headings"
    assert [n["text"] for n in real_listing if n["tag"] == "h3"], "the other one has them"


def test_the_inline_listing_attributes_in_prose(dais) -> None:  # type: ignore[no-untyped-def]
    """`Hosted by Data and Intelligent Systems`, not `Sponsor: ...`.

    One verb, always. Their edition also writes "Co-sponsored by" and "Presented by", but
    no feed carries a verb and choosing one per event would be asserting a fact about an
    arrangement we cannot see.
    """
    hosted = [b["hosted"] for b in dais["blocks"] if b["hosted"]]
    assert hosted, "every block in this edition has a sponsor"
    assert all(line.startswith("Hosted by ") for line in hosted)
    assert not any("Sponsor:" in line for line in hosted)


def test_every_block_with_a_page_ends_in_a_learn_more_link(dais) -> None:  # type: ignore[no-untyped-def]
    linked = [b for b in dais["blocks"] if b["hrefs"]]
    assert linked
    assert all(b["more"] == "Learn More" for b in linked)


def test_an_event_with_no_location_says_so_rather_than_trailing_off() -> None:
    """`location TBA`, which the engineering template omits entirely.

    Two templates over the same data, differing on purpose. Their own edition writes these
    words, so an empty location has a rendering here rather than a silent gap.
    """
    line = node("""
        console.log(JSON.stringify(S.whenAndWhere({
          startTime: "2026-10-02T11:00:00", endTime: "2026-10-02T12:00:00", place: ""
        })));
    """)
    assert line == "11 a.m. — 12 p.m. Friday, Oct. 2, location TBA"


def test_an_all_day_event_is_not_written_as_a_midnight_range(dais) -> None:  # type: ignore[no-untyped-def]
    """`robotics`' Northeast Robotics Colloquium runs 3 October 00:00 to 4 October 00:00.

    Put through the range arithmetic that produces `4:30 — 6 p.m.`, that reads
    `12 — 12 a.m.` -- a zero-length midnight event, which is not what the feed says. The
    model carries no all-day flag, so it is read off the stamps.
    """
    nerc = next(b for b in dais["blocks"] if "Northeast Robotics" in b["title"])
    assert nerc["when"] == "All day Saturday, Oct. 3, in Commons"


# ---- the format details their edition pins down --------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected", "why"),
    [
        ("2026-09-28T16:30:00", "2026-09-28T18:00:00", "4:30 — 6 p.m.", "one shared meridiem"),
        ("2026-10-02T09:00:00", "2026-10-02T10:00:00", "9 — 10 a.m.", "and in the morning"),
        (
            "2026-09-30T13:00:00",
            "2026-09-30T15:45:00",
            "1 — 3:45 p.m.",
            "`:00` dropped at the open",
        ),
        (
            "2026-10-02T11:00:00",
            "2026-10-02T12:00:00",
            "11 a.m. — 12 p.m.",
            "crossing noon, so the meridiem is stated twice",
        ),
        (
            "2026-09-29T23:30:00",
            "2026-09-30T00:30:00",
            "11:30 p.m. — 12:30 a.m.",
            "and crossing midnight",
        ),
        ("2026-09-29T12:00:00", "", "12 p.m.", "noon is 12 p.m., not 0 p.m."),
        ("2026-09-29T00:00:00", "", "12 a.m.", "and midnight is 12 a.m."),
        ("2026-09-29T16:30:00", "", "4:30 p.m.", "no end time, so no range"),
    ],
)
def test_a_time_range_is_written_the_way_their_edition_writes_one(
    start: str, end: str, expected: str, why: str
) -> None:
    """Where this goes wrong is the shared meridiem, so noon and midnight are both here.

    Taking the meridiem from the start time and printing it once gives `11 a.m. — 12 a.m.`
    for a talk that ends at noon: readable, plausible and an hour wrong at one end.
    """
    got = node(f'console.log(JSON.stringify(S.timeRange("{start}", "{end}")));')
    assert got == expected, why


@pytest.mark.parametrize(
    ("date", "expected"),
    [
        ("2026-09-28", "Monday, Sept. 28"),
        ("2026-10-02", "Friday, Oct. 2"),
        ("2026-03-09", "Monday, March 9"),
        ("2026-07-01", "Wednesday, July 1"),
    ],
)
def test_the_month_is_abbreviated_their_way(date: str, expected: str) -> None:
    """`Sept.` rather than `Sep.`, and the short months are not abbreviated at all --
    Princeton house style, and what their own edition writes. No leading zero on the day.
    """
    assert node(f'console.log(JSON.stringify(S.shortDate("{date}")));') == expected


def test_the_reset_finds_the_next_thursday_for_this_publication(tree) -> None:  # type: ignore[no-untyped-def]
    """The reset button has to offer the next *DaIS* edition when DaIS is selected, and
    the weekday it rolls over on comes from the manifest rather than from the page."""
    got = node(f"""
        const fs = require("fs");
        const root = {json.dumps(str(tree))};
        const manifest = JSON.parse(fs.readFileSync(root + "/status.json"));
        const plan = manifest.purposes["dais-newsletter"].schedule;
        console.log(JSON.stringify({{
          midweek: S.nextEditionDate("2026-09-22T09:00:00", plan),
          onTheDayBefore: S.nextEditionDate("2026-09-24T13:59:00", plan),
          onceItIsOut: S.nextEditionDate("2026-09-24T14:01:00", plan)
        }}));
    """)
    assert got == {
        "midweek": "2026-09-24",
        "onTheDayBefore": "2026-09-24",
        "onceItIsOut": "2026-10-01",
    }


# --------------------------------------------------------------------------------------
# Two styles, and the rule that neither may be half-dressed
# --------------------------------------------------------------------------------------
#
# The DaIS layout shipped with no rules at all in `EXPORT_STYLES`: every class it emits
# was absent from the map, so the preview looked right against the site's stylesheet and
# the exported copy went out as unstyled paragraphs. Nothing caught it, because the
# styling tests rendered the day-grouped listing and only that.
#
# So the map and the markup are now checked against each other, in both directions and
# for every template. A class with no rule is an element that leaves here naked; a rule
# with no class is dead weight that hides the next rename.

#: One item set exercising every branch either template has: a placeholder title, two
#: sponsors of which one has no site, a speaker, a description, and an event with no page.
STYLE_PROBE = (
    TINY_DOM
    + """
const edition = S.resolveEdition({publicationDate: "2026-09-08"});
const feeds = {
  citp: {label: "Center for Information Technology Policy", home: "https://citp.princeton.edu"},
  nowhere: {label: "A Unit With No Site"}
};
const items = [
  S.decorate({
    startTime: "2026-09-08T12:15:00", endTime: "2026-09-08T13:15:00",
    title: "AI Agents and the Augmentation Agenda", sources: ["citp", "nowhere"],
    series: "CITP Seminars", urlRef: "https://citp.princeton.edu/events/2026/ai-agents",
    speakers: [{name: "Arvind Narayanan", affiliation: "Princeton University"}],
    location: {name: "Sherrerd Hall", detail: "306"},
    content: "A talk about what agents can and cannot do."
  }, feeds),
  S.decorate({
    startTime: "2026-09-09T16:30:00", title: "A CITP Seminars Talk by",
    titleIsPlaceholder: true, sources: ["citp"], series: "CITP Seminars",
    location: {name: "Sherrerd Hall", detail: "306"}
  }, feeds)
];
const render = (template, list) => {
  const tree = S.buildListing(edition, list, D.document, template);
  const host = D.makeElement("div");
  host.replaceChildren.apply(host, tree.childNodes.slice());
  return host.innerHTML;
};
"""
)

#: `<tag ... class="name">`, which is exactly the shape `inlineStyles` keys on.
_TAGGED = re.compile(r'<([a-z0-9]+)[^>]*\sclass="([^"]+)"')


def emitted_classes(template: str) -> set[str]:
    """Every `tag.class` either listing of this template can produce."""
    markup = node(
        STYLE_PROBE + f'console.log(JSON.stringify([render("{template}", items), '
        f'render("{template}", [])]));'
    )
    return {f"{tag}.{cls}" for chunk in markup for tag, cls in _TAGGED.findall(chunk)}


@pytest.mark.parametrize("template", ["day-grouped", "inline-date"])
def test_every_class_a_template_emits_has_a_rule(template: str) -> None:
    """The defect that shipped, in the direction it shipped in."""
    rules = set(node("console.log(JSON.stringify(S.EXPORT_STYLES));")[template])
    emitted = emitted_classes(template)
    assert emitted, "the probe rendered nothing, so this would pass vacuously"
    assert emitted - rules == set(), (
        f"{template} emits these with no rule, so they leave here unstyled: "
        f"{sorted(emitted - rules)}"
    )


@pytest.mark.parametrize("template", ["day-grouped", "inline-date"])
def test_no_rule_survives_the_class_it_styled(template: str) -> None:
    """The other direction. A rule for a class nothing emits any more is not harmless:
    it reads as coverage, and it is what makes the next rename look already handled."""
    rules = set(node("console.log(JSON.stringify(S.EXPORT_STYLES));")[template])
    emitted = emitted_classes(template)
    assert rules - emitted == set(), (
        f"{template} has rules for classes it never emits: {sorted(rules - emitted)}"
    )


@pytest.mark.parametrize("template", ["day-grouped", "inline-date"])
def test_every_element_in_either_style_carries_its_rule_inline(template: str) -> None:
    """Mailchimp strips `<style>`, so the check that matters is per element, per style."""
    doc = node(
        STYLE_PROBE + f'const host = D.makeElement("div");\n'
        f'const tree = S.buildListing(edition, items, D.document, "{template}");\n'
        f"host.replaceChildren.apply(host, tree.childNodes.slice());\n"
        f'console.log(JSON.stringify(S.exportDocument(host, "{template}")));'
    )
    unstyled = re.findall(r"<(h2|h3|hr|dl|dt|dd|p|span|div|a)(?![^>]*style=)[^>]*>", doc)
    assert unstyled == [], f"{template}: tags with no inline style: {unstyled}"


def test_the_two_styles_are_not_the_same_style() -> None:
    """Anti-vacuity for everything above. If `inline-date` fell back to the default map,
    every test here would still pass while the switcher did nothing."""
    styles = node("console.log(JSON.stringify(S.EXPORT_STYLES));")
    assert styles["day-grouped"] != styles["inline-date"]
    assert "#EC2770" in " ".join(styles["inline-date"].values()), "the DaIS accent"
    assert "#EC2770" not in " ".join(styles["day-grouped"].values())


def test_an_unknown_style_falls_back_rather_than_rendering_nothing() -> None:
    """A stale link carrying a retired style name should produce the default listing, not
    an unstyled one. The registry refuses an unknown template at load, so this is about a
    URL somebody kept, not about configuration."""
    same = node("""
        console.log(JSON.stringify(
          S.stylesFor("no-such-style") === S.stylesFor(S.DEFAULT_TEMPLATE)
        ));
    """)
    assert same is True


@pytest.mark.parametrize(("events", "rules"), [(0, 0), (1, 0), (2, 1), (5, 4)])
def test_the_dais_listing_rules_between_events_not_after_them(events: int, rules: int) -> None:
    """Their edition puts a dotted rule between entries.

    Between, not after: a rule below the last event leaves an editor a trailing line to
    delete, which is exactly the tidying this exists to remove. The single-event case is
    the one an off-by-one gets wrong and nobody notices, since most editions have several.
    """
    got = node(
        STYLE_PROBE
        + f"""
        const many = [];
        for (let i = 0; i < {events}; i++) {{
          many.push(S.decorate({{
            startTime: "2026-09-0" + (i + 1) + "T12:00:00", title: "Talk " + i,
            sources: ["citp"], location: {{name: "Sherrerd Hall", detail: "306"}}
          }}, feeds));
        }}
        const html = render("inline-date", many);
        console.log(JSON.stringify((html.match(/<hr /g) || []).length));
    """
    )
    assert got == rules


def test_the_dais_export_carries_its_own_accent(dais) -> None:  # type: ignore[no-untyped-def]
    """The pink pill and dotted rule are theirs, taken from the issue rather than chosen.

    Worth pinning because it is the visible difference between "the DAIS layout" and
    "the engineering layout with the headings removed".
    """
    styles = node("console.log(JSON.stringify(S.EXPORT_STYLES));")["inline-date"]
    assert "border-radius: 50px" in styles["a.ev-button"]
    assert "#EC2770" in styles["a.ev-button"]
    assert "dotted #EC2770" in styles["hr.ev-rule"]
    assert "text-align: center" in styles["p.ev-when"]
    assert "italic" in styles["p.ev-hosted"]
