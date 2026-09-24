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

### Close the monitoring gap with healthchecks.io

The one failure nothing in this repository can catch: if GitHub disables every scheduled
workflow at once, publishing stops, the watchdog stops with it, and the heartbeat that
exists to prevent exactly that is disabled too. The site would serve its last deploy
indefinitely with `generatedAt` receding into the past.

A dead man's switch answers it, because it alerts on **absence** rather than on a signal.
That is the right shape here: the failure is that nothing ran.

Two checks, not one — they fail for different reasons and a single check would conflate
them:

| Check | Pinged by | Fires when |
|---|---|---|
| `upcoming-publish` | `publish.yml` on success | no publish completed inside its period |
| `upcoming-verify` | `verify.yml` when the site is healthy | the site stopped being served correctly, or the watchdog itself stopped |

```yaml
# at the end of the publish job
- name: Report to the dead man's switch
  if: steps.build.outputs.status == '0'
  run: curl -fsS -m 10 --retry 3 "${{ secrets.HEALTHCHECK_PUBLISH_URL }}"
```

Use the `/start` and `/fail` endpoints too, so a run that fails is reported as a failure
rather than waiting out the grace period as silence.

Set the period to the slowest cadence — hourly overnight, so 1 hour with a generous grace,
or the weekend schedule will alert every Saturday. Getting that wrong makes the check cry
wolf, which is worse than not having it.

Notes worth recording before adopting it:

- **The ping URL is the credential.** Anyone holding it can silence the alarm. It belongs
  in repo secrets, and the same contract test that guards `BOT_BYPASS_HEADER` should refuse
  a hardcoded one.
- **It can be self-hosted** if an external dependency for monitoring is itself unacceptable
  — which is a fair objection, since the monitor would then share a failure domain with
  whatever hosts it.
- **The free tier covers this** at two checks.

**Effort:** an hour, most of it choosing the period.

### A Codespace, so a developer can tinker with their own instance

Getting started currently means Python 3.12, Node 22, Docker, a virtualenv, and knowing
that `BOT_BYPASS_HEADER` holds a whole header line. None of that is written down as a
sequence; it is inferred from the Makefile and two workflows.

A `.devcontainer/` makes the working setup the *default* one, and it suits this project
better than most because **everything already runs offline**. A Codespace needs no
credential and no network to be useful: `make build`, `make publish` and the whole suite
work against committed fixtures, so a developer is looking at real output within a minute
of the container coming up.

What it needs:

```jsonc
// .devcontainer/devcontainer.json
{
  "image": "mcr.microsoft.com/devcontainers/python:3.12",
  "features": {
    "ghcr.io/devcontainers/features/node:1": { "version": "22" },
    "ghcr.io/devcontainers/features/docker-in-docker:2": {}
  },
  "postCreateCommand": "make install",
  "forwardPorts": [8000],
  "containerEnv": {
    // Visibly not a credential, and the suite reaches no network anyway.
    "BOT_BYPASS_HEADER": "x-codespace-placeholder: not-a-credential"
  }
}
```

Three things worth getting right rather than accepting the defaults:

- **Node belongs in it.** The simulator's logic is JavaScript and the suite drives it
  through Node. A container without it turns 78 tests into an error a newcomer has to
  diagnose on their first run.
- **Docker-in-Docker earns its weight** only because `make docker-test` is the path CI
  runs, and a developer who cannot run it locally will find out in CI instead. It is the
  slowest feature to build, so it is a real trade rather than an obvious win.
- **The placeholder credential should be visibly fake**, matching the Makefile's own. A
  Codespace with a real one would be a credential on a machine nobody audits, and the
  contract test that refuses a plausible-looking default exists for exactly this reason.

Worth adding at the same time: `make serve` already publishes and serves the site on 8000,
so forwarding that port means both pages and the simulator work in a Codespace with no
extra step.

**Effort:** an hour, plus however long the first container build takes to settle.

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

## Notifications

Today a failure is a red workflow run and a GitHub issue. That reaches whoever watches this
repository and nobody else — not the maintainer who is not looking, and not the editors who
depend on the output.

Two audiences with almost nothing in common, and conflating them is the way this goes
wrong. Maintainers want to know the machinery broke; editors want to know their edition is
ready and their deadline is coming. An editor who receives "gate_diff_threshold failed on
`cbe`" learns nothing and starts ignoring the sender.

### Maintainers

| Send when | Because |
|---|---|
| A source has been stale past its grade | The watchdog already computes this and files an issue; an issue nobody is subscribed to is a log entry |
| A scheduled workflow was found stopped | The heartbeat already detects and re-enables it, and that is worth knowing about even when repaired |
| The dead man's switch fired | healthchecks.io sends this itself, so it needs no work here beyond configuring the address |
| A build failed for a reason no gate anticipated | The `check` tier exists precisely for what we did not foresee |

Deliberately **not** on every red run. Publishing runs 48 times a day; a source failing for
an hour would send 48 emails, and the second one is already noise.

### Editors

