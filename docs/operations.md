# Operations

How to run this project from a machine that has nothing on it, and what to do when it goes
wrong. The other documents explain why the system is shaped the way it is; this one is the
sequence of commands.

## What lives where

The question this answers: **if this laptop were wiped tonight, what would be lost?**

| Thing | Where it lives | Survives a wiped machine? |
|---|---|---|
| Code, config, fixtures, docs, the ruleset definition | `main` on GitHub | Yes |
| The last published tree and the fetch state (`state/upstream.json`) | the `published` branch | Yes. Every build reads it; see [ci-cd.md](ci-cd.md#the-published-branch) |
| Publishing, the watchdog, the heartbeat | GitHub Actions, on GitHub's runners | Yes. They do not need this machine and keep running while it is gone |
| `BOT_BYPASS_HEADER` (and `HEARTBEAT_DEPLOY_KEY`, optional and not set) | repository secrets | Yes, but **write-only**: GitHub never shows a secret again. See [Secrets](#secrets) |
| Pages, the `github-pages` environment, the `main` ruleset | repository settings | Yes. Listed in [GitHub settings](#github-settings) so they can be rebuilt if the repository ever is |
| `.venv`, the Docker test image, `dist/`, `.feeds/` | this checkout, gitignored | No, and nothing is lost: `make bootstrap` rebuilds them |
| Homebrew tools, the Docker VM, the `gh` login | this machine | No. [A fresh machine](#a-fresh-machine) reinstalls them |

So the only thing a wiped machine can cost is a secret's *value* that was never written
down anywhere else. Neither is irreplaceable -- both can be regenerated -- but
`BOT_BYPASS_HEADER` comes from outside this project, so keep its value in a password
manager.

Before wiping, check that nothing is only local:

```sh
git status --short            # nothing uncommitted
git stash list                # no stashes
git log --branches --not --remotes --oneline   # no unpushed commits
```

## A fresh machine

macOS with Homebrew. On Linux, install the same tools with the distribution's package
manager; `make bootstrap` checks the same things either way.

```sh
# 1. Tools. Versions match CI: Python 3.12+, Node 22+.
brew install git python@3.12 node gh colima docker docker-compose
brew link docker-compose      # Homebrew can leave it unlinked; `docker-compose` then
                              # reports "command not found" although it is installed
colima start                  # the Docker VM; `colima status` checks it

# 2. GitHub.
gh auth login                 # needs `repo` and `workflow` scopes

# 3. The checkout.
gh repo clone pu-shd/upcoming
cd upcoming
make bootstrap                # checks every tool, then builds .venv and the test image
```

`make bootstrap` lists every missing or unusable tool in one pass and changes nothing until
they are all fixed, so run it, install what it names, and run it again. It is safe to re-run
at any time. `make bootstrap DRY_RUN=1` checks the tools and says what it would build. It
ends with `bootstrap: ready` -- if it does not say that, it did not finish.

**If the checkout is under `~/Downloads`, `~/Documents` or `~/Desktop`**, macOS keeps those
folders private and Colima's VM cannot mount them. The symptom is
`error while creating mount source path ... operation not permitted`, and bootstrap's last
step reports it. Either move the checkout (for example to `~/src`), or give the VM access:
System Settings > Privacy & Security > Full Disk Access, add `/opt/homebrew/bin/limactl`,
then `colima restart`.

Nothing in that sequence needs a secret. The suite and `make publish` run offline against
committed fixtures with a visibly fake bypass header. Only a live local run --
`make publish FETCH=1 ENRICH=1` -- needs the real `BOT_BYPASS_HEADER`.

### After a Homebrew upgrade

A Homebrew Python upgrade deletes the interpreter `.venv` links to and leaves the directory
behind. `make` rebuilds a venv whose interpreter is gone, and `make bootstrap` says that is
why it is reinstalling. Nothing to do by hand.

## Secrets

Two, both repository secrets. Neither has a default anywhere, and a contract test refuses a
workflow that gives `BOT_BYPASS_HEADER` a plausible-looking one.

### `BOT_BYPASS_HEADER`

- **What it is:** one *whole* HTTP header line, `Name: value`, sent with every event-page
  scrape. The header's name is part of the secret so that neither half is written in this
  repository.
- **Who needs it:** Publish's enrichment step only. Every feed fetch answers a plain
  request; event pages on the Site Builder hosts answer 403 without it. Tests, `make check`
  and offline builds use a fake value.
- **Where the value comes from:** outside this project -- it is a detail of someone else's
  bot-protection arrangement. The issuer is deliberately not recorded here; keep it beside
  the value in your password manager.
- **Set or rotate it:** `gh secret set BOT_BYPASS_HEADER` and paste the line at the prompt.
  Never pass it as an argument: that puts it in shell history.
- **If it is lost:** CI keeps working, because GitHub still holds it. You only need the
  value to run live enrichment locally, or to re-create the secret in a new repository.
- **If it stops working:** the enrichment success-rate gate fails the affected sources
  rather than publishing them unenriched. See [failure-modes.md](failure-modes.md).

### `HEARTBEAT_DEPLOY_KEY`

- **What it is:** the private half of a write deploy key titled *heartbeat keepalive*. The
  `main` ruleset lets deploy keys bypass it, and the heartbeat pushes its keepalive commit
  with this key -- the workflow token cannot, because GitHub refuses the Actions app as a
  ruleset bypass.
- **Status: not set, by choice.** The repository runs without it.
- **Who needs it:** the heartbeat, only on the rare day the repository has been quiet for
  35 days. Without it the keepalive push fails, loudly, with a message naming the secret,
  and the heartbeat goes red every day until something lands on `main`. It still
  re-enables any stopped schedule and opens its issues on those days.
- **What to do instead:** when the heartbeat goes red with *HEARTBEAT_DEPLOY_KEY is unset*,
  merge any pull request -- a docs fix will do -- before day 60 of quiet. That is the
  activity the keepalive would have supplied.
- **To add it later:** nothing else uses the key. To create or rotate it:

  ```sh
  ssh-keygen -t ed25519 -N '' -C 'pu-shd/upcoming heartbeat keepalive' -f heartbeat_key
  gh repo deploy-key add heartbeat_key.pub --allow-write --title "heartbeat keepalive"
  gh secret set HEARTBEAT_DEPLOY_KEY < heartbeat_key
  rm heartbeat_key heartbeat_key.pub
  ```

  Then delete the old key: `gh repo deploy-key list`, `gh repo deploy-key delete <id>`.
  The private key never needs to exist anywhere but the secret.

## GitHub settings

Settings that live in the repository's configuration rather than in this tree. They survive
a wiped machine; this list is for rebuilding them if the repository itself is ever moved or
re-created.

| Setting | Value | How to restore |
|---|---|---|
| Pages | Source: **GitHub Actions**. Served at `https://pu-shd.github.io/upcoming/` | Settings > Pages |
| Environment `github-pages` | Deployment branches: `main` only | Created by the first Pages deploy; set the branch policy under Settings > Environments |
| Ruleset `main` | `.github/rulesets/main.json`: pull request required, the three Tests jobs (`pytest`, `lint`, `container`) required, no force-push, no deletion; deploy keys may bypass | `make protect` |
| Deploy key | None. Optional; see [`HEARTBEAT_DEPLOY_KEY`](#heartbeat_deploy_key) | -- |
| Secrets | `BOT_BYPASS_HEADER` (`HEARTBEAT_DEPLOY_KEY` optional, not set) | [Secrets](#secrets) |
| Actions workflow permissions | Default **read**. Every workflow declares the permissions it needs | Settings > Actions > General |
| Label `watchdog` | Carried by every issue Verify and the heartbeat open | `gh label create watchdog` |

The heartbeat checks the ruleset daily against `.github/rulesets/main.json` and opens a
`watchdog` issue if main is missing any of it, so a ruleset disabled in the settings page is
reported rather than discovered.

## Changing `main`

Every change reaches `main` through a pull request whose `pytest`, `lint` and `container`
checks have passed. That applies to admins too. The usual sequence:

```sh
git switch -c my-change
# ... edit, then make test lint typecheck docker-test
git push -u origin my-change
gh pr create --fill
gh pr checks --watch
gh pr merge --squash --delete-branch      # or --rebase to keep separate commits
```

To change the protection itself, edit `.github/rulesets/main.json` in a pull request, merge
it, then run `make protect` (`DRY_RUN=1` first to see what it will do). It updates the
ruleset in place and then reads back what GitHub enforces, failing if `main` does not carry
all of it.

**In an emergency** -- CI itself is broken and a fix cannot pass its own checks -- set the
ruleset to *Disabled* under Settings > Rules, merge the fix, and run `make protect` to
restore it. The heartbeat files an issue if it is left off.

## Runbooks

Each one starts from what you see.

### Publish is red: "a source failed; the tree published with its last good copy"

The site is up. One or more sources failed, are serving their last good feed marked stale,
and everything else published normally. The run is red so that this is noticed.

1. Find the source: the job summary, or

   ```sh
   gh run view <run-id> --log | grep -E 'could not fetch|source\(s\) failed'
   ```

2. Fetch the feed yourself to see what the server is doing:

   ```sh
   curl -sS -o /dev/null -D - -A 'pu-shd-upcoming/0.1 (+https://github.com/pu-shd/upcoming)' <feed_url>
   ```

3. Decide by what it says:
   - **5xx, timeouts, TLS errors that clear within hours:** wait. The last good feed covers
     it, and Verify starts failing only after six hours stale.
   - **A permanent block** -- `cf-mitigated: challenge` from Cloudflare, a login page, a
     host that is gone: [take the source offline](#taking-a-source-offline-and-bringing-it-back)
     and ask its owners to exempt the feed URL.
   - **It works from campus but times out from Actions:** see the next runbook.
   - **The feed parses differently** (a new date format, a missing field): that is a code
     change. Capture the feed as a fixture first and write the failing test.

### A source works from campus but times out from Actions

Seen with `cs` on 2026-10-21: every connection from GitHub's runners timed out at the TCP
connect while the feed answered instantly on campus. Fetching from a Princeton address
proves nothing about reachability from outside, so do not read a working `curl` on campus
as "the server is fine". The fix is upstream: ask the department's IT whether they have
started filtering off-campus or cloud traffic, and to allow the feed URL. Leave the source
serving its last good copy unless the block is confirmed permanent.

### Taking a source offline, and bringing it back

In `config/sources.yaml`, under the source:

```yaml
    status: unavailable
    reason: >
      What is wrong, since when, how it was established, and what would fix it.
```

An unavailable source may not carry a `feed_url`, so move it into a comment beside the
status. Keep everything else -- that is what makes bringing it back a two-line change.
`kellercenter` is the worked example. Then update the counts the tests and README state,
and merge as usual. The source is published as `disabled` with its reason in
`status.json`.

To bring it back: remove `status` and `reason`, restore `feed_url`, update the counts, and
merge.

### Every Publish run is "cancelled" and has no jobs

Something is holding the `publish` concurrency group. GitHub keeps one *pending* run per
group, so each new tick cancels the previous pending one, and none ever gets a runner.

```sh
gh run list --workflow publish.yml --status waiting
gh run list --workflow publish.yml --status in_progress
```

Seen on 2026-10-06: a deploy job sat in the `github-pages` environment gate for 18 hours
with nothing to approve. `timeout-minutes` does not cover an environment wait. A plain
`gh run cancel <id>` did nothing; this cleared it:

```sh
gh api -X POST repos/pu-shd/upcoming/actions/runs/<id>/force-cancel
```

The next scheduled Publish then runs normally.

### Verify is red, or a `watchdog` issue opened

Verify fetches the live site from outside and compares it with what `status.json` promises.
Its issue says which check failed. The common ones:

- **`status.json` is too old** (over 90 minutes): Publish is not completing -- look at the
  Publish runs, and at the runbook above.
- **A feed is stale past six hours:** a source has been failing that long; see the first
  runbook.
- **Counts differ from the manifest:** a partial deploy. Re-run Publish by hand.

The issue closes itself when a later run passes.

### The heartbeat is red

Its summary says which half:

- **A scheduled workflow had stopped:** it has already re-enabled it and opened an issue.
  If it recurs, the repository is going quiet faster than the 35-day keepalive expects.
- **main is missing its branch protection:** run `make protect`.
- **`HEARTBEAT_DEPLOY_KEY is unset`:** the repository has been quiet 35 days. Merge any
  pull request before day 60, or add the key; see [the secret](#heartbeat_deploy_key).

### Running Publish by hand

```sh
gh workflow run publish.yml                          # what the schedule does
gh workflow run publish.yml -f ignore_cadence=true   # refetch every source now
gh workflow run publish.yml -f rescrape=true         # re-read every event page
gh workflow run publish.yml -f allow_large_diff=true # accept a feed losing most events
gh workflow run publish.yml -f enrich=false          # skip event-page scraping
gh run watch "$(gh run list --workflow publish.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

`allow_large_diff` overrides the gate that stops a truncated upstream response from
replacing a good feed. Use it only once you have established that the drop is real.

### The container tests fail en masse but pass locally

Check which image ran:

```sh
docker image inspect pu-shd-upcoming:dev --format '{{.Created}}'
```

`docker-compose run` reuses whatever image carries the name. Rebuild with
`docker-compose build tests` (or `make docker-test`, which builds first). This repository
names its images `pu-shd-upcoming:*` because pu-orfe/upcoming, in a directory also called
`upcoming`, once overwrote an unnamed one and 148 tests failed for it.
