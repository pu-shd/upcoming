# Roadmap

Ordered by whether anyone else has to act first. Nothing here is committed work; it is the
list a maintainer would pick from.

## Ours alone

### Drill the gates that have never fired

Three safety mechanisms are unit-tested and have never run in production: the **diff
threshold**, the **enrichment health gate**, and the **deploy-when-a-source-failed** path.
A mechanism that has never fired for real is a hypothesis.

Worth knowing before drilling: a run with a deliberately wrong bypass header produced
*byte-identical* output, because these hosts are not currently behind the bot challenge. So
the credential's necessity is itself unverified, and the gate that would catch its absence
has nothing to catch. Force the conditions instead — a truncated fixture for the diff gate,
a blocked transport against a live run for enrichment.

**Effort:** an hour.

### Unify the last duplicated vocabulary

`validate.py` defines `Severity` as `pass | warn | fail`; `verify.py` separately defines
`FAIL` and `WARN` as bare strings, and both modules have a `failures()` and a `warnings()`
returning different types. Two spellings of one idea.

Not urgent — mypy catches a wrong import — but a third consumer will invent a fourth
spelling. Move the vocabulary beside `clock.py` and `config.py`; leave the record types
alone, since a gate verdict and a site finding genuinely differ.

**Effort:** half an hour.

### Publish from the container

Tests run in Docker; publishing runs `pip install -e .` on the runner. The path that
produces the artifact is the one path never exercised in a container, which is backwards —
a dependency resolving differently on the runner than in the image would show up in
published output rather than in a test.

This also sets up a lockfile, which the dependency audit made possible by removing the
pins that were there for nothing.

**Effort:** a couple of hours.

### Split the untagged events from the unmappable ones

No category spelling in any live feed is unmapped. But **roughly one event in seven carries
no canonical tag at all**, which makes it invisible to every tag-filtered combined feed.
The figure moves with upstream, so measure rather than trust this sentence:

```sh
curl -s https://pu-shd.github.io/upcoming/combos/all/events.json \
  | python3 -c "import json,sys,collections; e=json.load(sys.stdin); \
      u=[x for x in e if not x['tags']]; print(len(u),'of',len(e)); \
      print(collections.Counter(x['sources'][0] for x in u).most_common())"
```

The distinction that matters is between "upstream published no `CATEGORIES`" and "we have
no word for this". Only the second is ours to fix, and today's gate cannot tell them apart.
Split the warning, then work the second list.

**Effort:** half a day.

### A second platform adapter

Four of the five unavailable sources are unavailable for the same structural reason: not
Princeton Site Builder, and no iCal. The parse layer already refuses to assume Site Builder
and the model already carries `platform`, so the seam exists — what is missing is a second
implementation behind it.

Worth doing only **after** the WordPress question below is answered, because the answer
decides whether the adapter reads a REST API or scrapes a listing page. Building the wrong
one first is a day spent twice.

**Effort:** a day.

## Waiting on someone else

Each is recorded in `config/sources.yaml` with its reason rather than quietly omitted.

| Source | Blocked on | What unblocks it |
|---|---|---|
| `cs` | Drupal, not Site Builder, no iCal view enabled. The bare apex was checked too. | Ask CS to enable an iCal view — minutes of their time against a day of ours. **Try this before writing any adapter.** |
| `acee` | WordPress. REST exposes an `events` post type whose ACF fields are empty unauthenticated. | A read token, or confirmation that dates live only in rendered HTML — which decides scrape versus API. |
| `decenter` | WordPress, `conference` and `eng_event` post types. | Same question as `acee`. One answer may cover three sources. |
| `metro` | `/events/feed/` returns RSS, but `pubDate` is the post's publication date, not the event's. | Nothing in that feed carries the event date, so RSS is a dead end regardless. |
| `nextg` | No feed of its own; correctly a derived view over ECE. ECE publishes one `CATEGORIES` value across all twelve events, so there is no discriminator. | ECE tagging its NextG events, or NextG supplying a curated list. A guessed predicate would publish an unfiltered ECE feed under NextG's name. |

`cs` is the sixth SEAS department and `acee` is named on the newsletter's own page. Between
them they are the two blocked sources the engineering newsletter actually needs.

## Decisions for the owner

**Where should the feeds live?** They are served from `pu-shd.github.io/upcoming`. The
predecessor used `upcoming.orfe.princeton.edu` via a CNAME. If anything institutional is
going to ingest these, a princeton.edu host is more durable than an org-scoped Pages path —
and moving it later means every consumer updates a URL. Cheap now, expensive after
adoption.

**Who should be told, and how?** Failures surface as a red run and a GitHub issue. That
works for whoever watches this repository and nobody else. If a department should hear that
its own feed has been down for six hours, that needs a webhook secret, a route, and a
decision about who is actually on the hook.

**Should FPOs reach the newsletter?** An ECE final public oral currently appears in the
`engineering-newsletter` purpose; the real edition has none. The lever exists —
`purpose_overrides` on `ece` — but it is an editorial call, so it is visible rather than
silently filtered.

## Larger, if the project grows

**An external check.** The one gap no in-repo work closes: if every scheduled workflow is
disabled at once, nothing notices. A scheduled fetch of `status.json` from anywhere else at
all, asserting its age, is a few lines wherever you already run something on a timer.

**Curated series names.** `citp`'s `series` is its comma-joined `CATEGORIES` where the
newsletter writes "CITP Seminars". Their names are editorial and not in the feed. A
per-source series alias map would close it, at the cost of another vocabulary to maintain.

**Modality.** The feeds carry nothing for the editors' `(hybrid)` marker. It may be
derivable from `rawEventDetails`, which we already capture — worth measuring before
building.

**Holiday handling in the simulator.** The edition rule is weekly-only by decision. the predecessor's
`src/newsletter.py` models blackouts and exceptions; porting that would mean the simulator
reproduces a shifted edition without a manual override. It would also mean either a Python
model the page consumes, or a second implementation that can drift — which is the trade
that was already made once.
