# Recipes must not depend on the developer's interactive shell.
SHELL := /bin/bash

VENV        ?= .venv
PY          := $(VENV)/bin/python
REGISTRY    ?= config/sources.yaml
COMPOSE     ?= docker-compose

# macOS portability rules these recipes follow, because macOS is the primary development
# target while CI runs on Linux:
#   * no `sha256sum`  (macOS has `shasum -a 256`) -- hashing lives in hashlib inside the
#     package, so the local and CI paths are byte-identical by construction
#   * no `getent`, `readlink -f`, `grep -P`, `date -d`
#   * no `sed -i` without a backup suffix
# A test asserts these stay absent from the Makefile.

.PHONY: help bootstrap protect venv install sources check build build-fixtures publish serve verify test \
        test-cov \
        lint typecheck fmt \
        docker-build docker-test docker-lint docker-sources clean

help:
	@echo "Setup"
	@echo "  make bootstrap      check the tools, then build .venv and the test image"
	@echo "  make bootstrap DRY_RUN=1   check the tools; say what would be built"
	@echo "  make install        create $(VENV) and install the package with dev extras"
	@echo "  make protect        apply .github/rulesets/main.json to the repository (admin)"
	@echo "  make protect DRY_RUN=1     say what protect would change"
	@echo ""
	@echo "Inspect"
	@echo "  make sources        print the resolved registry: what each SUMMARY field means"
	@echo "  make check          validate the registry and exit non-zero on any problem"
	@echo ""
	@echo "Build"
	@echo "  make build SOURCE=orfe    build one source from its committed fixture"
	@echo "  make build SOURCE=orfe ENRICH=1   also scrape its event pages (network)"
	@echo "  make build-fixtures       build orfe and mae -- the inversion, both ways"
	@echo "  make publish              build every live source and every combo into dist/"
	@echo "  make serve               publish, then serve dist/ at http://localhost:8000"
	@echo "  make publish FETCH=1      fetch each source's ICS first (network)"
	@echo ""
	@echo "Watch"
	@echo "  make verify               check what the published site is actually serving"
	@echo ""
	@echo "Verify"
	@echo "  make test           pytest"
	@echo "  make lint           ruff check + format check"
	@echo "  make typecheck      mypy"
	@echo ""
	@echo "Container"
	@echo "  make docker-test    run the suite in the container (the CI path)"
	@echo "  make docker-lint    run lint and types in the container"
	@echo "  make docker-sources print the registry from inside the container"

# Keyed on the interpreter, not the directory. A Homebrew Python upgrade deletes the
# interpreter a venv links to and leaves the directory behind; a rule on the directory
# then calls a python that no longer exists. make treats a dangling link as missing, so
# this rebuilds exactly that case and leaves a working venv alone.
$(PY):
	rm -rf $(VENV)
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip

venv: $(PY)

# Both scripts are zsh, both take --dry-run, and docs/operations.md says when to run each.
bootstrap:
	zsh scripts/bootstrap.zsh $(if $(DRY_RUN),--dry-run,)

protect:
	zsh scripts/protect-main.zsh $(if $(DRY_RUN),--dry-run,)

install: $(PY)
	$(PY) -m pip install --quiet -e '.[dev]'
	@echo "installed; run 'make check' to validate the registry"

# BOT_BYPASS_HEADER holds a WHOLE header line, "Name: value", so the header name lives in
# the secret rather than in this repository. It has no default anywhere: a placeholder
# credential is how the predecessor's pipeline came to 403 on every event page while
# reporting success. These targets pass a visibly fake line because they only prove that
# config *loads*.
sources: $(PY)
	@BOT_BYPASS_HEADER="$${BOT_BYPASS_HEADER:-x-make-placeholder: not-a-credential}" \
		$(PY) -m upcoming.cli --registry $(REGISTRY) sources

check: $(PY)
	@BOT_BYPASS_HEADER="$${BOT_BYPASS_HEADER:-x-make-placeholder: not-a-credential}" \
		$(PY) -m upcoming.cli --registry $(REGISTRY) check

# Builds from committed fixtures, so it reaches no network. Fetching arrives with the
# enrichment layer; until then --feed is required, which keeps the offline guarantee
# structural rather than a convention.
build: $(PY)
	@if [ -z "$(SOURCE)" ]; then echo "usage: make build SOURCE=<slug> [FEED=<path>]" >&2; exit 2; fi
	@BOT_BYPASS_HEADER="$${BOT_BYPASS_HEADER:-x-make-placeholder: not-a-credential}" \
		$(PY) -m upcoming.cli --registry $(REGISTRY) build \
			--source "$(SOURCE)" \
			--feed "$${FEED:-tests/fixtures/feeds/$(SOURCE)/feed.ics}" \
			$(if $(ENRICH),--enrich,)

# The two sources whose SUMMARY means opposite things. Building both from one codebase
# with nothing differing but config is the proof the refactor exists to deliver.
build-fixtures: $(PY)
	@$(MAKE) --no-print-directory build SOURCE=orfe
	@$(MAKE) --no-print-directory build SOURCE=mae

# The whole tree: twelve live sources, five combined feeds, and a status.json saying what
# succeeded and how stale anything is. Offline by default -- it builds from the committed
# fixtures, so a developer gets the same code path over known bytes that CI runs over live
# ones. FETCH=1 pulls each source's ICS first, which is what CI does.
publish: $(PY)
	@BOT_BYPASS_HEADER="$${BOT_BYPASS_HEADER:-x-make-placeholder: not-a-credential}" \
		$(PY) -m upcoming.cli --registry $(REGISTRY) publish \
			--out "$${OUT:-dist}" \
			--feeds "$${FEEDS:-tests/fixtures/feeds}" \
			$(if $(FETCH),--fetch,) \
			$(if $(ENRICH),--enrich,)

# The landing page reads status.json over fetch(), which file:// will not allow, so
# previewing it needs a real origin. Same tree the deploy uploads.
serve: $(PY) publish
	@echo "serving dist/ at http://localhost:8000 -- ctrl-c to stop"
	@$(PY) -m http.server 8000 --directory "$${OUT:-dist}"

# The watchdog, run by hand. It takes no credential on purpose: it must see exactly what a
# consumer sees, and a credential would let it pass where a consumer fails.
verify: $(PY)
	$(PY) -m upcoming.cli verify --base-url "$${SITE:-https://pu-shd.github.io/upcoming}"

test: $(PY)
	$(PY) -m pytest

test-cov: $(PY)
	$(PY) -m pytest --tb=short -v

lint: $(PY)
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

typecheck: $(PY)
	$(PY) -m mypy upcoming

fmt: $(PY)
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

docker-build:
	$(COMPOSE) build

docker-test: docker-build
	$(COMPOSE) run --rm tests

docker-lint:
	$(COMPOSE) build lint
	$(COMPOSE) run --rm lint

docker-sources:
	$(COMPOSE) run --rm sources

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
