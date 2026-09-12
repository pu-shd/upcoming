"""The landing page, and the fact that the root is served at all.

The root 404'd for this site's first three deploys while every feed resolved perfectly,
which is precisely the kind of fault nothing notices: no ingest depends on it, so no
consumer complains, and the publishing workflow is green throughout.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.support import FIXTURES, REPO_ROOT
from upcoming.build import build_from_file, load_pronunciation
from upcoming.publish import assemble, site_files
from upcoming.verify import WARN, check_landing_page

SITE = REPO_ROOT / "site"
INDEX = (SITE / "index.html").read_text(encoding="utf-8")
SIMULATOR = (SITE / "simulator.html").read_text(encoding="utf-8")
STYLE = (SITE / "style.css").read_text(encoding="utf-8")

#: Every page, so an assertion about "a page" cannot silently apply to one of them. A
#: second page that skipped these checks would be the obvious regression.
PAGES = {"index.html": INDEX, "simulator.html": SIMULATOR}


def test_the_site_directory_has_an_index() -> None:
    assert (SITE / "index.html").is_file()


def test_the_index_is_copied_into_the_published_tree(registry, tmp_path):  # type: ignore[no-untyped-def]
    load_pronunciation()
    results = {
        s.slug: build_from_file(FIXTURES / "feeds" / s.slug / "feed.ics", s) for s in registry.live
    }
    tree = assemble(registry, results, {}, {}, root=tmp_path, generated_at="2026-09-11T12:00:00Z")
    assert tree.files["index.html"] == INDEX


def test_a_static_file_can_never_overwrite_a_feed_or_the_manifest(registry, tmp_path):  # type: ignore[no-untyped-def]
    """Order matters: the data is the product, and the page is decoration over it.

    A file dropped into site/ named status.json would otherwise replace the health
    manifest with a static copy that is wrong the moment it is written.
    """
    load_pronunciation()
    results = {
        s.slug: build_from_file(FIXTURES / "feeds" / s.slug / "feed.ics", s) for s in registry.live
    }
    tree = assemble(registry, results, {}, {}, root=tmp_path, generated_at="2026-09-11T12:00:00Z")
    document = json.loads(tree.files["status.json"])
    assert document["generatedAt"] == "2026-09-11T12:00:00Z"
    assert json.loads(tree.files["feeds/orfe/events.json"])


def test_site_files_absent_is_not_an_error(tmp_path):  # type: ignore[no-untyped-def]
    """A build that produces only data is a valid build."""
    assert site_files(tmp_path / "nothing-here") == {}


# --------------------------------------------------------------------------------------
# The page itself
# --------------------------------------------------------------------------------------


#: The one external origin a page may load from. The menu is set in Libre Franklin to
#: match orfe.princeton.edu, which loads it from here -- a self-hosted copy would be more
#: self-contained but would also drift from the face the site it matches is actually
#: using. The trade is acceptable only because it is *decorative*: the stack falls back to
#: the system sans and every page renders completely without it.
FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


@pytest.mark.parametrize("page", sorted(PAGES), ids=sorted(PAGES))
def test_no_page_loads_an_external_resource(page: str) -> None:
    """Self-contained apart from the webfont, so a page cannot break because a CDN did.

    Resources only. A hyperlink to another site is navigation the reader chooses, and the
    page still renders fully offline without it.
    """
    body = PAGES[page]
    resources = re.findall(r'src\s*=\s*["\']([^"\']+)', body)
    resources += re.findall(r'<link[^>]+href\s*=\s*["\']([^"\']+)', body)
    for url in resources:
        if any(host in url for host in FONT_HOSTS):
            continue
        assert not url.startswith(("http://", "https://", "//")), f"{page}: external {url}"


@pytest.mark.parametrize("page", sorted(PAGES), ids=sorted(PAGES))
def test_the_webfont_is_decorative_not_required(page: str) -> None:
    """A font that fails to load must cost the page nothing but its face.

    `display=swap` so text is never invisible while it loads, and a real fallback stack
    so a blocked request leaves the menu set in the system sans rather than in whatever
    the browser reaches for last.
    """
    body = PAGES[page]
    if "fonts.googleapis.com" not in body:
        return
    assert "display=swap" in body
    assert '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>' in body
    sans = re.search(r"--sans:\s*([^;]+);", STYLE)
    assert sans, "expected a --sans stack"
    assert sans.group(1).count(",") >= 3, f"fallbacks are thin: {sans.group(1)}"
    assert sans.group(1).strip().endswith("sans-serif")


@pytest.mark.parametrize("page", sorted(PAGES), ids=sorted(PAGES))
def test_every_outbound_link_is_somewhere_we_meant_to_send_people(page: str) -> None:
    """Navigation is allowed, but not to anywhere at all.

    A stray absolute URL in a published page is how a typo becomes a link to somebody
    else's site, so the hosts are enumerated rather than merely permitted.
    """
    allowed = ("github.com/pu-shd/upcoming",)
    for url in re.findall(r'<a[^>]+href\s*=\s*["\']([^"\']+)', PAGES[page]):
        if url.startswith(("http://", "https://")):
            assert any(host in url for host in allowed), f"{page}: unexpected link {url}"


def test_the_page_reads_the_manifest_rather_than_being_generated_with_it() -> None:
    """This is what stops the page drifting out of step with the feeds.

    A page rendered at build time states counts that are true only at that instant; this
    one fetches, so it is either current or visibly broken.
    """
    assert "fetch('status.json'" in INDEX
    assert re.search(r"\{\s*cache:\s*'no-store'\s*\}", INDEX)


def test_the_page_says_so_when_the_manifest_cannot_be_read() -> None:
    """Silence must not equate to health.

    A page that renders an empty table on a failed fetch looks like a site with no feeds,
    which reads as calm rather than as broken.
    """
    assert 'id="error"' in INDEX
    assert ".catch(" in INDEX


def test_the_page_escapes_what_it_renders() -> None:
    """status.json carries publisher-derived text: titles, locations, failure reasons.

    None of it is trusted markup, and it reaches the DOM through innerHTML.
    """
    assert "&amp;" in INDEX and "&lt;" in INDEX
    assert re.search(r"function\s*\(s\)\s*\{\s*\n?\s*return String", INDEX)


@pytest.mark.parametrize(
    "claim",
    [
        "status.json",  # the freshness contract
        "stale",  # a failing source keeps serving, and says so
    ],
)
def test_the_page_states_the_contracts_a_consumer_needs(claim: str) -> None:
    """These are the four things a consumer gets wrong if nobody tells them."""
    assert claim in INDEX


@pytest.mark.parametrize("page", sorted(PAGES), ids=sorted(PAGES))
def test_every_page_declares_a_viewport_and_a_language(page: str) -> None:
    assert 'name="viewport"' in PAGES[page]
    assert re.search(r'<html[^>]+lang="en"', PAGES[page])


@pytest.mark.parametrize("page", sorted(PAGES), ids=sorted(PAGES))
def test_wide_content_scrolls_inside_its_own_container(page: str) -> None:
    """The tables are wide and the body must never scroll sideways on a phone."""
    assert "overflow-x: auto" in STYLE
    assert 'class="card scroll"' in PAGES[page]


def test_both_themes_are_defined_once_in_the_shared_stylesheet() -> None:
    """A colour defined only inside the dark block is the classic unreadable-page bug.

    Defining the palette on bare `:root` and redefining only the tokens under the media
    query is what makes the un-stamped default resolve as a complete set.
    """
    assert "color-scheme: light dark" in STYLE
    assert "prefers-color-scheme: dark" in STYLE
    for page in PAGES.values():
        assert "prefers-color-scheme" not in page, "theming belongs in style.css, not a page"


@pytest.mark.parametrize("page", sorted(PAGES), ids=sorted(PAGES))
def test_every_page_uses_the_shared_stylesheet(page: str) -> None:
    """One stylesheet, not a copy per page.

    Duplicating it would be clone-and-retarget in miniature, which is the failure the
    simulator page exists to demonstrate an alternative to -- the two predecessor
    repositories ship byte-identical simulator JavaScript.
    """
    assert '<link rel="stylesheet" href="style.css">' in PAGES[page]
    assert "<style>" not in PAGES[page]


@pytest.mark.parametrize("page", sorted(PAGES), ids=sorted(PAGES))
def test_every_page_carries_the_menu(page: str) -> None:
    """A menu on one page only is the likely regression, so it is asserted on both."""
    body = PAGES[page]
    assert 'class="nav"' in body
    for target in PAGES:
        assert f'href="{target}"' in body, f"{page} does not link {target}"
    assert body.count('aria-current="page"') == 1
    assert f'href="{page}" aria-current="page"' in body


def test_the_stylesheet_is_published_with_the_pages(registry, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """It is a resource the pages cannot render without, so a publish that drops it is
    worse than one that drops a page -- both pages would serve unstyled."""
    load_pronunciation()
    results = {
        s.slug: build_from_file(FIXTURES / "feeds" / s.slug / "feed.ics", s) for s in registry.live
    }
    tree = assemble(registry, results, {}, {}, root=tmp_path, generated_at="2026-09-11T12:00:00Z")
    assert tree.files["style.css"] == STYLE
    assert tree.files["simulator.html"] == SIMULATOR
    assert "simulator.js" in tree.files


def test_the_simulator_declares_every_control_it_wires_up() -> None:
    """The JS looks controls up by id. A renamed id fails silently at runtime -- the
    listener simply never attaches -- so the two are checked against each other here."""
    wanted = set(
        re.findall(r'\$\("([a-z-]+)"\)', (SITE / "simulator.js").read_text(encoding="utf-8"))
    )
    assert wanted, "expected the simulator to look up controls by id"
    for control in sorted(wanted):
        assert f'id="{control}"' in SIMULATOR, (
            f"simulator.js wires #{control}, the page has no such id"
        )


# --------------------------------------------------------------------------------------
# The watchdog notices a missing root
# --------------------------------------------------------------------------------------


def test_the_watchdog_warns_when_the_root_is_not_served() -> None:
    from tests.test_verify import BASE, SiteTransport

    findings = check_landing_page(BASE, SiteTransport())
    assert [f.level for f in findings] == [WARN]
    assert "not served" in findings[0].message


def test_the_watchdog_warns_when_the_root_is_not_html() -> None:
    """A server can answer 200 with a directory listing or an error body."""
    from tests.test_verify import BASE, SiteTransport

    findings = check_landing_page(BASE, SiteTransport(files={"": "not a page"}))
    assert [f.level for f in findings] == [WARN]


def test_a_missing_landing_page_warns_rather_than_failing() -> None:
    """No consumer's ingest depends on it, and a watchdog that cries wolf gets ignored."""
    from tests.test_verify import BASE, SiteTransport

    assert check_landing_page(BASE, SiteTransport())[0].level == WARN


