# CI and CD

Four workflows. They are deliberately separate: a lint failure must never be able to stop a
departmental feed from updating.

| Workflow | Trigger | Job |
|---|---|---|
| `tests.yml` | push, pull request | Correctness. Three jobs: `pytest`, `lint`, `container` |
| `publish.yml` | schedule, **after Tests passes**, dispatch | Builds and deploys the tree |
| `verify.yml` | hourly at :25, dispatch | Checks the live origin from outside |
| `heartbeat.yml` | daily at 05:47, dispatch | Keeps the schedules from being disabled |

## Publishing

### Cadence

```
0,20,40 12-23 * * 1-5    every 20 minutes, weekday daytime Eastern
0       0-11  * * 1-5    hourly overnight
0       *     * * 0,6    hourly at weekends
```

Departmental calendars are edited by people during working hours, so a uniform cadence
spends most of its runs proving nothing changed overnight while still being slower than it
needs to be when someone posts a seminar. The three expressions do not overlap — a test
expands them and fails on a collision, because a scheduled tick firing twice means two
publishes racing for one branch.

**That schedule is the fastest any source is polled, not the rate all of them are.** Each
source declares its own `cadence` and is refetched only once it has elapsed. Ten minutes of
slack stops an hourly cadence checked every twenty minutes from silently becoming eighty.

### The three-stage economy

Measured before any of this existed: about **4,244 requests a day** to departmental web
servers, of which ~4,100 were event-page scrapes returning identical markup.

1. **Cadence** decides whether to ask at all.
2. **Conditional requests** — stored `ETag` and `Last-Modified`, replayed as
   `If-None-Match` / `If-Modified-Since`. Eleven of the twelve measured in September
   answer `304` with no body.
3. **Enrichment windows** — `rebuild_after_hours` reuses the last scrape's values. ORFE
   went from 960 requests a day to roughly 80.

State lives at `state/upstream.json` in the published tree. Published rather than cached
because an Actions cache expires after a week, and losing it silently would restore the old
behaviour with nothing reporting the regression.

### The published branch

The previously published tree is an **input**, not only an output:

- a failed source serves its last good feed from it
- the diff gate compares against it
- combined feeds are built from a failed source's copy in it
- `lastSuccessAt` and the fetch validators are read from it

It is restored by checking out the `published` branch into `dist/`, not from a cache or a
Pages artifact — both of those expire, and losing this would turn a brief upstream outage
into a blank departmental listing.

### Publishing waits for the suite

On a push, `publish.yml` is triggered by `workflow_run` on **Tests completing
successfully**, not by the push itself. The two used to start together and publishing
finished first — measured, 34 seconds before the suite it had not waited for — so a commit
could deploy and only then be found broken. The gates inside the build catch output that is
*invalid*; nothing there catches output that is valid and wrong, which is what a suite is
for.

Scheduled and manual runs carry no `workflow_run` payload and are unaffected. They publish
whatever is on `main`, which is the point of a schedule — and it is why this narrows the
window rather than closing it. **The only thing that keeps broken code off `main` is
branch protection**, which is a repository setting rather than anything in this tree.

CodeQL is deliberately not waited on: it is a security scan rather than a correctness gate,
and blocking a departmental feed on it would trade a real delay for no correctness the
suite does not already provide.

### Why the deploy is its own job

```yaml
deploy:
  needs: publish
  if: always() && needs.publish.result != 'cancelled' && needs.publish.result != 'skipped'
```

A step that does not run because an earlier step failed is reported as **skipped**, and
skipped reads as green. That is exactly how the predecessor's site went stale while its
workflow list stayed clean. `always()` is what lets one department's upstream outage
publish the other eleven.

The build step captures its exit code with `|| code=$?` rather than reading `$?` — the
default shell is `bash -e`, which `set -uo pipefail` does not clear, so a bare `$?` is
read only when the command succeeded. Exit 4 means "a source failed and the tree is
complete"; anything else means the tree was never written, and nothing is deployed.

Every run deploys, including runs where no feed changed, because `status.json` is the
liveness beacon. The feeds carry no timestamp, so an unchanged feed redeploys
byte-identically and the branch history shows only what really moved.

## The watchdog

`verify.yml` runs on its own schedule against the live origin and knows nothing about the
run that produced it. A check inside the publishing workflow would be skipped by the same
crash that skips the deploy.

It fetches `status.json`, checks its age, fetches every path the manifest promises, and
**compares the served event counts against the promised ones** — which is what catches a
partial deploy, where some paths updated and some did not and neither file alone looks
wrong. It also checks the root page, since every feed can serve perfectly while the root
404s.

It carries **no bypass credential**, deliberately: it must see exactly what an ordinary
consumer sees. Granting it one would let it pass where every real consumer is blocked.

Findings are graded. A failure files or updates one issue and closes it on recovery, so a
problem outlives the run that found it — a red run in a list of green ones is easy to miss
for a week.

## The heartbeat

GitHub disables scheduled workflows on a public repository after 60 days without repository
activity: it emails the owner, stops running them, and nothing in the repository reports
it.

Two mechanisms, because neither covers both halves:

- **Prevention** — commit a keepalive once the repository has been quiet past 35 days,
  leaving 25 days of slack. On a threshold rather than daily, so an actively developed
  repository never accumulates commits nobody asked for.
- **Detection** — ask the API whether each scheduled workflow is still `active`, and
  re-enable what GitHub stopped. A workflow somebody switched off by hand is reported too:
  turning the publisher off is legitimate, leaving it off silently is not.

**It cannot rescue itself.** If every scheduled workflow is disabled at once, this one goes
with them. That is inherent to running monitoring inside the thing being monitored.

## Secrets

One: `BOT_BYPASS_HEADER`, holding a **whole header line** (`Name: value`), so neither half
is written in this repository. It has no default anywhere. A contract test refuses any
workflow that assigns it a value that is neither the secret store nor visibly fake — a
placeholder credential reaching a real host is how the predecessor came to 403 on every
page while reporting a clean run.

## Local equivalents

```sh
make test           # the pytest job
make lint typecheck # the lint job
make docker-test    # the container job, byte-for-byte
make publish        # the publish job, offline from fixtures
make publish FETCH=1 ENRICH=1   # with the network
make verify         # the watchdog, against the live site
```

`make publish` is offline by default: a developer runs the same code path over known bytes
that CI runs over live ones.
