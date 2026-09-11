"""What the events from a feed may be put toward.

An optional, feed-level declaration with a per-event override. Two properties carry the
weight, and both are about what happens when nobody says anything:

* **Silence means none.** A feed that declares no purpose serves none, and nothing is
  inherited from ``defaults:``. A source silently joining a publication is a worse failure
  than one left out and noticed.
* **A name nothing declares is a load error.** A purpose is matched by name, so a
  misspelled one selects nothing and reports success -- indistinguishable from a filter
  that is simply strict. The same shape as the FPO and renamed-series bugs found earlier.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
import yaml

from tests.support import FIXTURES, REPO_ROOT, TEST_ENV
from upcoming.build import build_from_file, load_pronunciation, resolve_purposes
from upcoming.combine import Combo, combine, load_combos
from upcoming.errors import ConfigFatal
from upcoming.model import Event, to_wire
from upcoming.registry import PurposeOverride, load_registry

SOURCES = REPO_ROOT / "config" / "sources.yaml"
ORFE_FEED = FIXTURES / "feeds" / "orfe" / "feed.ics"


def registry_with(tmp_path, mutate):  # type: ignore[no-untyped-def]
    """The shipped registry with one edit, so only that edit is under test."""
    document = yaml.safe_load(SOURCES.read_text())
    mutate(document)
    target = tmp_path / "sources.yaml"
    target.write_text(yaml.safe_dump(document))
    return target


@pytest.fixture
def orfe(registry):  # type: ignore[no-untyped-def]
    load_pronunciation()
    # `publish_unless` is ORFE's own business and would confuse counts here.
    return replace(next(s for s in registry.live if s.slug == "orfe"), publish_unless=None)


def purposes_of(source):  # type: ignore[no-untyped-def]
    result = build_from_file(ORFE_FEED, source)
    assert result.ok, result.diagnostics
    return [e.purposes for e in result.events]


# --------------------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------------------


def test_a_purpose_is_declared_with_a_label(registry) -> None:  # type: ignore[no-untyped-def]
    assert registry.purposes
    for name, entry in registry.purposes.items():
        assert entry.get("label"), name


def test_a_purpose_without_a_label_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A purpose nobody can name is one nobody can judge a feed against."""
    config = registry_with(tmp_path, lambda d: d["purposes"].update({"mystery": {}}))
    with pytest.raises(ConfigFatal, match="needs a `label`"):
        load_registry(config, env=TEST_ENV)


def test_a_purpose_name_must_be_publishable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """It goes into a record and gets matched by a predicate, so it is slug-shaped."""
    config = registry_with(
        tmp_path, lambda d: d["purposes"].update({"Some Purpose": {"label": "x"}})
    )
    with pytest.raises(ConfigFatal, match="lowercase alphanumeric"):
        load_registry(config, env=TEST_ENV)


def test_a_feed_naming_an_undeclared_purpose_is_a_load_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The failure the vocabulary exists for.

    A source declaring a misspelled purpose would be absent from anything selecting on it,
    the feed would stay valid, and every gate would pass.
    """

    def typo(document):  # type: ignore[no-untyped-def]
        for entry in document["sources"]:
            if entry["slug"] == "orfe":
                entry["purposes"] = ["engineering-newsletters"]

    with pytest.raises(ConfigFatal, match=r"which nothing declares"):
        load_registry(registry_with(tmp_path, typo), env=TEST_ENV)


def test_a_predicate_on_an_undeclared_purpose_is_a_load_error(registry, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The same guard from the consuming side."""
    config = tmp_path / "combos.yaml"
    config.write_text(
        "combos:\n"
        "  - name: ghost\n"
        "    label: Ghost\n"
        "    include: ['*']\n"
        "    where:\n"
        "      purposes:\n"
        "        contains: nothing-declares-this\n"
        "    key: [starts_at, title]\n"
    )
    with pytest.raises(ConfigFatal, match="not a declared purpose"):
        load_combos(
            config,
            known_sources=[s.slug for s in registry.sources],
            tags=registry.sources[0].tags,
            purposes=tuple(registry.purposes),
        )


# --------------------------------------------------------------------------------------
# Optional, and never inherited
# --------------------------------------------------------------------------------------


def test_a_feed_that_declares_nothing_serves_nothing(orfe) -> None:  # type: ignore[no-untyped-def]
    """Optional means optional: no declaration, no purposes, no error."""
    bare = replace(orfe, purposes=(), purpose_overrides=())
    assert set(purposes_of(bare)) == {()}