def test_the_watchdog_accepts_the_real_page() -> None:
    from tests.test_verify import BASE, SiteTransport

    assert check_landing_page(BASE, SiteTransport(files={"": INDEX})) == []


# --------------------------------------------------------------------------------------
# The schema, served rather than only committed
# --------------------------------------------------------------------------------------


def test_both_schemas_are_published(registry, tmp_path):  # type: ignore[no-untyped-def]
    """Shipping them in the repository only is half a contract.

    A validator cannot resolve a path in somebody else's git tree, and
    events.schema.json $refs its sibling — so both are served or neither resolves.
    """
    load_pronunciation()
    results = {
        s.slug: build_from_file(FIXTURES / "feeds" / s.slug / "feed.ics", s) for s in registry.live
    }
    tree = assemble(registry, results, {}, {}, root=tmp_path, generated_at="2026-09-11T12:00:00Z")
    assert "schema/event.schema.json" in tree.files
    assert "schema/events.schema.json" in tree.files


def test_the_published_schema_validates_the_published_feeds(registry, tmp_path):  # type: ignore[no-untyped-def]
    """The loop that matters: the served contract accepts the served data.

    Validating against the repository copy would prove the repository is consistent with
    itself. This proves a consumer fetching both over HTTPS gets an agreeing pair.
    """
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    load_pronunciation()
    results = {
        s.slug: build_from_file(FIXTURES / "feeds" / s.slug / "feed.ics", s) for s in registry.live
    }
    tree = assemble(registry, results, {}, {}, root=tmp_path, generated_at="2026-09-11T12:00:00Z")

    event = json.loads(tree.files["schema/event.schema.json"])
    feed = json.loads(tree.files["schema/events.schema.json"])
    store = Registry().with_resource(
        event["$id"], Resource.from_contents(event, default_specification=DRAFT202012)
    )
    validator = Draft202012Validator(feed, registry=store)

    for path, body in tree.files.items():
        if path.startswith(("feeds/", "combos/")):
            assert list(validator.iter_errors(json.loads(body))) == [], path


