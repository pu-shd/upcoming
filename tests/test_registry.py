"""Registry loading, layering, and the refusals that keep silence from meaning success.

Most of these tests assert that something *fails*. That is the design: the failure mode
this project exists to prevent is a run that produces schema-valid, wrong output and
reports success, so the loader's job is to refuse ambiguous configuration rather than pick
a plausible default.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.support import TEST_ENV
from upcoming.errors import ConfigFatal
from upcoming.registry import STATUS_LIVE, STATUS_UNAVAILABLE, load_registry

MINIMAL = """
defaults:
  platform: princeton-site-builder
  url_template: "https://{host}/feeds/events/ical.ics"
  location_rules: [room_token, whole]
sources:
  - slug: alpha
    host: alpha.example.edu
    expectations:
      summary_role: title
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# The real registry
# --------------------------------------------------------------------------------------


def test_the_committed_registry_loads(registry) -> None:
    assert registry.sources, "the committed registry should declare sources"


def test_every_source_declares_what_summary_means(registry) -> None:
    """The fact the predecessor's fork got wrong. Stated, never inferred."""
    for source in registry.sources:
        assert source.expectations.summary_role in {"speaker", "title", "composite", "mixed"}


def test_orfe_and_mae_declare_opposite_summary_roles(registry) -> None:
    """The inversion, pinned.

    If these two ever agree, either a mapping has been broken or this test has stopped
    meaning anything -- both worth a failure.
    """
    assert registry.by_slug("orfe").expectations.summary_role == "speaker"
    assert registry.by_slug("mae").expectations.summary_role == "title"


def test_unavailable_sources_have_a_recorded_reason(registry) -> None:
    """A gap with no explanation gets re-investigated by the next person."""
    for source in registry.sources:
        if source.status == STATUS_UNAVAILABLE:
            assert len(source.reason) > 40, f"{source.slug} needs a substantive reason"


def test_unavailable_sources_are_never_fetchable(registry) -> None:
    for source in registry.sources:
        if source.status == STATUS_UNAVAILABLE:
            assert source.feed_url is None, f"{source.slug} is unavailable but has a feed_url"


def test_live_sources_are_fetchable_and_have_a_location_chain(registry) -> None:
    live = registry.live
    assert len(live) >= 12, f"expected at least 12 live sources, found {len(live)}"
    for source in live:
        assert source.feed_url, f"{source.slug} is live with no feed_url"
        assert source.location_rules, f"{source.slug} is live with no location rules"


def test_kellercenter_is_declared_allowed_empty_on_its_own_platform(registry) -> None:
    """Valid-and-empty must be distinguishable from broken.

    The two payloads are byte-identical in shape, so only this declaration knows which it
    is -- and a count floor it can never satisfy would leave it permanently red, which is
    how people learn to ignore alerts.
    """
    keller = registry.by_slug("kellercenter")
    assert keller.status == STATUS_LIVE
    assert keller.expectations.allow_empty is True
    assert keller.expectations.min_events == 0
    assert keller.platform == "drupal-ical-api"
    assert keller.uid_pattern is None, (
        "kellercenter must not inherit Site Builder's UID pattern: its scheme is unknown"
    )


def test_quantum_declares_a_wide_placeholder_band(registry) -> None:
    """78% of quantum's events carry a TBD title, and that is normal for this feed."""
    quantum = registry.by_slug("quantum")
    assert quantum.expectations.max_placeholder_title_rate >= 0.9


def test_location_rule_order_puts_the_dash_rule_before_room_tokens(registry) -> None:
    """Order is load-bearing, not cosmetic.

    ORFE writes "125 - Sherrerd Hall". A room-token rule seeing that string first would
    split on the leading "125" and produce name="125 - Sherrerd", so the dash rule has to
    win. This asserts the ordering rather than trusting whoever edits the chain next.
    """
    rules = registry.by_slug("orfe").location_rules
    assert "detail_dash_name" in rules
    assert rules.index("detail_dash_name") < rules.index("room_token")


def test_every_location_chain_ends_by_declining(registry) -> None:
    """`whole` last means an unsplittable venue lands in `name` entire.

    "Commons" and "Maeder Hall Auditorium" have no room component. Guessing a split would
    put a building name into the room field, which is worse than declining.
    """
    for source in registry.live:
        assert source.location_rules[-1] == "whole", (
            f"{source.slug}'s chain must end with `whole` so it declines rather than guesses"
        )


