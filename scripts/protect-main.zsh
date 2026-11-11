#!/usr/bin/env zsh
# Apply .github/rulesets/main.json to this repository as the ruleset protecting main.
#
#   make protect                  # create or update the ruleset, then confirm it applies
#   make protect DRY_RUN=1        # say what would change; change nothing
#
# Safe to re-run: it finds the ruleset by name and updates it in place, or creates it when
# there is none. It touches the live repository, so --dry-run exists and the result is
# read back afterwards -- a PUT that GitHub accepted is not the same as a branch that is
# protected, and only the second is what this script is for.
#
# Needs gh logged in as a repository admin, and python3 (any 3.12+).

set -euo pipefail

usage() {
  print -r -- "usage: ${0:t} [--dry-run]"
}

dry_run=0
for arg in "$@"; do
  case $arg in
    --dry-run) dry_run=1 ;;
    -h|--help) usage; exit 0 ;;
    *) print -u2 -r -- "${0:t}: unknown argument: $arg"; usage >&2; exit 2 ;;
  esac
done

root=${0:A:h:h}
spec=$root/.github/rulesets/main.json
cd "$root"

fail() {
  print -u2 -r -- "protect: $1"
  exit 1
}

[[ -f $spec ]] || fail "no ruleset at $spec"
whence -p gh >/dev/null || fail "gh is not installed (brew install gh)"
whence -p python3 >/dev/null || fail "python3 is not installed (brew install python@3.12)"
gh auth status >/dev/null 2>&1 || fail "gh is not logged in (gh auth login)"

repo=${UPCOMING_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}
[[ -n $repo ]] || fail "cannot tell which repository this is"
name=$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["name"])' "$spec")

existing=$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id")
if [[ -n $existing ]]; then
  method=PUT endpoint="repos/$repo/rulesets/$existing"
  action="update ruleset '$name' (id $existing)" done_msg="updated ruleset '$name' (id $existing)"
else
  method=POST endpoint="repos/$repo/rulesets"
  action="create ruleset '$name'" done_msg="created ruleset '$name'"
fi

if (( dry_run )); then
  print -r -- "protect: would $action on $repo from ${spec#$root/}"
  print -r -- "protect: dry run; nothing changed"
  exit 0
fi

gh api --method "$method" "$endpoint" --input "$spec" >/dev/null
print -r -- "protect: $done_msg on $repo"

# Read back what GitHub now enforces on main and compare it with the spec, using the same
# comparison the heartbeat runs every day.
live=$(gh api "repos/$repo/rules/branches/main")
python=$root/.venv/bin/python
[[ -x $python ]] || python=python3
gaps=$(PYTHONPATH=$root "$python" -I -c '
import json, sys
sys.path.insert(0, sys.argv[1])
from upcoming.heartbeat import parse_rules, protection_gaps
gaps = protection_gaps(parse_rules(json.loads(sys.argv[2])), json.load(open(sys.argv[3])))
print("\n".join(gaps))
' "$root" "$live" "$spec")

if [[ -n $gaps ]]; then
  print -u2 -r -- "protect: GitHub accepted the ruleset but main does not carry all of it:"
  print -u2 -r -- "$gaps"
  exit 1
fi
print -r -- "protect: main carries every rule in ${spec#$root/}"
