# Upcoming

Campus event feeds pulled from what departments, units, and centers advertise.

This project is a heavy refactor of [pu-orfe/upcoming](https://github.com/pu-orfe/upcoming), informed by
[pubino/mae-upcoming](https://github.com/pubino/mae-upcoming).

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

17 total: 12 live, 5 recorded as unavailable.

| | |
|---|---|
| **Live, Princeton Site Builder** | `orfe` (23 events), `citp` (24), `quantum` (18), `materials` (16), `ece` (12), `cbe` (10), `mae` (10), `ai` (7), `bioengineering` (3), `cee` (1), `robotics` (1) |
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

Four feeds pack more than one field into `SUMMARY`, or mean different things by it on
different events of the same feed. They declare an ordered rule chain instead of a single
role:

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

Two layers, deliberately separate. **Named patterns** in `config/patterns.yaml` are shared
by every source, each carrying `match` *and* `no_match` examples run as a table test — the
risk with these shapes is never failing to match, it is matching something confidently and
wrongly. **Rule chains** are per source, because which patterns apply and in what order is
a property of one department's conventions.

The engine is small on purpose: no arithmetic, no computed values, no cross-event state,
and predicate nesting stops at depth two. Anything needing more gets a *name* — a pattern
or a Python predicate — which config selects but cannot define.

Every event records which rule mapped it, and each source's test pins the exact
distribution. A reordering that silently moves four events between rules still produces
schema-valid output; the recorded counts are the only thing that catches it.

The hardest case parses correctly:

```
Princeton Quantum Colloquium: An Astonishing Quantum Universe in Two Dimensions: From
Fundamental Physics to (Possibly) Quantum Computation, Jainendra Jain (Penn State University)
```

The series ends at the first colon; the title keeps its own colon and its own
parenthetical; the speaker and affiliation come from the last one. Elsewhere in the same
feed, `University of Colorado, Boulder` survives its comma intact.

## Reading the event pages

Some fields live on the event page rather than in the feed — ORFE's talk titles, MAE's
speakers. `--enrich` scrapes the targets a source declares:

```sh
make build SOURCE=orfe            # feed only; reaches no network
make build SOURCE=orfe ENRICH=1   # also scrapes the event pages
```

The layering matches the rule chains. **One target is harmonized**: `div.events-detail-main`
is present on all 11 live hosts and means the same thing on each, so it is written once in
`defaults:`. **Everything semantic is per source**, because `div.event-subtitle` carries
the talk title on orfe, the speaker on mae, and the host on materials — one selector,
three meanings. A source overriding `enrich` replaces the list wholesale rather than
appending, for the same reason rule chains do: selector order is priority order.

Three things a target can declare:

```yaml
- field: speakers
  selectors:                                            # tried left to right
    - "div.field--name-field-ps-event-speaker-name"     # MAE's FPO pages: a bare name
    - "div.event-subtitle"                              # MAE's seminar pages: name + affiliation
  split_affiliation: true
  reject: ['^hosted by']                              # materials' host line
```

`reject` is the one worth explaining. materials' subtitle reads "Hosted by Alice Kunin" — a
real person, correctly scraped, and the wrong one. Publishing it as the speaker would be
schema-valid and false. The decline is counted, so a selector that always rejects surfaces
rather than quietly yielding nothing.

## What gets published

```
feeds/<source>/events.json   one per live source, faithful to its upstream
combos/<name>/events.json    the declared combinations
schema/events.schema.json    the contract, served so a validator can $ref it
state/upstream.json          internal bookkeeping, not a contract (see below)
status.json                  what succeeded, what failed, and how stale anything is
index.html                   the landing page, which reads status.json at load time
simulator.html               the newsletter simulator (see below)
simulator.js  style.css      its behaviour, and the styles both pages share
```

Five combined feeds are declared in `config/combos.yaml` as set algebra over the
per-source feeds: `all` (115), `sherrerd-hall` (40), `engineering` (35), `all-no-fpo`
(105), `seminars` (56). A sixth, `nextg`, is declared **disabled with its reason** — ECE's
feed carries no NextG discriminator, so a guessed predicate would publish an unfiltered ECE
feed under NextG's name. Every unavailable source and every disabled combo appears in
`status.json` with its reason, so "we chose not to" and "we forgot" never read alike.

`all-no-fpo` removes exactly the ten final public orals across three departments. That is
what the canonical tag vocabulary in `config/tags.yaml` exists for: the four upstream
spellings of the same thing map to one tag. The predecessor filters on the literal series
string `FPO`, which is correct for ORFE — measured against its live feed, it publishes 19
events and excludes exactly the 4 final public orals, all of which carry `series: "FPO"`.
What that approach does not survive is a second department: `mae` publishes
`Final Public Oral Exam` and `ece` publishes
`Final Public Oral Examinations,Final Public Orals`, so a literal match finds neither. The
vocabulary exists for the scale, not because the predecessor was wrong within its own.

## The newsletter simulator

`simulator.html` answers the question an editor actually has: *which of these events would
my edition contain?* Pick a publication date and it shows what an editorial system would
ingest for that edition, which titles are still unannounced, and the listing made up ready
to compose. A menu moves between it and the feed listing.

Beyond what the predecessor sites offer it adds **source selection** — a checkbox per feed,
with unavailable ones shown and disabled beside their reason — and **purpose selection**,
filtering on the `purposes` each event carries. One button selects the feeds declaring a
purpose; the two controls otherwise stay independent, so neither silently overrides the
other.

**Nothing about the feeds is written into the page.** Sources, their names and their
purposes all come from `status.json`, so a thirteenth source appears in the simulator the
moment it is registered. That is a deliberate response to the predecessors: ORFE's and
MAE's `feed-simulator.js` are **byte-identical** (`md5 1f4aa8d0…`) with the department
baked in, which is how one file came to be maintained twice — and both are a lossy mirror
of ORFE's 750-line `newsletter.py`, missing its blackouts and exceptions entirely.

### The listing it exports

Shaped like the edition the editors assemble in Mailchimp by hand: grouped by day, only
days with events, each entry carrying the time and their own field labels — `Speaker(s):`,
`Sponsor(s):`, `Series:`, `Location:`. Copy for Mailchimp puts it on the clipboard as rich
text so a paste keeps its structure, and the download is a standalone file.

Both carry their styling **inline, on every element**, because Mailchimp and the clients it
sends to strip `<style>` blocks and honour only `style=""`. The download also carries the
same rules as a stylesheet so the file reads properly when simply opened. The rules are
written for email — no grid, no flex, no custom properties, since a `--dim` resolves to
nothing once the file has left this site. One definition produces both renderings, and a
test fails if they disagree. The preview on the page is untouched by any of it and keeps
following the reader's light or dark theme.

**Sponsor** is the one field they write by hand that we can now generate: it is each feed's
declared name for itself, which is why `label` joins every feed's record in `status.json`.
Nothing turns the slug `citp` into "Center for Information Technology Policy" but the
registry.

Two things the feeds do not carry, so the page says so rather than leaving an editor to
notice: an event's **modality** (their `(hybrid)` marker) and a building name longer than
the calendar gives — `Bowen` where the newsletter writes `Bowen Hall`. A title still
awaiting announcement is **marked** in the export, since replacing it is the editor's job.

### Checked against an edition that went out

`tests/fixtures/newsletter/2026-09-08-edition.json` is the real 7–14 September 2026 issue,
transcribed from its Mailchimp export with every field verified against that file. The
suite runs the simulator's own JavaScript through Node over the committed feeds and
compares. Three of the seven events are in the fixture snapshot and are asserted exactly,
sponsor included; the fixture records why the other four are not — three are ORFE events
absent from the snapshot, and one is published by a unit we have no feed for.

It found a real defect. `ai` and `materials` both list the same 12:05 talk, and the editors
merged it into one entry with **three** sponsors. Our feeds publish it twice, correctly —
a per-source feed reproduces its own upstream — so the simulator counts it as a repeat. But
both titles were still *synthesized placeholders*, generated from each unit's own template,
so matching on the title found no duplicate where there plainly was one. The repeat check
now keys on the speaker's name whenever the title is a placeholder.

The edition rule is the standard weekly schedule only — Monday publication at noon, the
Tuesday before as the deadline, coverage from publication day to that week's Sunday. It has
**no holiday shifts**: the real 7 September edition published on the Tuesday for Labor Day,
and reproducing it means overriding the publication date, which the page tells you.

### Purpose

A feed may declare what its events can be put toward, and every event carries it as
`purposes`. The names are a small vocabulary declared under `purposes:` in
`config/sources.yaml`, each with a label:

```yaml
purposes:
  engineering-newsletter:
    label: Engineering events newsletter

sources:
  - slug: orfe
    purposes: [engineering-newsletter]
    # A feed mostly destined somewhere, with a class of events that is not. First match
    # wins, and `purposes: []` means these events serve none.
    purpose_overrides:
      - when: { tags: { contains: fpo } }
        purposes: []
```

Optional, and nothing is inherited from `defaults:` — a feed added for some other reason
serves no purpose until it says so, because a source silently joining a publication is a
worse failure than one left out and noticed.

The vocabulary is controlled because a purpose is matched by name: a misspelled one selects
nothing and reports success, which is indistinguishable from a filter that is simply
strict. So an undeclared name is a load error, whether it appears on a source, in an
override, or in a predicate that selects on it.

Overrides resolve **after** mapping, since their predicates read `tags` and `series` —
values that do not exist until mapping has run. A merged record carries the **union** of
its sources' purposes: intersecting would drop an event from a purpose precisely because a
second unit also listed it. And `purposes` is not part of the merge comparison, so two
records differing only in it are still the same event rather than a published duplicate.

`status.json` names each feed's purposes too, the unavailable ones included, so the
question is answerable per feed as well as per event.

### `status.json` is the contract for "is this current"

The feeds deliberately carry **no timestamp**, which is what lets published bytes be
compared byte-for-byte to detect drift — so `status.json` is the only file with a clock,
and the only way a consumer can tell fresh from frozen. It names every feed, its status
(`ok` / `empty` / `failed` / `disabled`), its event count, whether it is stale, why, and
`lastSuccessAt`, e.g., when its content was last built from a live fetch. That last field is
carried forward across failures, which is how we measure staleness.

`empty` is first-class rather than a kind of failure: `kellercenter` serves a well-formed
calendar with no events, and that payload is byte-identical to a broken source's. Only the
source's own declaration separates them.

### A failure is never silent

A source that fails **keeps serving its last good feed**. Every combined feed that includes a
failed source is built from that source's last good copy rather than omitting it, and
records which input it fell back to.

## Keeping it running

GitHub disables scheduled workflows on a public repository after 60 days without
repository activity: it emails the owner, stops running them, and nothing in the
repository reports it. The site simply stops updating — and the watchdog that would notice
stops with it. `heartbeat.yml` commits a keepalive once the repository has been quiet past
35 days, leaving 25 days of slack, and separately asks the API whether each scheduled
workflow is still `active`, re-enabling and reporting anything that is not. A workflow
somebody switched off by hand is reported too: turning the publisher off is legitimate,
leaving it off silently is not.

The limit it cannot cover, stated rather than implied: if *every* scheduled workflow is
disabled at once, the heartbeat is disabled too and cannot rescue itself. That is inherent
to running your own monitoring inside the thing being monitored, and only a check from
outside the repository answers it.

## Not doing the same work twice

A run rebuilds every feed unconditionally, and that is cheap and deliberate: the feeds
carry no timestamp, so an unchanged source re-renders byte-identically and the published
branch records no change. What is *not* cheap is asking twelve departmental web servers for
things they have already given us. Measured before this existed: about **4,244 requests a
day**, of which ~4,100 were event-page scrapes returning identical markup.

Two mechanisms, both keyed off `state/upstream.json` — internal bookkeeping, published
rather than cached because an Actions cache expires after a week and losing it silently
would restore the old behaviour with nothing reporting the regression.

**Conditional requests.** Each source's `ETag` and `Last-Modified` are remembered and
replayed as `If-None-Match` / `If-Modified-Since`. Eleven of the twelve sources answer
`304` with no body when nothing has changed. A 304 is deliberately neither `ok` nor
`failed`: as `ok` we would build a feed from an empty body, and as `failed` a healthy
source would be marked stale every time it answered correctly.

**Reusing the last scrape.** `rebuild_after_hours` decides how long enrichment results
stay good — 24 by default, **6 for the two sources with a 30-minute cadence**. Inside the
window, scraped values are carried forward from the previously published feed and no page
is fetched at all. ORFE goes from 960 requests a day to about 80.

Two properties keep that safe. Events new since the last scrape are absent from the carried
set and so are fetched anyway — otherwise a seminar added this morning would publish
untitled for hours. And carrying a value carries its **provenance**: copying a title
without `titleSource` would republish it as synthesized, and since feeds are compared
byte-for-byte that disagreement would read as a change on every run. A test asserts a warm
run fetches nothing and emits bytes identical to a cold one.

The cost is stated rather than hidden: a title edited on an event page takes up to
`rebuild_after_hours` to appear. That number is per source in config, which is where a
freshness-versus-politeness trade belongs.

## Cadence and the watchdog

`publish.yml` runs every 20 minutes on weekday daytime Eastern and hourly otherwise —
departmental calendars are edited by people during working hours, so a uniform cadence
spends most of its runs proving nothing changed overnight while still being slower than it
needs to be when someone posts a seminar. The three crons do not overlap; a test proves it.

That schedule is the *fastest* any source is polled, not the rate every source is polled
at. Each source declares its own `cadence` (`30m`, `1h`, `6h`) and is only refetched once
that has elapsed. `cee` published one event this term; asking it seventy-two times a day to
be told nothing changed is not something to do to someone else's server. A source that is
not due is reported as **current**, not stale — it is serving exactly what its own
configuration asked for. `--ignore-cadence` overrides this.

Every run deploys, including runs where no feed changed, because `status.json` is the
site's liveness beacon. A consumer must be able to tell "we checked eleven minutes ago and
ORFE is stale" from "nothing has run in three days".

`verify.yml` is a **separate** workflow on its own schedule that checks the live origin and
knows nothing about the run that produced it. That separation is the whole point. The
predecessor's pipeline job died before its Pages steps, so those steps were *skipped*
rather than failed — and a skipped step is green. The site went stale and nothing reported
it. A check living inside that job would have been skipped by the same failure.

It fetches `status.json`, checks its age, fetches every path the manifest promises, and
compares the served event counts against the promised ones — which is what catches a
**partial deploy**, where some paths updated and some did not and neither file alone looks
wrong. Staleness is graded by duration rather than treated as a yes/no, which is the difference
between a useful watchdog and an ignored one. A department whose server rebooted is stale
for twenty minutes and needs nobody woken; one stale for two days is an outage nobody has
noticed. Under six hours is a warning, past it a failure that files an issue. Reporting
both the same way would mean either the reboot pages or the two-day outage stays green,
and there is no threshold that makes both right. It opens one
issue when something is wrong and closes it on recovery, so a problem outlives the run that
found it.

The watchdog gets **no bypass credential**, deliberately — it must see exactly what an
ordinary consumer sees.

The logic is in `upcoming/verify.py` with 19 tests rather than in a YAML step, and
`.github/workflows/` is itself covered by `tests/test_workflows.py`: that the crons do not
collide, that the deploy is its own job rather than a step, that it runs even when a source
failed, that every job has a timeout, and that lint can never block a publish.

## Checked against the predecessor

`tests/test_differential.py` runs this pipeline against the two golden pairs shipped in
`pubino/mae-upcoming` — real ICS in, a real department's expected JSON out. Shared fields
must agree exactly, including ORFE's `Elynn Chen\, New York University` escaping and every
location split, with none of the swapped-name-and-detail tolerance the predecessor's own
test allows.

Every divergence is a named test that states its reason and first asserts the golden
really contains what it diverges from. Four of them are defects found in the predecessor's
published output:

| finding | this build |
|---|---|
| ORFE's golden has 13 records; its feed has 14 | emits all 14 — the missing one is a second Drupal node for the same talk, and de-duplicating is the consumer's call |
| MAE's golden publishes the literal `"TBD"` as a title, with `titleSource` and `titleIsPlaceholder` **absent** | synthesizes a title and flags it, so a consumer can tell |
| ORFE's abstracts have words glued together — `adata-driven`, `banditmodel`, `anyneural`, `publichealth`, 15 times in one fixture | unfolds per RFC 5545, so the words survive |
| HTML entities half-decode: `&gt\;` becomes `>\;`, leaving a stray semicolon, and MAE's golden leaves `&amp\;` untouched | decodes once, consistently |

## Design notes

**Identity is source-scoped.** `ps_events` UIDs are *per-site sequences*, not global
identifiers. Measured: `ps_events:4056:delta:0` is `ai`'s "ORFE Colloquium" **and**
`materials`' "Materials Institute Symposium" — two unrelated events six months apart — and
12 of 125 nids collide across these feeds. So `id` is `"{source}:{guid}"`, `guid` is
published verbatim but documented as source-scoped, and a dedupe key of `guid` alone is a
configuration error.

**Per-source feeds never deduplicate.** Each represents one upstream feed. A consumer
reading two of them that collide resolves that itself.

**A source may decline to publish part of its own feed.** `publish_unless` drops matching
events and `publish_where` keeps only matching ones, using the same predicate vocabulary as
a combined feed's `exclude_where` / `where` — one dialect, so reading about either teaches
the other. ORFE uses it: the department does not list final public orals on its
upcoming-events display, so `feeds/orfe/events.json` carries 19 of the 23 events its
calendar publishes.

This makes a per-source feed a *declared view* of its upstream rather than an unconditional
mirror, which is a real change in what the feed promises — so the count declined and the
predicate that did it are published in `status.json`. A filtered feed and a feed whose
upstream went quiet look identical from outside, and only one of them is a decision.
Filtering runs after mapping (so tags exist to match on) and before enrichment (so an event
we are not publishing costs nobody a page fetch — for ORFE that is four requests saved
every half hour). Every other source is an unfiltered mirror, and a test fails if that
changes without the diff showing it.

**Combined feeds deduplicate only when every detail matches**, collapsing to one record
whose `sources` array names every feed that carried it. When a key matches but details
differ, both records are emitted and the divergence is counted — never merged, never
guessed. In today's data that means almost nothing merges, and that is the correct outcome:
`cee` and `mae` both list the same talk but disagree on the location (`Maeder Hall
Auditorium` vs `TBD`), and the four `ai`/`orfe` colloquium pairs carry a generic series
placeholder against a named speaker.

**`sources` is always an array**, length 1 in a per-source feed and length N in a combined
one, so consumers parse both identically.

**Derived fields are computed, never stored.** `speaker` and `affiliation` are properties
over `speakers[]`, so they cannot fall out of step with it — and the loader refuses an
enrichment target that names one of them, since scraping into the scalar would keep one
speaker and silently drop the rest.

**Environment access lives in exactly one module.** A test enforces it. The predecessor
reads `os.getenv` inside leaf functions — 21 call sites in its enrichment module alone —
which makes per-source configuration impossible in a single process, and that impossibility
is the root cause of the fork.

**Thresholds are per source.** A global constant would sit permanently breached for the
sources whose normal is unusual, and a gate that always fires teaches everyone to ignore
it.

## License

MIT © The Trustees of Princeton University
