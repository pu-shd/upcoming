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


def test_the_page_reaches_no_external_host() -> None:
    """Self-contained, so it cannot break because someone else's CDN did.

    It also means the page works from a local `make publish` tree with no network.
    """
    for url in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', INDEX):
        if url.startswith(("http://", "https://")):
            assert "github.com/pu-shd/upcoming" in url, f"external resource: {url}"


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
        "never de-duplicate",  # the per-source contract
        "sources",  # the array that names a merged record's origins
        "source-scoped",  # why `id` rather than `guid`
        "status.json",  # the freshness contract
        "stale",
    ],
)
def test_the_page_states_the_contracts_a_consumer_needs(claim: str) -> None:
    """These are the four things a consumer gets wrong if nobody tells them."""
    assert claim in INDEX


def test_the_page_declares_a_viewport_and_a_language() -> None:
    assert 'name="viewport"' in INDEX
    assert re.search(r'<html[^>]+lang="en"', INDEX)


def test_wide_content_scrolls_inside_its_own_container() -> None:
    """The tables are wide and the body must never scroll sideways on a phone."""
    assert "overflow-x: auto" in INDEX
    assert INDEX.count('class="card scroll"') >= 3


def test_the_page_works_in_both_themes() -> None:
    assert "prefers-color-scheme: dark" in INDEX
    assert "color-scheme: light dark" in INDEX


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