# --------------------------------------------------------------------------------------
# The simulator's newer controls
# --------------------------------------------------------------------------------------


def test_the_received_table_has_a_column_for_including_an_event() -> None:
    """The editor curates the export without changing what the feed says was ingested."""
    assert 'id="pick-all"' in SIMULATOR
    assert 'class="pick"' in SIMULATOR
    header = re.search(r"<thead>.*?</thead>", SIMULATOR, re.S).group(0)
    assert header.count("<th ") == 7, "six data columns plus the include box"
    assert 'aria-label="Include every event in the export"' in header


def test_the_include_column_only_changes_the_export() -> None:
    """Stated on the page, because a control that silently changed both views would be
    the natural misreading."""
    assert "does not change either way" in SIMULATOR


def test_the_export_buttons_sit_below_the_listing() -> None:
    """You decide to copy after reading it, not before."""
    preview = SIMULATOR.index('id="export"')
    for control in ('id="copy"', 'id="download"'):
        assert SIMULATOR.index(control) > preview, f"{control} is above the preview"


def test_the_export_section_is_named_for_what_it_produces() -> None:
    assert "<h2>Email and Events Page Export</h2>" in SIMULATOR


def test_the_export_says_how_much_of_the_edition_it_holds() -> None:
    """Otherwise a deselected event looks like a missing one."""
    assert 'id="export-count"' in SIMULATOR


