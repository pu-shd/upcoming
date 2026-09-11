# upcoming

Campus event feeds for SHD — at Sherrerd Hall, and Beyond.

One `events.json` per data source, plus combined feeds composed from them by declared set
algebra. A refactor of [pu-orfe/upcoming](https://github.com/pu-orfe/upcoming), informed by
[pubino/mae-upcoming](https://github.com/pubino/mae-upcoming).

> **Status: all twelve live sources build, and four enrich from their event pages.**
> Every source produces `events.json` from committed fixtures, offline; `orfe` and `mae`
> are additionally checked field-by-field against the predecessor's own expected output.

## The important section

These feeds put **the same information in different fields**, and they all satisfy the same
schema. `orfe` writes the *speaker* into the ICS `SUMMARY` and puts the talk title on the
event page; `mae` does the exact reverse. So running one department's mapping against the
other's feed produces output that is schema-valid, error-free, and useless — the title
sitting in `speaker` and the speaker sitting in `title`, with nothing anywhere reporting a
problem.

That is why `mae-upcoming` exists as a *fork* rather than a second source in one pipeline,
and it is the failure this project is built to make impossible:

- **`expectations.summary_role` has no default.** A source that does not declare what its
  `SUMMARY` field means fails to load. Every possible default is wrong for roughly half
  these feeds, and wrong invisibly, so the only fix is to refuse to have one. Three values:
  `speaker`, `title`, and `rules` for the four feeds where it varies event by event.
- **Every mapping decision is recorded on the event** (`mappingRules`, `locationRule`,
  `titleSource`, `summaryRaw`), so "which field did `SUMMARY` go to?" is answerable from
  published output alone.
- **Declining to guess is a first-class outcome.** An unsplittable venue keeps its whole
  string as `location.name` rather than being guessed apart; text a rule cannot classify is
  parked in `summaryRest` and counted, never assigned to a plausible-looking field.

`make sources` prints the one table that makes a copy-paste onboarding error visible:

```
SOURCE          STATUS       SUMMARY IS  CADENCE  ENRICH                FEED
orfe            live         speaker     30m      title,raw_details     https://orfe.princeton.e…
mae             live         title       1h       speakers,raw_details  https://mae.princeton.ed…
ai              live         mixed       1h       -                     https://ai.princeton.edu…
bioengineering  live         title       1h       speakers,raw_details  https://bioengineering.p…
```

## Sources

17 declared: 12 live, 5 recorded as unavailable. All of it measured, not assumed.

| | |
|---|---|
| **Live, Princeton Site Builder** | `orfe` (23 events), `citp` (24), `quantum` (18), `materials` (16), `ece` (12), `cbe` (10), `mae` (10), `ai` (7), `bioengineering` (3), `cee` (1), `robotics` (1) |
| **Live, different platform** | `kellercenter` — `PRODID:-//Drupal iCal API//EN`, currently a well-formed calendar with **zero events** |
| **Declared unavailable** | `nextg`, `cs`, `acee`, `decenter`, `metro` |

Four shapes the design has to accommodate, each drawn from the live data:

- **`SUMMARY` is not consistent even within one feed.** `ai` carries a bare speaker name, a
  full talk title, *and* a series label across different events, so it is declared `mixed`.
- **Location conventions mix within one feed.** `cbe` writes both `E105 SEAS-BioE` and
  `SEAS-CBE F212`, so the room rule splits only when exactly one end of the string looks
  like a room, and declines otherwise.
- **`TBD` is common, not exceptional.** `quantum` carries a placeholder title in **14 of
  18** events; `mae` has one event whose entire `SUMMARY` is the string `TBD`. `TBD` is
  treated as absence — publishing it would satisfy the schema's `minLength: 1` and be
  wrong.
- **An event can have several speakers.** `bioengineering`'s Rising Stars symposium lists
  four. `speakers[]` is canonical, and the scalar `speaker` and `affiliation` are derived
  properties published alongside it so the existing ingest keeps working. `affiliation` is
  empty when the speakers disagree, because picking the first would attribute one person's
  institution to the whole panel.
- **Empty is not the same as broken.** `kellercenter` is legitimately empty. Only its
  `allow_empty` declaration knows that, because the two payloads are identical.

Each unavailable source records *why*, so nobody repeats the investigation — run
`make check` to read them. Short version: `nextg` has no feed of its own and no
discriminator in `ece`'s data to filter on; `cs` is Drupal without an iCal view (and
`cs.princeton.edu` just redirects to `www.cs.princeton.edu`, so it is not a second host);
`acee`, `decenter`, and `metro` are WordPress with no ICS export and no event dates exposed
to unauthenticated REST.

## The bypass credential

Every Site Builder **event page** returns `403` to every client without a bot-bypass
header. Every **ICS endpoint** answers a bare request. That asymmetry is the trap: with no
credential the feed still publishes, green, having scraped nothing.

The repository secret **`BOT_BYPASS_HEADER` holds a whole header line**, `Name: value` — so
the header *name* is not written here either. Holding only the value would still leave the
header named in a config file, and a named header with no value beside it invites the next
reader to supply a plausible one. That is exactly how the predecessors came to send `1`.

There is no default anywhere. A source with enrichment enabled refuses to load without it:

```
$ make check
configuration error: sources.orfe.http.secret_headers needs secret BOT_BYPASS_HEADER,
which is unset or empty. It has no default on purpose: a placeholder value would let
every event page fetch 403 while the run reported success.
```

The registry references it by name only, and only sources that actually scrape require it:

```yaml
http:
  secret_headers: [BOT_BYPASS_HEADER]
```

Keep it a secret rather than a repository variable — variable values are not masked in
Actions logs. A secret whose body is not a parseable header line is refused, and the
refusal never echoes the body.

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

Building and publishing reach no network by default: they read the committed fixtures, so a
developer runs the same code path over known bytes that CI runs over live ones. `make
publish FETCH=1` and `make publish ENRICH=1` are the opt-ins. The suite enforces the same
thing at the socket layer.

Requires Python 3.12+. Docker Compose is invoked as `docker-compose`.

## When one field means several things

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

Against the live site that takes ORFE from 14 synthesized titles to 3, with 14 pages
fetched for 28 field reads — each page is fetched and parsed once per run, however many
fields read it.

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

### Telling a blocked scrape from an empty page

Every Site Builder event page returns 403 without the bypass header. The predecessor's
fetch helper catches its own `raise_for_status` and returns `""`, so a run where *every*
page is blocked reports `attempted=125 updated=0 errors=0` — byte-identical to a clean run
that found nothing, and it publishes a green feed with every enrichment empty.

So fetching returns a typed outcome, and the health gate reads a **success rate** rather
than an error count:

```
orfe: title: reached 0 of 14 pages (0%, floor 80%); 14 HTTP error(s), 0 network error(s).
Every request failed, which is the shape of a bot challenge rather than a content problem
-- check the bypass credential for this host.
```

A page that loads and simply has no abstract is not a failure, and is counted separately.

## What gets published

```
feeds/<source>/events.json   one per live source, faithful to its upstream
combos/<name>/events.json    the declared combinations
schema/events.schema.json    the contract, served so a validator can $ref it
status.json                  what succeeded, what failed, and how stale anything is
index.html                   the landing page, which reads status.json at load time
```

Five combined feeds are declared in `config/combos.yaml` as set algebra over the
per-source feeds: `all` (115), `sherrerd-hall` (40), `engineering` (35), `all-no-fpo`
(105), `seminars` (56). A sixth, `nextg`, is declared **disabled with its reason** — ECE's
feed carries no NextG discriminator, so a guessed predicate would publish an unfiltered ECE
feed under NextG's name. Every unavailable source and every disabled combo appears in
`status.json` with its reason, so "we chose not to" and "we forgot" never read alike.

`all-no-fpo` removes exactly the ten final public orals across three departments. That is
what the canonical tag vocabulary in `config/tags.yaml` exists for: the four upstream
spellings of the same thing map to one tag. The predecessor excludes the literal string
`FPO` and so publishes a "filtered" feed identical to the unfiltered one.

### `status.json` is the contract for "is this current"

The feeds deliberately carry **no timestamp**, which is what lets published bytes be
compared byte-for-byte to detect drift — so `status.json` is the only file with a clock,
and the only way a consumer can tell fresh from frozen. It names every feed, its status
(`ok` / `empty` / `failed` / `disabled`), its event count, whether it is stale, why, and
`lastSuccessAt` — when its content was last built from a live fetch. That last field is
carried forward across failures, which is what makes staleness a **duration** rather than
a yes/no: without it, every run of a week-long outage would look like its first.

`empty` is first-class rather than a kind of failure: `kellercenter` serves a well-formed
calendar with no events, and that payload is byte-identical to a broken source's. Only the
source's own declaration separates them.

### A failure is never silent

A source that fails **keeps serving its last good feed**, because a blank departmental
listing is worse than one a few hours old — and an unannounced stale one is worse than
both, so the staleness and its reason are published. Every combined feed that includes a
failed source is built from that source's last good copy rather than omitting it, and
records which input it fell back to. Omitting it would quietly shrink a feed a consumer
relies on, which is the failure mode this whole layer exists to prevent.

One source failing still publishes the other eleven, still deploys, and still ends the run
red.

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

**Per-source feeds never deduplicate.** Each is a faithful representation of one upstream
feed. A consumer reading two of them that collide resolves that itself.

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