def test_nothing_is_inherited_from_defaults(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A feed added for some other reason must not acquire a purpose by being added.

    This is the requirement the whole design turns on, so it is asserted against a source
    appended to the real registry rather than against a stub.
    """

    def add(document):  # type: ignore[no-untyped-def]
        assert "purposes" not in document["defaults"], "defaults must not carry purposes"
        document["sources"].append(
            {
                "slug": "somewhere-else",
                "label": "A feed added for some other reason",
                "host": "example.princeton.edu",
                "feed_url": "https://example.princeton.edu/feeds/events/ical.ics",
                "location_rules": ["whole"],
                "expectations": {"summary_role": "title", "min_events": 0, "allow_empty": True},
            }
        )

    registry = load_registry(registry_with(tmp_path, add), env=TEST_ENV)
    assert next(s for s in registry.sources if s.slug == "somewhere-else").purposes == ()


def test_a_declared_feed_stamps_every_one_of_its_events(orfe) -> None:  # type: ignore[no-untyped-def]
    assert set(purposes_of(orfe)) == {("engineering-newsletter",)}


# --------------------------------------------------------------------------------------
# Overriding at the event level
# --------------------------------------------------------------------------------------


def test_an_override_replaces_the_feeds_declaration_for_matching_events(orfe) -> None:  # type: ignore[no-untyped-def]
    """A feed mostly destined somewhere, with a class of events that is not."""
    source = replace(
        orfe,
        purpose_overrides=(
            PurposeOverride(when={"tags": {"contains": "colloquium"}}, purposes=()),
        ),
    )
    result = build_from_file(ORFE_FEED, source)
    assert result.ok
    for event in result.events:
        expected = () if "colloquium" in event.tags else ("engineering-newsletter",)
        assert event.purposes == expected, event.title


def test_an_override_can_assign_a_different_purpose(orfe, registry) -> None:  # type: ignore[no-untyped-def]
    """Not only subtraction: an event may serve something its feed as a whole does not."""
    name = next(iter(registry.purposes))
    source = replace(
        orfe,
        purposes=(),
        purpose_overrides=(
            PurposeOverride(when={"tags": {"contains": "seminar"}}, purposes=(name,)),
        ),
    )
    result = build_from_file(ORFE_FEED, source)
    assert {e.purposes for e in result.events} == {(), (name,)}


def test_the_first_matching_override_wins(orfe) -> None:  # type: ignore[no-untyped-def]
    """Ordered, like location_rules and summary.rules, so precedence is readable."""
    source = replace(
        orfe,
        purpose_overrides=(
            PurposeOverride(when={"tags": {"contains": "seminar"}}, purposes=()),
            PurposeOverride(
                when={"tags": {"contains": "seminar"}}, purposes=("engineering-newsletter",)
            ),
        ),
    )
    result = build_from_file(ORFE_FEED, source)
    seminars = [e for e in result.events if "seminar" in e.tags]
    assert seminars
    assert all(e.purposes == () for e in seminars)


def test_an_override_reads_fields_that_only_exist_after_mapping(orfe) -> None:  # type: ignore[no-untyped-def]
    """`tags` and `series` are produced by mapping, so resolution runs after it.

    Stamping purposes during mapping would leave every tag predicate matching nothing.
    """
    source = replace(
        orfe,
        purpose_overrides=(PurposeOverride(when={"series": {"contains": "Wilks"}}, purposes=()),),
    )
    result = build_from_file(ORFE_FEED, source)
    matched = [e for e in result.events if "Wilks" in e.series]
    assert matched
    assert all(e.purposes == () for e in matched)


def test_resolution_is_a_no_op_without_overrides(orfe) -> None:  # type: ignore[no-untyped-def]
    result = build_from_file(ORFE_FEED, orfe)
    assert resolve_purposes(result.events, orfe) == result.events


def test_an_override_with_no_purposes_key_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """ "Serves none" and "unfinished" must not look alike.

    `purposes: []` says the first out loud; omitting the key could mean either.
    """

    def add(document):  # type: ignore[no-untyped-def]
        for entry in document["sources"]:
            if entry["slug"] == "orfe":
                entry["purpose_overrides"] = [{"when": {"tags": {"contains": "fpo"}}}]

    with pytest.raises(ConfigFatal, match="needs `purposes`"):
        load_registry(registry_with(tmp_path, add), env=TEST_ENV)


def test_an_override_with_no_predicate_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def add(document):  # type: ignore[no-untyped-def]
        for entry in document["sources"]:
            if entry["slug"] == "orfe":
                entry["purpose_overrides"] = [{"purposes": []}]

    with pytest.raises(ConfigFatal, match="needs a `when` predicate"):
        load_registry(registry_with(tmp_path, add), env=TEST_ENV)


def test_an_override_naming_an_undeclared_purpose_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def add(document):  # type: ignore[no-untyped-def]
        for entry in document["sources"]:
            if entry["slug"] == "orfe":
                entry["purpose_overrides"] = [
                    {"when": {"tags": {"contains": "fpo"}}, "purposes": ["invented"]}
                ]

    with pytest.raises(ConfigFatal, match=r"which nothing declares"):
        load_registry(registry_with(tmp_path, add), env=TEST_ENV)


def test_an_override_predicate_is_validated_like_any_other(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def add(document):  # type: ignore[no-untyped-def]
        for entry in document["sources"]:
            if entry["slug"] == "orfe":
                entry["purpose_overrides"] = [
                    {"when": {"tags": {"contains": "not-a-tag"}}, "purposes": []}
                ]

    with pytest.raises(ConfigFatal, match="not a canonical tag"):
        load_registry(registry_with(tmp_path, add), env=TEST_ENV)


# --------------------------------------------------------------------------------------
# The published record
# --------------------------------------------------------------------------------------


def test_the_field_is_published_and_schema_described(orfe) -> None:  # type: ignore[no-untyped-def]
    result = build_from_file(ORFE_FEED, orfe)
    assert to_wire(result.events[0])["purposes"] == ["engineering-newsletter"]

    schema = json.loads((REPO_ROOT / "schema" / "event.schema.json").read_text())
    field = schema["properties"]["purposes"]
    assert field["type"] == "array"
    assert field["uniqueItems"] is True


def test_the_published_feeds_still_validate(registry, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The new field must not have broken the contract it was added to."""
    from jsonschema import Draft202012Validator
    from referencing import Registry as SchemaRegistry
    from referencing import Resource
    from referencing.jsonschema import DRAFT202012

    from upcoming.publish import assemble

    load_pronunciation()
    results = {
        s.slug: build_from_file(FIXTURES / "feeds" / s.slug / "feed.ics", s) for s in registry.live
    }
    tree = assemble(registry, results, {}, {}, root=tmp_path, generated_at="2026-09-11T12:00:00Z")
    event = json.loads(tree.files["schema/event.schema.json"])
    feed = json.loads(tree.files["schema/events.schema.json"])
    store = SchemaRegistry().with_resource(
        event["$id"], Resource.from_contents(event, default_specification=DRAFT202012)
    )
    validator = Draft202012Validator(feed, registry=store)
    for path, body in tree.files.items():
        if path.startswith("feeds/"):
            assert list(validator.iter_errors(json.loads(body))) == [], path


def test_the_manifest_names_each_feeds_purposes(registry, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Answerable at the feed level too, including for a source that is unavailable."""
    from upcoming.publish import assemble

    load_pronunciation()
    results = {
        s.slug: build_from_file(FIXTURES / "feeds" / s.slug / "feed.ics", s) for s in registry.live
    }
    tree = assemble(registry, results, {}, {}, root=tmp_path, generated_at="2026-09-11T12:00:00Z")
    document = json.loads(tree.files["status.json"])
    records = {f["path"]: f for f in document["feeds"]}
    assert records["feeds/orfe/events.json"]["purposes"] == ["engineering-newsletter"]
    assert records["feeds/cs/events.json"]["status"] == "disabled"
    assert records["feeds/cs/events.json"]["purposes"] == ["engineering-newsletter"]


def test_a_merged_record_unions_its_purposes() -> None:
    """An event two units both publish stays eligible for everything either serves.

    Intersecting would drop it from a purpose precisely *because* a second unit also
    listed it, which is the opposite of what a collision should mean.
    """
    shared = {
        "platform": "princeton-site-builder",
        "starts_at": "2026-10-02T16:15:00Z",
        "ends_at": "2026-10-02T17:15:00Z",
        "start_time": "2026-10-02T12:15:00",
        "end_time": "2026-10-02T13:15:00",
        "timezone": "America/New_York",
        "title": "Joint Colloquium",
        "url": "https://example.edu/e",
    }
    combo = Combo(name="pair", label="Pair", include=("orfe", "citp"), key=("starts_at", "title"))
    result = combine(
        combo,
        {
            "orfe": (
                Event(id="orfe:1", guid="1", sources=("orfe",), purposes=("a-purpose",), **shared),
            ),
            "citp": (
                Event(id="citp:2", guid="2", sources=("citp",), purposes=("b-purpose",), **shared),
            ),
        },
    )
    assert result.merged == 1
    assert result.events[0].purposes == ("a-purpose", "b-purpose")


def test_purposes_are_not_part_of_the_merge_comparison() -> None:
    """Two records differing only in purpose are still the same event.

    Comparing on it would turn an editorial label into a reason to publish a duplicate.
    """
    from upcoming.combine import COMPARED

    assert "purposes" not in COMPARED