def test_the_deadline_is_a_control_rather_than_a_dead_string() -> None:
    """Retyping a date into a calendar is exactly the transcription this project removes.

    Built by the script rather than written into the page, because the deadline changes
    with every edition -- so the guard is that the styles it needs exist.
    """
    for rule in (".deadline-toggle", ".deadline-menu", ".deadline-choice"):
        assert rule in STYLE, rule
    code = (SITE / "simulator.js").read_text(encoding="utf-8")
    assert "deadlineControl" in code
    assert 'setAttribute("aria-expanded"' in code


def test_the_deadline_menu_can_be_dismissed() -> None:
    """A menu left stranded open over the page is worse than no menu."""
    code = (SITE / "simulator.js").read_text(encoding="utf-8")
    assert '"Escape"' in code
    assert 'document.addEventListener("click", close)' in code


def test_the_calendar_link_opens_safely() -> None:
    """It leaves the site, so it gets `rel=noopener`."""
    code = (SITE / "simulator.js").read_text(encoding="utf-8")
    assert 'setAttribute("rel", "noopener")' in code


def test_one_download_helper_serves_both_files() -> None:
    """The listing and the calendar file take the same route out.

    Two copies of blob-create/click/revoke is where one of them ends up leaking the URL
    it made.
    """
    code = (SITE / "simulator.js").read_text(encoding="utf-8")
    assert code.count("URL.createObjectURL") == 1
    assert code.count("URL.revokeObjectURL") == 1


def test_the_menu_follows_princeton_site_builders_horizontal_pattern() -> None:
    """Values taken from ps_base/css/styles.css, not estimated.

    orfe.princeton.edu wears Site Builder's horizontal menu: a sticky band with a hairline
    above and below, and links carrying a 5px transparent bottom border that fills in for
    the current page. Pinned because "make it look like theirs" is otherwise a judgement
    nobody can check.
    """
    assert "position: sticky" in STYLE
    assert "border-bottom: 5px solid transparent" in STYLE
    assert "font-weight: 600" in STYLE
    for rule in (".menubar", ".menubar-inner", ".menubar-title"):
        assert rule in STYLE, rule


def test_the_menu_bar_is_not_trapped_inside_a_scroll_container() -> None:
    """`overflow-x: hidden` on an ancestor silently kills `position: sticky`.

    Which is why the bar sits outside `main` rather than full-bleeding out of it with
    `100vw` and negative margins — that needs the hidden overflow, and the sticky then
    stops working with nothing to show for it.
    """
    assert "overflow-x: hidden" not in STYLE.split(".scroll")[0]
    for page in PAGES.values():
        nav = page.index('class="menubar"')
        assert nav < page.index("<main>"), "the bar must precede main, not sit inside it"


def test_a_hidden_menu_stays_hidden() -> None:
    """`display: flex` beats the browser's `[hidden] { display: none }`.

    Without the explicit rule the calendar dropdown is permanently open, however
    carefully the script sets the attribute — which is exactly what shipped.
    """
    assert ".deadline-menu[hidden] { display: none; }" in STYLE
    flex_at = STYLE.index(".deadline-menu {")
    hidden_at = STYLE.index(".deadline-menu[hidden]")
    assert hidden_at < flex_at, "the override must not be outranked by source order"
