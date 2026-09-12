# Failure modes

Every entry is a way this system can be wrong. The ones that matter produce output
indistinguishable from working correctly.

## The four the predecessor shipped

These are not hypothetical. Each was found in `pu-orfe/upcoming`'s published output and
each drove a mechanism here.

| Failure | What it looked like | Mitigation |
|---|---|---|
| **A total scrape blackout reads as a clean run** | Its fetch helper returns `""` for a 403, so every page failing produces zero errors and a feed with nothing enriched | `ScrapeStats.success_rate` measures *reaching* the page, not finding a value. The gate is a rate, so a count of zero errors cannot hide it. |
| **A placeholder credential authenticates nothing** | `os.getenv("BOT_BYPASS_HEADER_VALUE", "1")` sends `1`, gets 403 everywhere, reports success | The secret holds a whole header line and has no default anywhere. A contract test refuses any workflow that assigns it a value that is neither the secret store nor visibly fake. |
| **A skipped step is green** | The pipeline died before its Pages steps, so they were *skipped*, the site went stale, and the workflow list stayed clean | Deploy is a separate job with `always()`. A watchdog runs on its own schedule against the live origin. |
| **A filtered feed identical to the unfiltered one** | Excluding the literal string `FPO` finds ORFE's four and neither of `mae`'s or `ece`'s spellings | A canonical tag vocabulary, and a gate that warns about every category spelling nothing recognises |

## Silent-success failures, and what catches each

| Failure | Caught by | Residual risk |
|---|---|---|
| Upstream truncates a feed to a handful of events | `gate_diff_threshold` — refuses a build losing most of the previous feed | **Never fired in production.** `--allow-large-diff` overrides it. |
| A source's mapping is wrong but schema-valid | Per-source rule-distribution tests. A reordering that reclassifies four events fails even though output stays valid | Only covers sources with a fixture |
| Two unrelated events share a UID | `id` is source-scoped; a `guid` dedupe key is refused at load | — |
| A tag alias goes stale when a series is renamed | `gate_unmapped_tags` warns with the spelling and a count | **A warning, not a failure.** It went unread for a day and four ORFE seminars were silently absent from `combos/seminars`. |
| A predicate names a value nothing produces | Load error, for tags and purposes both | Other fields are not vocabulary-checked |
| A feed is stale rather than fresh | `status.json` is the only file with a clock; `lastSuccessAt` carried across failures | A consumer that does not read it cannot tell |
| Enrichment silently stops filling a field | `min_enrich_success_rate`, per source | A warm run attempts nothing, so there is no rate to breach — correct, but it means the gate is quiet on most runs |
| The publisher stops running entirely | The watchdog, within 90 minutes | See **What nothing catches** below |

## Degradation, by design

Three things fail *softly* on purpose. Each is a choice about which bad outcome is worse.

**A failed source keeps serving its last good feed.** A blank departmental listing is worse
than one a few hours old. The staleness and its reason are published, because an
unannounced stale feed is worse than both. Combined feeds are built from the last good copy
rather than omitting the source, which would quietly shrink a feed a consumer relies on.

**A source not due for a refetch is reported current, not stale.** It is serving exactly
what its own configuration asked for. Conflating the two would report eight of twelve
departments as degraded on most ticks, and a signal that always fires is one everybody
learns to ignore.

**Staleness is graded by duration.** Under six hours warns; past it fails and files an
issue. A department whose server rebooted needs nobody woken; one stale for two days is an
outage nobody has noticed. Reporting both the same way means either the reboot pages or
the outage stays green.

## What nothing catches

Stated plainly, because each looks like something that is handled.

**The watchdog cannot watch itself.** Publishing, the watchdog and the heartbeat are all
GitHub scheduled workflows. If GitHub disables them — 60 days of repository inactivity, an
org change, a billing lapse — they stop together, and the heartbeat that exists to prevent
exactly that goes with them. The site would serve its last deploy indefinitely with
`generatedAt` receding into the past. The keepalive makes this unlikely, not impossible.
**The only real answer is one scheduled fetch of `status.json` from anywhere else.**

**The bypass credential's necessity is unverified.** A run with a deliberately wrong header
produced byte-identical output, so these hosts are not currently behind the bot challenge.
The gate that would catch a real blackout has never fired against a real one.

**Three safety mechanisms have never fired in production**: the diff threshold, the
enrichment health gate, and the deploy-when-a-source-failed path. Each is unit-tested and
each is a hypothesis until it runs for real.

**The simulator's edition rule has no holiday handling.** It implements the weekly schedule
only. The real 7 September 2026 edition published on the Tuesday for Labor Day; reproducing
it means overriding the publication date. The page says so, and a test pins the divergence
so it cannot later be mistaken for a bug.

**Upstream data quality is not ours to fix.** `ai` publishes four events whose `SUMMARY` is
`ORFE Talks & Seminars Flyers Fall 2026 Colloquium` — a flyers *listing page*, entered as
an event. We tag it `listing` and publish the publisher's wording rather than inventing a
title, because synthesizing one would hide their data-entry problem rather than surface it.

## Failure classes by layer

| Layer | Fails how | Blast radius |
|---|---|---|
| Config | Load error, before anything runs | Everything — nothing publishes |
| One source | `BuildResult(status="failed")` with diagnostics | That source only; others publish; last good feed served |
| Gate | Failure fails the source; warning publishes with a note | One source |
| Schema | Fails the source | One source |
| Combine | A missing input source is substituted from its last good copy | One combined feed, marked stale |
| Publish | Non-zero exit; the tree is not written | Everything, and the deploy is skipped deliberately |
| Deploy | A separate job, so it reads as a deploy failure | The site keeps serving the previous deploy |
