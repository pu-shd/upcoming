# Upcoming

Campus event feeds pulled from what departments, units, and centers advertise.

This project is a heavy refactor of [pu-orfe/upcoming](https://github.com/pu-orfe/upcoming), informed by
[pubino/mae-upcoming](https://github.com/pubino/mae-upcoming).

Developer documentation is in [`docs/`](docs/) — [architecture](docs/architecture.md),
[failure modes](docs/failure-modes.md), [testing](docs/testing.md),
[CI/CD](docs/ci-cd.md), and a [roadmap](docs/roadmap.md).

## Implementation

This project attempts to satisfy a consistent schema from event publishers' practice of putting **the same information in different fields**.

For example, `orfe` writes the *speaker* into the ICS `SUMMARY` and puts the talk title on the event page; `mae` does the exact reverse.

So running one department's mapping against the other's feed produces output that is schema-valid and error-free but incorrect.

To address this:

- **`expectations.summary_role` has no default.** The role the `SUMMARY` field plays must be configured per-source, otherwise the feed fails to load with options for `speaker`, `title`, and `rules` for defining how to interpret more complex usage.
- **Mapping decisions are recorded on the event.** Fields like `mappingRules`, `locationRule`, `titleSource`, `summaryRaw` help answer questions like "which field did `SUMMARY` go to?"
- **If a decision cannot be made, the code gives up.** Text a rule cannot classify is parked in `summaryRest` and counted, never assigned to a plausible-looking but potentially incorrect field.

Run `make sources` to print a table to see how a source feed is configured:

```
SOURCE          STATUS       SUMMARY IS  CADENCE  ENRICH                FEED
orfe            live         speaker     30m      title,raw_details     https://orfe.princeton.e…
mae             live         title       1h       speakers,raw_details  https://mae.princeton.ed…
ai              live         mixed       1h       -                     https://ai.princeton.edu…
bioengineering  live         title       1h       speakers,raw_details  https://bioengineering.p…
```

## Sources

19 total: 14 live, 5 recorded as unavailable.

| | |
|---|---|
| **Live, Princeton Site Builder** | `orfe` (23 events), `citp` (24), `dais` (22), `quantum` (18), `materials` (16), `ece` (12), `cbe` (10), `mae` (10), `ai` (7), `nam` (6), `bioengineering` (3), `cee` (1), `robotics` (1) |
| **Live, different platform** | `kellercenter` — `PRODID:-//Drupal iCal API//EN`, currently a well-formed calendar with **zero events** |
| **Declared unavailable** | `nextg`, `cs`, `acee`, `decenter`, `metro` |

The design accommodates 4 main inconsistencies:

- **`SUMMARY` is not consistent, even within one feed.** For example, `ai` carries a bare speaker name, a
  full talk title, *and* a series label across different events, so it is declared as a `mixed` field.
- **Location conventions mix within one feed.** `cbe` writes both `E105 SEAS-BioE` and `SEAS-CBE F212`, so the room rule splits only when exactly one end of the string looks
  like a room, and declines otherwise.
- **`TBD` is common, not exceptional.** `TBD` is treated as absence — publishing it would satisfy the schema's `minLength: 1` and be wrong.
- **An event can have several speakers.** `speakers[]` is canonical, and the scalar `speaker` and `affiliation` are derived
  properties published alongside it so the existing ingest keeps working. `affiliation` is
  empty when the speakers disagree.
- **Empty is not the same as broken.** `kellercenter` is legitimately empty. Only its
  `allow_empty` declaration knows that, because the two payloads are identical.

Each unavailable source records *why*, so nobody repeats the investigation — run
`make check` to read them.

## Usage

```sh
make install           # create .venv and install with dev extras
make sources           # the resolved registry: what each SUMMARY field means
make check             # validate the registry; non-zero on any problem
make build SOURCE=orfe # build one source from its committed fixture
make build-fixtures    # build orfe and mae -- the inversion, both ways
make publish           # the whole tree into dist/: every source, every combo, status.json
make verify            # check what the published site is actually serving
make test              # pytest
make lint typecheck    # ruff + mypy
make docker-test       # the suite in the container (the path CI runs)
```

Requires Python 3.12+. Docker Compose is invoked as `docker-compose` (Homebrew).

## The Worst Case: When one field means several things

Four feeds pack several fields into `SUMMARY`, or mean different things by it on different events of the same feed. They declare an ordered rule chain instead of a single role. First match wins.

```yaml
summary:
  on_no_match: fail            # there is no silent option
  rules:
    - id: speaker-dash-title
      when: { matches: person_dash_title }
      then: { capture: { speakers: person, title: title } }
    - id: bare-person-name
      when: { predicate: person_name_shape }
      then: { assign: { speakers: summary }, defer: [title] }
    - id: title
      when: { always: true }
      then: { assign: { title: summary } }
```

Two layers, deliberately separate:

- **Named patterns** live in `config/patterns.yaml` and are shared by every source. Each carries `match` *and* `no_match` examples, run as a table test. The risk with these shapes is never failing to match — it is matching something confidently and wrongly.
- **Rule chains** are per source, because which patterns apply and in what order is a property of one department's conventions.

The engine is small on purpose: no arithmetic, no computed values, no cross-event state, and predicate nesting stops at depth two. Anything needing more gets a *name* — a pattern, or a Python predicate — which config selects but cannot define.

Every event records which rule mapped it, and each source's test pins the exact distribution. A reordering that moves four events between rules still produces schema-valid output; the recorded counts are the only thing that catches it.

The hardest case parses correctly:

```
Princeton Quantum Colloquium: An Astonishing Quantum Universe in Two Dimensions: From
Fundamental Physics to (Possibly) Quantum Computation, Jainendra Jain (Penn State University)
```

The series ends at the first colon. The title keeps its own colon and its own parenthetical. The speaker and affiliation come from the last one. Elsewhere in the same feed, `University of Colorado, Boulder` survives its comma intact.

## Reading the event pages

Some fields live on the event page rather than in the feed — ORFE's talk titles, MAE's speakers.

```sh
make build SOURCE=orfe            # feed only; reaches no network
make build SOURCE=orfe ENRICH=1   # also scrapes the event pages
```

The layering matches the rule chains. `div.events-detail-main` is present on all 11 live hosts and means the same thing on each, so it is written once in `defaults:`. Everything semantic is per source, because `div.event-subtitle` carries the talk title on `orfe`, the speaker on `mae`, and the host on `materials` — one selector, three meanings.

```yaml
- field: speakers
  selectors:                                          # tried left to right
    - "div.field--name-field-ps-event-speaker-name"   # MAE's FPO pages: a bare name
    - "div.event-subtitle"                            # MAE's seminar pages: name + affiliation
  split_affiliation: true
  reject: ['^hosted by']                              # materials' host line
```

A source overriding `enrich` replaces the list wholesale rather than appending — selector order is priority order, same as rule chains.

`reject` is the one worth explaining. `materials`' subtitle reads "Hosted by Alice Kunin" — a real person, correctly scraped, and the wrong one. Publishing it as the speaker would be schema-valid and false. Declines are counted, so a selector that always rejects surfaces rather than quietly yielding nothing.

## What gets published

```
feeds/<source>/events.json   one per live source, faithful to its upstream
combos/<name>/events.json    the declared combinations
schema/events.schema.json    the contract, served so a validator can $ref it
status.json                  what succeeded, what failed, and how stale anything is
state/upstream.json          internal bookkeeping, not a contract
index.html  simulator.html   the landing page and the newsletter simulator
```

`status.json` is the only file with a clock. The feeds carry **no timestamp**, which is what lets published bytes be compared byte-for-byte to detect drift — so it is also the only way a consumer can tell fresh from frozen.

```json
{
  "path": "feeds/orfe/events.json",
  "status": "ok",
  "events": 19,
  "label": "Operations Research and Financial Engineering",
  "home": "https://orfe.princeton.edu",
  "purposes": ["engineering-newsletter"],
  "lastSuccessAt": "2026-09-12T01:24:08Z"
}
```

`empty` is a first-class status, not a kind of failure: `kellercenter` serves a well-formed calendar with no events, and that payload is byte-identical to a broken source's. Only the source's own declaration separates them.

**A failure is never silent.** A source that fails keeps serving its last good feed, marked `stale` with the reason. Every combined feed that includes it is built from that last good copy rather than omitting it, and records which input it fell back to. `lastSuccessAt` is carried across failures, which is what makes staleness a duration rather than a yes/no.

### Combined feeds

Declared in `config/combos.yaml` as set algebra over the per-source feeds.

```yaml
- name: all-no-fpo
  label: Every live source, without final public orals
  include: ["*"]
  exclude_where:
    tags: { contains: fpo }
  key: [starts_at, title]
```

Five build; a sixth, `nextg`, is **disabled with its reason** — ECE's feed carries no NextG discriminator, so a guessed predicate would publish an unfiltered ECE feed under NextG's name. Every unavailable source and disabled combo appears in `status.json` with its reason, so "we chose not to" and "we forgot" never read alike.

Combined feeds deduplicate **only when every compared detail matches**, collapsing to one record whose `sources` array names each feed that carried it. When the key matches but details differ, both records are emitted and the divergence is counted — never merged, never guessed.

A key may not name `guid` or `id`. Measured: `ps_events:4056:delta:0` is `ai`'s "ORFE Colloquium" *and* `materials`' "Materials Institute Symposium", two unrelated events. 12 of 125 nids collide across these feeds, so `id` is `"{source}:{guid}"` and a bare-`guid` key is a configuration error.

## The newsletter simulator

`simulator.html` answers the question an editor has: *which of these events would my edition contain?* Pick a publication date; it shows what an editorial system would ingest, which titles are still unannounced, and the listing made up ready to compose.

Each row is graded — `ready`, `fix first`, `check` — by what an editor would have to do
about it: a synthesized title or a missing location is a blocker, while a missing speaker
is reported as a gap rather than flagged, since two events in five have none and most
legitimately so. The state is carried three ways, because colour alone is not a signal
everyone receives: the badge's own word, a stripe, and a tooltip naming what is missing.

Beyond what the predecessor sites do it adds **source selection**, **purpose selection**, and an export shaped like what the editors assemble in Mailchimp by hand — grouped by day, with their own field labels, titles linked to their event pages and sponsors to their units. Both the copy and the download carry their styling inline, because email clients strip `<style>` and honour only `style=""`.

**Nothing about the feeds is written into the page.** Sources, names, sites and purposes all come from `status.json`, so a fifteenth source appears the moment it is registered. That is deliberate: ORFE's and MAE's `feed-simulator.js` are **byte-identical** (`md5 1f4aa8d0…`) with the department baked in, and both are a lossy mirror of ORFE's 750-line `newsletter.py`.

It is checked against editions that went out — two of them, from different publications. `tests/fixtures/newsletter/2026-09-08-edition.json` is the engineering issue of 7–14 September 2026, transcribed from its Mailchimp export; `2026-09-24-dais-edition.json` is the DaIS issue of 24 September, transcribed from the email. Every field in both was verified against the source file. The suite runs the simulator's own JavaScript through Node over the committed feeds and compares.

The DaIS edition reproduces line for line where its own edition is consistent — `4:30 — 6 p.m. Monday, Sept. 28, in Friend 006`, character for character. Where it is not, the generated listing picks one form and holds it: their edition writes a comma after the meridiem on some entries and not others, a hyphen on one line and an em dash on the next, and one of its *Learn More* links points at the wrong event. That last one is the whole argument.

That comparison found a defect on first contact. `ai` and `materials` both list the same 12:05 talk and the editors merged it into one entry with three sponsors; both our titles were *synthesized placeholders* built from each unit's own template, so matching on the title found no duplicate where there plainly was one. The repeat check now keys on the speaker's name whenever the title is a placeholder.

## Extending it

Everything below is config. None of it needs code.

**Add a source.** One block in `config/sources.yaml`. `summary_role` and `location_rules` have no defaults — a source that omits them fails to load rather than guessing.

```yaml
- slug: newunit
  label: The Unit's Own Name For Itself      # becomes Sponsor in a generated listing
  host: newunit.princeton.edu                # feed_url derives from url_template
  cadence: 1h
  location_rules: [sentinel, comma_room, whole]
  expectations:
    summary_role: title
```

Then `make check` to validate it and `make build SOURCE=newunit` to see it. Commit a captured ICS to `tests/fixtures/feeds/newunit/feed.ics` so the suite covers it offline.

**Add a combined feed.** One block in `config/combos.yaml`, as above. `include: ["*"]` means every live source.

**Map a category spelling.** Add an alias under the canonical tag in `config/tags.yaml`. The unmapped-tags gate warns about every spelling nothing recognises, so the work list writes itself:

```
orfe: unmapped_tags: 2 category spelling(s) have no canonical tag:
  'Stochastic Analysis and Financial Mathematics Seminar' (4)
```

**Declare a purpose.** A name and a label under `purposes:`, then name it on the sources that serve it. Optional and never inherited — a feed added for some other reason serves no purpose until it says so.

```yaml
purposes:
  engineering-newsletter:
    label: Engineering events newsletter

sources:
  - slug: orfe
    purposes: [engineering-newsletter]
    purpose_overrides:                        # first match wins
      - when: { tags: { contains: fpo } }
        purposes: []
```

**Give a purpose a publication.** A label is enough to select feeds. A purpose that is also a *publication* adds a `template` and a `schedule`, and the simulator reads both from `status.json` — so a third newsletter is config too.

```yaml
  dais-newsletter:
    label: DAIS events newsletter
    template: inline-date                     # or day-grouped
    schedule:
      publication: { weekday: THU, time: "14:00" }
      coverage:                               # Thursday's edition is next week's events
        start: { anchor: next_week_start }
        end:   { anchor: next_week_start, offset_days: 6 }
```

`deadline` is optional and its absence is not a default: DaIS states none — its edition says only to send an email — so the page shows no deadline rather than inventing a date that would look authoritative beside everything else on it. Weekday, anchor, clock and template are all checked at load, because an unknown one resolves to *some* date or *some* layout and produces an edition that looks finished and is wrong.

Two publications now exist, and they differ on both axes the mechanism claims to carry — Monday against Thursday, the current week against the next, day headings against one inline when-and-where line. That is the test of it: with one publication, nothing distinguishes a mechanism from a hardcoded shape behind a label.

**Filter a source's own feed.** `publish_unless` drops matching events, `publish_where` keeps only matching ones — the same predicate vocabulary a combined feed uses. ORFE does this: the department does not list final public orals, so its feed carries 19 of the 23 events its calendar publishes. The count declined and the clause that did it go into `status.json`, because a filtered feed and a feed whose upstream went quiet look identical from outside.

Every controlled vocabulary is checked by name at load. A misspelled tag, purpose, pattern, location rule or escape field is a load error — because a predicate on a value nothing produces filters to nothing and reports success, which is indistinguishable from a filter that is simply strict.

## Scale

Fourteen feeds, published every 20 minutes on weekday daytime Eastern and hourly otherwise. Three crons that do not overlap; a test proves it.

That schedule is the *fastest* any source is polled, not the rate all of them are. Two mechanisms keep the load off other people's servers, both keyed off `state/upstream.json`:

| | |
|---|---|
| **Per-source cadence** | Each source declares `30m`, `1h` or `6h` and is refetched only once it has elapsed. `cee` published one event this term; asking it 72 times a day is not something to do to somebody else's server. A source that is not due is reported **current**, not stale. |
| **Conditional requests** | `ETag` and `Last-Modified` are remembered and replayed. Eleven of the twelve measured in September answer `304` with no body when nothing has changed. A 304 is neither `ok` nor `failed` — as `ok` we would build a feed from an empty body, as `failed` a healthy source would be marked stale for answering correctly. |
| **Enrichment windows** | `rebuild_after_hours` decides how long scraped values stay good — 24 by default, 6 for the two sources on a 30-minute cadence. Inside the window nothing is fetched; events new since the last scrape are fetched anyway, so a seminar added this morning is not untitled for hours. |

Measured before these existed: about **4,244 requests a day**, of which ~4,100 were event-page scrapes returning identical markup. ORFE alone went from 960 a day to roughly 80.

Every run still deploys, including runs where no feed changed, because `status.json` is the liveness beacon. A consumer must be able to tell "checked eleven minutes ago, ORFE is stale" from "nothing has run in three days".

`verify.yml` is a **separate** workflow that checks the live origin and knows nothing about the run that produced it. That separation is the point: the predecessor's pipeline died before its Pages steps, so those steps were *skipped* rather than failed — and a skipped step is green. It compares served event counts against the ones `status.json` promises, which is what catches a partial deploy. Staleness is graded by duration: under six hours warns, past it fails and files an issue.

`heartbeat.yml` exists because GitHub disables scheduled workflows on a public repository after 60 days of quiet, silently. It commits a keepalive past 35 days and separately asks the API whether each scheduled workflow is still `active`, re-enabling and reporting anything that is not.

## Limitations

Stated rather than implied, because most of these look like bugs from outside.

| | |
|---|---|
| **Five sources produce nothing** | `cs` is Drupal without an iCal view; `acee`, `decenter` and `metro` are WordPress whose REST fields are empty unauthenticated; `nextg` has no feed of its own. Four of the five need a second platform adapter. `cs` and `acee` are the two the engineering newsletter actually wants. |
| **The heartbeat cannot rescue itself** | If *every* scheduled workflow is disabled at once, it goes with them. Inherent to running monitoring inside the thing monitored; only a check from outside the repository answers it. |
| **The simulator's edition rule is JavaScript, and weekly only** | No holiday shifts and no blackouts. The real 7 September 2026 edition published on the Tuesday for Labor Day; reproducing it means overriding the publication date, which the page says. |
| **No modality** | The feeds carry nothing for the editors' `(hybrid)` marker, so the generated listing omits it. |
| **Some series values are raw categories** | `citp`'s `series` is its comma-joined CATEGORIES, where the newsletter writes "CITP Seminars". Curated series names are not in the feed. |
| **An edited event page takes time to appear** | Up to that source's `rebuild_after_hours`. `--rescrape` forces a full re-read after a selector change. |
| **The bypass credential's necessity is unverified** | A run with a deliberately wrong header produced byte-identical output, so these hosts are not currently behind the challenge. The gate that would catch a real blackout has never fired in production. |
| **Per-source feeds do not deduplicate** | By design — each reproduces one upstream. A consumer reading two that collide resolves it. Combined feeds do, but only on a total match. |

## Checked against the predecessor

`tests/test_differential.py` runs this pipeline against the two golden pairs shipped in `pubino/mae-upcoming` — real ICS in, a real department's expected JSON out. Shared fields must agree exactly, with none of the swapped-name-and-detail tolerance the predecessor's own test allows.

Every divergence is a named test that states its reason and first asserts the golden really contains what it diverges from. Four are defects in the predecessor's published output:

| finding | this build |
|---|---|
| ORFE's golden has 13 records; its feed has 14 | emits all 14 — the missing one is a second Drupal node for the same talk, and deduplicating is the consumer's call |
| MAE's golden publishes the literal `"TBD"` as a title, with `titleSource` and `titleIsPlaceholder` **absent** | synthesizes a title and flags it, so a consumer can tell |
| ORFE's abstracts have words glued together — `adata-driven`, `banditmodel`, 15 times in one fixture | unfolds per RFC 5545, so the words survive |
| HTML entities half-decode: `&gt\;` becomes `>\;`, leaving a stray semicolon | decodes once, consistently |

A fifth, found by the simulator: the predecessor's 11 placeholder titles collapse to **4 distinct strings** — four events all called "An S. S. Wilks Memorial Seminar in Statistics Talk". Ours append the speaker, giving 11 distinct.

## Design notes

| | |
|---|---|
| **`sources` is always an array** | Length 1 in a per-source feed, length N in a combined one, so consumers parse both identically. |
| **Derived fields are computed, never stored** | `speaker` and `affiliation` are properties over `speakers[]` and cannot fall out of step with it. The loader refuses an enrichment target naming either, since scraping into the scalar would keep one speaker and drop the rest. |
| **Environment access lives in one module** | A test enforces it. The predecessor reads `os.getenv` in leaf functions — 21 call sites in its enrichment module alone — which makes per-source configuration impossible in one process, and is the root cause of the fork. |
| **Thresholds are per source** | A global constant would sit permanently breached for the sources whose normal is unusual, and a gate that always fires teaches everyone to ignore it. |
| **Every dependency is imported** | A contract test parses `pyproject.toml` against every module's imports. Four were found declared and unused, one of them carrying a version ceiling for a library never imported. |

## License

MIT © The Trustees of Princeton University