def test_no_two_sources_share_a_feed_url(registry) -> None:
    """Clone-and-retarget, in config form.

    Two sources on one feed would publish the same events twice under different source
    names, manufacturing the cross-source duplicates this design works to represent
    honestly. A derived view belongs in combos.
    """
    urls = [s.feed_url for s in registry.sources if s.feed_url]
    assert len(urls) == len(set(urls))


def test_enrichment_is_opt_in(registry) -> None:
    """A source starts with no enrichment and is switched on once selectors are verified.

    Measured: `event-subtitle` exists on only 5 of 11 hosts and the speaker-name field on
    only 4, so a shared default would scrape with selectors nobody checked and silently
    populate nothing.
    """
    enriching = [s.slug for s in registry.live if s.enrichment_enabled]
    assert enriching, "at least one source should have verified selectors by now"
    assert len(enriching) < len(registry.live), "enrichment should not be on by default"


def test_provenance_is_only_claimed_for_the_title_field(registry) -> None:
    """Scraping into `speaker` says nothing about where the title came from."""
    for source in registry.sources:
        for target in source.enrich:
            if target.provenance is not None:
                assert target.field_name == "title"


def test_mae_scrapes_the_speaker_without_claiming_title_provenance(registry) -> None:
    targets = {t.field_name: t for t in registry.by_slug("mae").enrich}
    assert "speaker" in targets
    assert targets["speaker"].provenance is None


def test_403_is_not_retryable(registry) -> None:
    """A bot challenge is not a transient fault.

    Retrying it burns the request budget while the actual fix is a credential.
    """
    for source in registry.live:
        assert 403 not in source.http.retry_on


def test_selectors_stay_ordered_sequences(registry) -> None:
    """Priority order must survive as a list, not collapse into a CSS comma group.

    One comma-joined selector string would be resolved by the parser in *document* order,
    not the priority order written here -- so a page carrying both shapes would answer with
    whichever appears first in the markup.
    """
    for source in registry.sources:
        for target in source.enrich:
            assert isinstance(target.selectors, tuple)
            for selector in target.selectors:
                assert "," not in selector, (
                    f"{source.slug}: {selector!r} bundles alternatives; list them separately"
                )


# --------------------------------------------------------------------------------------
# The refusals
# --------------------------------------------------------------------------------------


def test_a_source_without_summary_role_is_refused(tmp_path: Path) -> None:
    """No default. This is the whole anti-silence commitment.

    Every possible default is wrong for roughly half these feeds, and wrong invisibly,
    because the output still validates with title and speaker transposed.
    """
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
        sources:
          - slug: alpha
            host: alpha.example.edu
        """,
    )
    with pytest.raises(ConfigFatal, match="summary_role"):
        load_registry(path, env=TEST_ENV)


def test_an_unknown_summary_role_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
        sources:
          - slug: alpha
            host: alpha.example.edu
            expectations:
              summary_role: whatever
        """,
    )
    with pytest.raises(ConfigFatal, match="not one of"):
        load_registry(path, env=TEST_ENV)


def test_a_missing_secret_fails_the_load_rather_than_defaulting(tmp_path: Path) -> None:
    """The bypass credential has no fallback, on purpose.

    The predecessors ship `os.getenv("BOT_BYPASS_HEADER_VALUE", "1")` and an inline
    `|| '1'` in CI. With the secret unset they send the placeholder, get 403 on every event
    page, scrape nothing, and report success. Failing here is the fix.
    """
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
          http:
            headers:
              x-wdsoit-bot-bypass: "${secret:BOT_BYPASS_TOKEN}"
        sources:
          - slug: alpha
            host: alpha.example.edu
            enrich:
              - field: title
                selectors: ["div.event-subtitle"]
            expectations:
              summary_role: title
        """,
    )
    with pytest.raises(ConfigFatal, match="BOT_BYPASS_TOKEN"):
        load_registry(path, env={})


def test_an_empty_secret_is_treated_as_missing(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
          http:
            headers:
              x-wdsoit-bot-bypass: "${secret:BOT_BYPASS_TOKEN}"
        sources:
          - slug: alpha
            host: alpha.example.edu
            enrich:
              - field: title
                selectors: ["div.event-subtitle"]
            expectations:
              summary_role: title
        """,
    )
    with pytest.raises(ConfigFatal, match="BOT_BYPASS_TOKEN"):
        load_registry(path, env={"BOT_BYPASS_TOKEN": ""})


