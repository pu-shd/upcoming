# Architecture

## The pipeline

One direction, no loops. Each stage is a module and each is pure apart from the two marked.

```
config/*.yaml ──► registry.py ──────────► SourceConfig (frozen, per source)
                                                │
ICS bytes ──► parse.py ──► transform.py ──► resolve_purposes ──► select ──► enrich* ──► fallback
                              │                                              │
                          rules.py                                      scrape.py*
                          locate.py                                     fetch.py*
                          tags.py
                                                │
                                          validate.py (8 gates + JSON Schema)
                                                │
                       serialize.py ──► combine.py ──► publish.py ──► the tree
```

`*` reaches the network. Everything else is a pure function of its inputs, which is why
the whole pipeline runs offline against committed fixtures.

## Stage by stage

| Stage | Module | What it decides |
|---|---|---|
| **Load** | `registry.py` | Merges `defaults` → per-source → env. **Lists replace, never append** — rule order is priority order, so an inherited rule silently landing at position three would be unreasonable to debug. The only module allowed to read the environment. |
| **Parse** | `parse.py` | RFC 5545 unfolding, `\,` unescaping, list splitting. Deliberately not `ics.Calendar`: its `events` is a `set`, which loses feed order. |
| **Map** | `transform.py`, `rules.py` | What `SUMMARY` means. `summary_role` has **no default**; a source that omits it fails to load. |
| **Locate** | `locate.py` | Seven ordered rules turning `101 Sherrerd Hall` into a venue and a room. Declines rather than guessing when both ends look like rooms. |
| **Tag** | `tags.py` | Exact alias lookup against a canonical vocabulary. No stemming, no fuzzy matching. |
| **Purpose** | `build.resolve_purposes` | Applies per-event overrides over the feed's declaration. After mapping, because the predicates read `tags` and `series`. |
| **Select** | `build.select` | The source's own `publish_where` / `publish_unless`. Before enrichment, so an event we are not publishing costs nobody a page fetch. |
| **Enrich** | `enrich.py`, `scrape.py`, `fetch.py` | Scrapes declared targets. Reuses the last scrape inside `rebuild_after_hours`. |
| **Fallback** | `fallback.py` | Synthesizes a title where none exists and flags it `titleIsPlaceholder`. |
| **Gate** | `validate.py` | Eight gates plus JSON Schema. Failures fail the source; warnings publish with a note. |
| **Combine** | `combine.py`, `predicate.py` | Set algebra over the per-source feeds. Merges only on a total detail match. |
| **Publish** | `publish.py`, `serialize.py` | Assembles the tree and `status.json`. All escaping lives in `serialize.py`. |

## Why the stage order is what it is

Three orderings are load-bearing and a change to any of them is a behaviour change:

**Enrichment runs after mapping and before fallback.** A title scraped off the page beats
a synthesized one, and the fallback only fills what is still missing. The predecessor
leaves this implicit across two entry points that have twice drifted apart.

**Selection runs after mapping and before enrichment.** Tags and series do not exist before
mapping, so a predicate on either would have nothing to read. And an event we will not
publish should not cost a page fetch — for ORFE that is four requests saved every half
hour.

**Purposes resolve after mapping** for the same reason: stamping them earlier would leave
every tag predicate matching nothing.

## Shared vocabularies

Four modules exist because the same knowledge was being written down in several places.
Each was extracted after a concrete defect, not on principle:

| Module | Extracted because |
|---|---|
| `clock.py` | `"%Y-%m-%dT%H:%M:%SZ"` was hardcoded in four modules — including the watchdog, which *parses* what publishing writes. Producer and consumer of one string, defined separately. |
| `config.py` | Five loaders had grown the same eight lines. The unknown-key check is the part that turns a typo into a load error, and the registry was missing it. |
| `predicate.py` | Combined feeds and per-source filters both declare predicates. Two dialects that start identical and drift is the failure to avoid. |
| `serialize.py` | Escaping is a property of the wire format, not of the event. The predecessor keeps it in the model under one boolean that is right for ORFE and wrong for MAE. |

## Data model

`Event` is a frozen dataclass with `slots`. Two properties matter:

**Identity is source-scoped.** `id` is `"{source}:{guid}"`. `ps_events` UIDs are per-site
sequences: `ps_events:4056:delta:0` is `ai`'s "ORFE Colloquium" *and* `materials`'
"Materials Institute Symposium". 12 of 125 nids collide. A dedupe key naming `guid` alone
is refused at load.

**Derived fields are computed, never stored.** `speaker` and `affiliation` are properties
over `speakers[]` and cannot fall out of step with it. The loader refuses an enrichment
target naming either, since scraping into the scalar would keep one speaker and drop the
rest.

## Configuration layering

```
defaults:              # what every source shares
  location_rules: [...]
  enrich: [...]
sources:
  - slug: orfe
    location_rules: [...]   # REPLACES the default list entirely
```

Lists replace rather than append, everywhere. Extending a default means restating it,
which is explicit and reviewable in the diff. The alternative — appending — puts a rule at
a position nobody chose, and rule order decides which rule wins.

Every vocabulary is closed and checked by name at load: tags, purposes, patterns, location
rules, escapable fields, predicate operators. A misspelled one is a load error, because a
predicate on a value nothing produces filters to nothing and reports success.

## The published site

`site/` is static and published verbatim by `publish.site_files()`. Two pages share one
stylesheet. The simulator's JavaScript reads `status.json` for feed discovery and holds no
list of sources — the predecessors' equivalent is byte-identical between two repositories
with the department baked in.

## Where the boundaries are

| Boundary | Enforced by |
|---|---|
| Only `registry.py` reads the environment | `test_only_the_registry_reads_the_environment` |
| Only `build.build_from_text` catches bare `Exception` | `test_bare_exception_handlers_stay_at_the_build_boundary`, with an allow-list carrying reasons |
| `model.py` does not import the registry | `test_the_model_does_not_import_the_registry` |
| `provenance.py` has no intra-package imports | `test_provenance_has_no_intra_package_imports` |
| Only `config.py` calls `yaml.safe_load` | `test_every_config_loader_goes_through_the_shared_reader` |
| Every declared dependency is imported | `test_every_declared_dependency_is_actually_imported`, parsing `pyproject.toml` against every module's AST |
| The Makefile stays macOS-portable | `test_contracts.py` — no `sha256sum`, `getent`, `grep -P` or bare `sed -i` |

**Not** structurally enforced, though it holds: *no module branches on a source slug.* What
proves it is that all twelve live sources build from one codebase with nothing differing
but `config/sources.yaml` (`test_every_live_source_builds`). A grep-based assertion was
considered and rejected as easy to satisfy without being true.
