# upcoming

Campus event feeds for SHD — at Sherrerd Hall, and Beyond.

One `events.json` per data source, plus combined feeds composed from them by declared set
algebra. A refactor of [pu-orfe/upcoming](https://github.com/pu-orfe/upcoming), informed by
[pubino/mae-upcoming](https://github.com/pubino/mae-upcoming).

> **Status: early.** The source registry, the event model, and the quality gates are in
> place and tested. Nothing fetches a feed yet.

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
  these feeds, and wrong invisibly, so the only fix is to refuse to have one.
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
make install        # create .venv and install with dev extras
make sources        # the resolved registry: what each SUMMARY field means
make check          # validate the registry; non-zero on any problem
make test           # pytest
make lint typecheck # ruff + mypy
make docker-test    # the suite in the container (the path CI runs)
```

Requires Python 3.12+. Docker Compose is invoked as `docker-compose`.

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
