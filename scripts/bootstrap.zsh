#!/usr/bin/env zsh
# Make a fresh clone, or a machine Homebrew has just upgraded, ready to work on.
#
#   make bootstrap                # check the tools, then build what is missing
#   make bootstrap DRY_RUN=1      # check the tools and say what would be built
#
# Checks every tool first and reports all the problems at once, rather than failing on the
# first and making you rerun it once per missing tool. Changes nothing until every check
# passes. Safe to re-run: each step is idempotent, and a working .venv is left alone.
#
# Touches only this checkout and the local Docker daemon -- never GitHub, never the site.
# docs/operations.md walks through installing the tools it asks for.

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
cd "$root"

# Matched to CI: tests.yml runs Python 3.12 and Node 22.
min_python=(3 12)
min_node=22

problems=()

need() {
  local tool=$1 fix=$2
  whence -p "$tool" >/dev/null || problems+=("$tool is not installed -- $fix")
}

need git "brew install git"
need make "xcode-select --install"
need python3 "brew install python@3.12"
need node "brew install node"
need docker "brew install docker colima && colima start"
need docker-compose "brew install docker-compose && brew link docker-compose"
need gh "brew install gh && gh auth login"

if whence -p python3 >/dev/null; then
  version=$(python3 -c 'import sys; print("%d %d" % sys.version_info[:2])')
  have=(${=version})
  if (( have[1] < min_python[1] || (have[1] == min_python[1] && have[2] < min_python[2]) )); then
    problems+=("python3 is ${have[1]}.${have[2]}; ${min_python[1]}.${min_python[2]}+ is required -- brew install python@3.12")
  fi
fi

if whence -p node >/dev/null; then
  node_major=$(node --version)
  node_major=${${node_major#v}%%.*}
  if (( node_major < min_node )); then
    problems+=("node is $node_major; $min_node+ is required for the simulator tests -- brew install node")
  fi
fi

if whence -p docker >/dev/null && ! docker info >/dev/null 2>&1; then
  problems+=("docker cannot reach a daemon -- start one: colima start")
fi

if whence -p gh >/dev/null && ! gh auth status >/dev/null 2>&1; then
  problems+=("gh is not logged in -- gh auth login")
fi

if (( ${#problems} )); then
  print -u2 -r -- "bootstrap: ${#problems} problem(s); nothing was changed:"
  for problem in "${problems[@]}"; do
    print -u2 -r -- "  - $problem"
  done
  exit 1
fi

step() {
  local description=$1
  shift
  if (( dry_run )); then
    print -r -- "bootstrap: would $description"
    return 0
  fi
  print -r -- "bootstrap: $description"
  "$@"
}

# A venv directory whose interpreter is gone -- what a Homebrew Python upgrade leaves --
# is rebuilt by make's own rule. This only says so, because "why is it reinstalling
# everything" deserves an answer.
if [[ -d .venv && ! -x .venv/bin/python ]]; then
  print -r -- "bootstrap: .venv's interpreter is gone (usually a Homebrew Python upgrade); it will be rebuilt"
fi

step "install the package and its dev tools into .venv" make install
step "validate the source registry" make check
step "build the test image" docker-compose build tests

# The bind mount is the step that fails on macOS when the checkout sits in a folder the
# Docker VM may not read (~/Downloads, ~/Documents, ~/Desktop), with an error about
# creating the mount source path. Checked here rather than discovered mid-test.
mount_check() {
  if ! docker-compose run --rm tests true; then
    print -u2 -r -- "bootstrap: Docker cannot mount $root."
    print -u2 -r -- "  On macOS with Colima, give limactl access to this folder (System Settings >"
    print -u2 -r -- "  Privacy & Security > Full Disk Access, add /opt/homebrew/bin/limactl), then"
    print -u2 -r -- "  run 'colima restart' -- or keep the checkout outside ~/Downloads, ~/Documents"
    print -u2 -r -- "  and ~/Desktop."
    return 1
  fi
}
step "check Docker can mount this checkout" mount_check

if (( dry_run )); then
  print -r -- "bootstrap: dry run; nothing changed"
else
  print -r -- "bootstrap: ready"
fi
