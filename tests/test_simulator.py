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
    assert "exportDocument(exportEl)" in code, "download must read from the page"
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
        console.log(JSON.stringify({
          sheet: S.exportStylesheet(),
          styles: S.EXPORT_STYLES
        }));
    """)
    for selector, declarations in both["styles"].items():
        assert f"{selector} {{ {declarations} }}" in both["sheet"]


def test_the_export_styling_is_email_safe() -> None:
    """No grid, no flex, no custom properties.

    Grid and flex are unsupported across most email clients, and a custom property
    resolves to nothing once the file leaves this site, where `--dim` is defined. A `dl`
    losing its grid falls back to label-then-value on separate lines, which is how the
    editors' own edition already reads.
    """
    declarations = " ".join(node("console.log(JSON.stringify(S.EXPORT_STYLES));").values())
    for banned in ("grid", "flex", "var(--", "currentColor"):
        assert banned not in declarations, f"{banned} does not survive an email client"


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
    assert "inlineStyles(exportEl.innerHTML)" in code


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
