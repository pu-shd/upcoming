# Testing

1029 tests. `make test` locally, `make docker-test` for the path CI runs.

Two rules shape all of it:

1. **Silence is never success.** A test that skips itself when a tool is missing reports
   green while checking nothing. Node is installed in CI and in the dev image so the
   JavaScript checks actually run.
2. **A test states the failure it prevents.** Most docstrings here name a real defect —
   several from the predecessor's published output, several found by these tests.

## The suites

| File | Tests | Covers |
|---|---:|---|
| `test_simulator.py` | 151 | The simulator's JavaScript, through Node: dates, editions, both listing templates, the export, the calendar file |
| `test_patterns.py` | 107 | Every named regex's `match` and `no_match` table; `person_name_shape` against 34 real names and 18 real titles |
| `test_locate.py` | 66 | The seven location rules, including where they decline and where a chain omits one |
| `test_registry.py` | 66 | Config loading, merging, and every refusal |
| `test_site.py` | 63 | Both published pages: resources, themes, navigation, controls, and the export's style switcher |
| `test_architecture.py` | 55 | Structural boundaries — see below |
| `test_publish.py` | 51 | The tree, `status.json`, staleness, cadence |
| `test_purposes.py` | 44 | The purpose vocabulary, per-event overrides, the schedules and templates a publication declares, and the export layouts |
| `test_rules.py` | 38 | The mapping engine's predicates, actions and attribution |
| `test_model.py` | 35 | The event record, wire round-trip, derived fields |
| `test_verify.py` | 34 | The watchdog's findings and their severity |
| `test_workflows.py` | 33 | The CI YAML itself — see below |
| `test_contracts.py` | 29 | Cross-cutting promises: credentials, dependencies, CLI surface |
| `test_enrich.py` | 29 | Scraping, rejection, health rates |
| `test_upstream.py` | 29 | Conditional requests and enrichment windows |
| `test_combine.py` | 27 | Set algebra, merging, divergence |
| `test_parse.py` | 27 | RFC 5545 unfolding and escaping |
| `test_provenance.py` | 26 | `titleSource`, `titleIsPlaceholder`, what counts as missing |
| `test_heartbeat.py` | 22 | Keepalive thresholds and stopped-schedule detection |
| `test_build.py` | 21 | End-to-end assembly per source |
| `test_shared.py` | 21 | `clock.py` and `config.py`, and that nothing bypasses them |
| `test_differential.py` | 20 | This pipeline against the predecessor's goldens |
| `test_tags.py` | 18 | Canonical tags and alias lookup |
| `test_select.py` | 17 | Per-source `publish_where` / `publish_unless` |

## The kinds of test, and why each exists

**Table tests.** Every named pattern carries `match` *and* `no_match` examples, run as a
parametrized test. A pattern with no negative examples fails the suite: the risk with these
shapes is never failing to match, it is matching something confidently and wrongly.

**Distribution tests.** Each source pins the exact count of events per mapping rule. A
reordering that reclassifies four events still produces schema-valid output; the recorded
counts are the only thing that catches it.

**Differential tests.** The pipeline runs against `pubino/mae-upcoming`'s golden pairs —
real ICS in, a real department's expected JSON out. Every divergence is a named test that
first asserts the golden really contains what it diverges from, so a "divergence" cannot be
a misreading.

The simulator has two of its own, against newsletters that actually went out: the
engineering issue of 7–14 September 2026 and the DaIS issue of 24 September. Two rather
than one on purpose — with a single edition, nothing distinguishes a mechanism driven by a
declared schedule and template from that publication's shape written into the page. The
second fixture's events, window, layout and punctuation all differ, and it passes through
the same code path.

**Structural tests** (`test_architecture.py`, `test_contracts.py`). Boundaries a reviewer
would otherwise have to hold in their head: only `registry.py` reads the environment, only
one function catches bare `Exception`, `model.py` does not import the registry, every
config loader goes through one reader, every declared dependency is imported, and the
Makefile stays macOS-portable.

**Workflow tests** (`test_workflows.py`). YAML can only be exercised by pushing, which
makes it the least reviewable part of a repository. These assert what has actually gone
wrong: the crons do not overlap, the deploy is its own job, it runs even when a source
failed, every job has a timeout, lint cannot block a publish, and **no step reads `$?`
after a command** — the default shell is `bash -e`, so that branch never runs.

**Both-directions tests.** The export's style map and the markup its templates emit are
checked against each other, per template: a class with no rule leaves the page unstyled,
and a rule with no class is dead weight that makes the next rename look already handled.
The DaIS layout shipped with *no* rules at all — the preview took the site's stylesheet
and looked right, while the exported copy went out as bare paragraphs — because the
styling tests rendered one template and only that.

**JavaScript tests.** `site/simulator.js` exports its pure functions under
`module.exports` and guards its DOM wiring behind `typeof document`, so pytest can drive it
through Node. `tests/fixtures/newsletter/tiny-dom.js` is a DOM faithful in the one respect
that matters — `appendChild` and `replaceChildren` *move* a node — which is what a previous
stub could not model, and why a broken export shipped.

## Fixtures

| Path | What it is |
|---|---|
| `tests/fixtures/feeds/<slug>/feed.ics` | A captured ICS per source. The whole pipeline runs offline against these. |
| `tests/fixtures/pages/` | Trimmed event-page captures plus a `manifest.json` mapping real URLs to them |
| `tests/fixtures/golden/` | The predecessor's published output, for the differential tests |
| `tests/fixtures/newsletter/2026-09-08-edition.json` | The engineering newsletter of 7–14 September 2026, transcribed from its Mailchimp export with every field verified against that file |
| `tests/fixtures/newsletter/2026-09-24-dais-edition.json` | The DaIS newsletter of 24 September 2026, from the email. Records what we cannot produce as well as what we can, each with its reason |
| `tests/fixtures/newsletter/tiny-dom.js` | The DOM shim |

## Two global guards

`conftest.py` installs both automatically:

- **Network is blocked at the socket layer** unless a test is marked `@pytest.mark.live`.
  The predecessor patches its fetch helper per test, which only protects the tests that
  remember to.
- **Pipeline environment variables are cleared** before every test. A value exported in a
  developer's shell would otherwise leak into assertions and make a failure look like a
  code regression.

## Adding a test

Put it beside its subject and say what it prevents. A docstring reading "tests the parser"
is worth less than one naming the input that used to break it.

For a new source, the minimum is a captured fixture at
`tests/fixtures/feeds/<slug>/feed.ics` — the existing suites pick it up and will exercise
building, gating and schema validation without further work.

## Running one thing

```sh
.venv/bin/python -m pytest tests/test_locate.py -q        # one file
.venv/bin/python -m pytest -k "placeholder" -q            # by name
.venv/bin/python -m pytest -q -x                          # stop at the first failure
```

Run each gate separately and read its exit code. Chaining them behind `&&` with output
suppressed is how two lint findings once reached CI:

```sh
ruff check . ; echo "exit=$?"
ruff format --check . ; echo "exit=$?"
mypy upcoming ; echo "exit=$?"
pytest ; echo "exit=$?"
```