def test_a_source_without_enrichment_loads_without_the_secret(tmp_path: Path) -> None:
    """The credential is needed for event pages, not for the feed.

    Measured: every ICS endpoint answers a bare request; every event page 403s without the
    header. So requiring the secret from a source that never scrapes would be a false
    barrier -- and would make the whole registry unloadable in a fixture-only test run.
    """
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
          http:
            headers:
              x-wdsoit-bot-bypass: "${secret:BOT_BYPASS_TOKEN}"
        sources:
          - slug: alpha
            host: alpha.example.edu
            expectations:
              summary_role: title
        """,
    )
    result = load_registry(path, env={})
    assert result.by_slug("alpha").is_live


def test_the_resolved_secret_reaches_the_http_headers(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
          http:
            headers:
              x-wdsoit-bot-bypass: "${secret:BOT_BYPASS_TOKEN}"
        sources:
          - slug: alpha
            host: alpha.example.edu
            enrich:
              - field: title
                selectors: ["div.event-subtitle"]
            expectations:
              summary_role: title
        """,
    )
    source = load_registry(path, env={"BOT_BYPASS_TOKEN": "real-value"}).by_slug("alpha")
    assert source.http.headers["x-wdsoit-bot-bypass"] == "real-value"


def test_the_fingerprint_hashes_the_reference_not_the_secret_value(tmp_path: Path) -> None:
    """The fingerprint is published in build state and drives the rebuild gate.

    It must change when the configuration changes and stay stable when a credential
    rotates -- otherwise rotating a token would look like a config edit and force a
    needless rebuild of every source, and the published state would leak the token's
    identity.
    """
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
          http:
            headers:
              x-wdsoit-bot-bypass: "${secret:BOT_BYPASS_TOKEN}"
        sources:
          - slug: alpha
            host: alpha.example.edu
            enrich:
              - field: title
                selectors: ["div.event-subtitle"]
            expectations:
              summary_role: title
        """,
    )
    first = load_registry(path, env={"BOT_BYPASS_TOKEN": "one"}).by_slug("alpha")
    second = load_registry(path, env={"BOT_BYPASS_TOKEN": "two"}).by_slug("alpha")
    assert first.fingerprint() == second.fingerprint()
    assert "one" not in str(first.raw) and "two" not in str(second.raw)


def test_editing_a_rule_changes_the_fingerprint(tmp_path: Path) -> None:
    """The other half of the gate.

    Keying a rebuild on the feed hash alone -- which is what the predecessor does -- means
    an edited mapping silently never takes effect while upstream is quiet.
    """
    before = load_registry(write(tmp_path, MINIMAL), env=TEST_ENV).by_slug("alpha")
    after = load_registry(
        write(tmp_path, MINIMAL.replace("[room_token, whole]", "[comma_room, whole]")),
        env=TEST_ENV,
    ).by_slug("alpha")
    assert before.fingerprint() != after.fingerprint()


def test_two_sources_sharing_a_feed_url_are_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        defaults:
          location_rules: [whole]
        sources:
          - slug: alpha
            feed_url: "https://example.edu/feeds/events/ical.ics"
            expectations:
              summary_role: title
          - slug: beta
            feed_url: "https://example.edu/feeds/events/ical.ics"
            expectations:
              summary_role: title
        """,
    )
    with pytest.raises(ConfigFatal, match="share feed_url"):
        load_registry(path, env=TEST_ENV)


def test_an_unavailable_source_without_a_reason_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        sources:
          - slug: alpha
            status: unavailable
            expectations:
              summary_role: title
        """,
    )
    with pytest.raises(ConfigFatal, match="records no reason"):
        load_registry(path, env=TEST_ENV)


def test_an_enrich_target_without_selectors_is_refused(tmp_path: Path) -> None:
    """Enrichment with no selectors would scrape every page and populate nothing."""
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
        sources:
          - slug: alpha
            host: alpha.example.edu
            enrich:
              - field: title
                selectors: []
            expectations:
              summary_role: title
        """,
    )
    with pytest.raises(ConfigFatal, match="no selectors"):
        load_registry(path, env=TEST_ENV)


def test_claiming_title_provenance_for_another_field_is_refused(tmp_path: Path) -> None:
    """MAE's insight, enforced by the loader instead of by a code comment."""
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
        sources:
          - slug: alpha
            host: alpha.example.edu
            enrich:
              - field: speaker
                selectors: ["div.speaker"]
                provenance: enriched
            expectations:
              summary_role: title
        """,
    )
    with pytest.raises(ConfigFatal, match="provenance"):
        load_registry(path, env=TEST_ENV)


def test_a_live_source_without_location_rules_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        sources:
          - slug: alpha
            feed_url: "https://alpha.example.edu/feeds/events/ical.ics"
            expectations:
              summary_role: title
        """,
    )
    with pytest.raises(ConfigFatal, match="location_rules"):
        load_registry(path, env=TEST_ENV)