| Send when | Contents |
|---|---|
| The submission deadline approaches | The existing 24-hour reminder, but pushed rather than only offered as a calendar file. The simulator already composes the text. |
| An edition closes | The listing as it stands, with the `fix first` rows called out — a title still unannounced is the editor's to chase, and the earlier they see it the better |
| A source they rely on has been dark for days | Only for a sustained outage, and named in their terms: "Chemical and Biological Engineering has published nothing since Tuesday" |

The listing is already generated with inline styling for exactly this — `exportDocument()`
produces a standalone HTML document, and `workflowmail` takes an `html` input, so the body
is a function call rather than new work.

### Delivery: three options

We have an Azure subscription appropriate for the purpose, and
[`pu-shd/workflowmail`](https://github.com/pu-shd/workflowmail) already exists in this
organisation.

| | **workflowmail (ACS)** | **workflowmail (Graph)** | **Resend.com** |
|---|---|---|---|
| Credential | **None stored.** Azure OIDC + Managed Identity | OAuth refresh token, held in Azure | An API key in repo secrets |
| Sender | `DoNotReply@<guid>.azurecomm.net` | **Any O365 mailbox** — a real princeton.edu address | A verified domain |
| Setup | Automatic; deploy creates the ACS resources | One device-code login at deploy | Domain verification, DNS records |
| Ongoing | None | Automatic weekly token heartbeat | Key rotation |
| Guardrails | Recipient restrictions, rate limiting, subject validation, already built | Same | Ours to build |
| Calling it | `uses: pu-shd/workflowmail/.github/workflows/email-reusable.yml@main` | Same | A `curl` and a secret |

**Recommendation: `workflowmail`, on the Graph backend for editors and ACS for
maintainers.**

The reasoning is about credentials and about who the sender appears to be.

**Secretless matters here more than usual.** This repository's central premise is that
silence must never equate to success, and its worst historical failure was a credential
with a plausible default that authenticated nothing while reporting a clean run. An OIDC
flow with no stored key removes that class of failure rather than guarding against it. A
Resend key is a fourth secret to rotate, and one whose absence would fail in exactly the
quiet way we work to avoid.

**The sender address matters for editors and not for maintainers.** A deadline reminder
arriving from `DoNotReply@8f3a…azurecomm.net` looks like spam and will be filtered; from a
real princeton.edu mailbox it does not. Maintainers will not care, so ACS is fine for them
and needs no O365 consent.

**The rate limiting is not incidental.** A notification loop is a way to mail an entire
department a hundred times, and `workflowmail` has recipient restrictions and rate limiting
already. Building those on top of Resend is the part of the work that would actually take
time.

Where Resend would win is if this ever needs to send to people **outside** the university,
or wants delivery analytics. Neither is in scope, and adopting it for a future that may not
arrive would mean carrying a secret for it in the meantime.

### Order to build

1. **healthchecks.io** first — smallest, closes a documented gap, and its own alerting
   covers the maintainer case well enough to defer the rest.
2. **Maintainer email** on the sustained-failure conditions above, via `workflowmail` on
   ACS. One reusable-workflow call from `verify.yml` and `heartbeat.yml`.
3. **A recipient list in config**, not in a workflow. Editors change; a workflow edit to
   add one is a pull request nobody should need to make. `config/notify.yaml`, validated at
   load like every other vocabulary, so a malformed address is a load error rather than a
   silent non-delivery.
4. **Editor email** last, on the Graph backend, once there is somebody to send it to and an
   address to send it from.

**Effort:** an hour for the switch; a day for maintainer mail; a second day for editors,
most of it Azure configuration rather than code.

## Larger, if the project grows

**Curated series names.** `citp`'s `series` is its comma-joined `CATEGORIES` where the
newsletter writes "CITP Seminars". Their names are editorial and not in the feed. A
per-source series alias map would close it, at the cost of another vocabulary to maintain.

**Modality.** The feeds carry nothing for the editors' `(hybrid)` marker. It may be
derivable from `rawEventDetails`, which we already capture — worth measuring before
building.

**A speaker written into `DESCRIPTION`.** `nam` puts one there in a labelled field —
`Speaker: Taylor Webb, Department of Psychology, Princeton University` — and nothing reads
a speaker out of `DESCRIPTION`. The two routes that exist are a `SUMMARY` rule chain and
page enrichment, and neither reaches it, so the DaIS newsletter's first entry loses a
speaker line the hand-written edition has. The same suppression hides the blurb, since the
content opens with that labelled field.

Measure first: if this is one source's convention it is a per-source `enrich` selector
against its event page, which is config. If several sources do it, it is a description
rule chain — a third place a field can come from, and worth the weight only once it is
paying for itself more than once.

**Holiday handling in the simulator.** Each publication now declares its own weekday,
window and layout, but the rule within a publication is still weekly-only by decision. the predecessor's
`src/newsletter.py` models blackouts and exceptions; porting that would mean the simulator
reproduces a shifted edition without a manual override. It would also mean either a Python
model the page consumes, or a second implementation that can drift — which is the trade
that was already made once.