def test_a_duplicate_slug_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
        sources:
          - slug: alpha
            host: one.example.edu
            expectations:
              summary_role: title
          - slug: alpha
            host: two.example.edu
            expectations:
              summary_role: title
        """,
    )
    with pytest.raises(ConfigFatal, match="duplicate source slug"):
        load_registry(path, env=TEST_ENV)


def test_an_unknown_top_level_key_is_refused(tmp_path: Path) -> None:
    """The predecessor's loader reads 7 of 12 declared fields and ignores the rest in
    silence, so a knob set in the config file does nothing at all. Refusing unknown keys is
    the structural fix."""
    path = write(tmp_path, MINIMAL + "\nsorces: []\n")
    with pytest.raises(ConfigFatal, match="unknown top-level keys"):
        load_registry(path, env=TEST_ENV)


def test_a_missing_registry_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigFatal, match="no source registry"):
        load_registry(tmp_path / "absent.yaml", env=TEST_ENV)


def test_malformed_yaml_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text("sources: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigFatal, match="not valid YAML"):
        load_registry(path, env=TEST_ENV)


# --------------------------------------------------------------------------------------
# Layering
# --------------------------------------------------------------------------------------


def test_a_source_overrides_a_default(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
          timezone: America/New_York
        sources:
          - slug: alpha
            host: alpha.example.edu
            timezone: America/Chicago
            expectations:
              summary_role: title
        """,
    )
    assert load_registry(path, env=TEST_ENV).by_slug("alpha").timezone == "America/Chicago"


def test_lists_replace_rather_than_append(tmp_path: Path) -> None:
    """An inherited rule landing in an arbitrary position would change which rule wins.

    Rule order decides the outcome, so extending a chain has to be explicit: restate it.
    """
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [detail_dash_name, comma_room, whole]
        sources:
          - slug: alpha
            host: alpha.example.edu
            location_rules: [room_token, whole]
            expectations:
              summary_role: title
        """,
    )
    assert load_registry(path, env=TEST_ENV).by_slug("alpha").location_rules == (
        "room_token",
        "whole",
    )


def test_null_unsets_an_inherited_value(tmp_path: Path) -> None:
    """Distinct from absent: kellercenter must actively *not* inherit a UID pattern."""
    path = write(
        tmp_path,
        """
        defaults:
          url_template: "https://{host}/feeds/events/ical.ics"
          location_rules: [whole]
          uid_pattern: '^ps_events:\\d+:delta:0$'
        sources:
          - slug: alpha
            host: alpha.example.edu
            uid_pattern: null
            expectations:
              summary_role: title
        """,
    )
    assert load_registry(path, env=TEST_ENV).by_slug("alpha").uid_pattern is None


def test_an_allowlisted_env_override_applies(tmp_path: Path) -> None:
    """Quarantining a broken source should not need a commit."""
    path = write(tmp_path, MINIMAL)
    env = dict(TEST_ENV, UPCOMING_ALPHA_STATUS="unavailable", UPCOMING_ALPHA_FEED_URL="")
    with pytest.raises(ConfigFatal, match="records no reason"):
        load_registry(path, env=env)


def test_env_can_repoint_a_feed_at_a_fixture(tmp_path: Path) -> None:
    path = write(tmp_path, MINIMAL)
    env = dict(TEST_ENV, UPCOMING_ALPHA_FEED_URL="file:///tmp/alpha.ics")
    assert load_registry(path, env=env).by_slug("alpha").feed_url == "file:///tmp/alpha.ics"


def test_env_cannot_change_what_a_field_means(tmp_path: Path) -> None:
    """The decision the predecessor put in a repository variable.

    `ENRICH_TARGET_FIELD` as an environment variable means the single most failure-prone
    choice in the system lives somewhere invisible to review, untested, and reproduced in a
    fresh clone only if someone remembered to inline a default. It belongs in the repo.
    """
    path = write(tmp_path, MINIMAL)
    env = dict(TEST_ENV, UPCOMING_ALPHA_SUMMARY_ROLE="speaker")
    with pytest.raises(ConfigFatal, match="not an overridable key"):
        load_registry(path, env=env)


def test_env_cannot_change_selectors(tmp_path: Path) -> None:
    path = write(tmp_path, MINIMAL)
    env = dict(TEST_ENV, UPCOMING_ALPHA_ENRICH_SELECTORS="div.anything")
    with pytest.raises(ConfigFatal, match="not an overridable key"):
        load_registry(path, env=env)
