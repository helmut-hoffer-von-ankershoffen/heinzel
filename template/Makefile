.DEFAULT_GOAL := help
.PHONY: help install setup release-local doctor tools-verify check check-fast preflight fix lint format typecheck test test-parallel commits test-all-pythons test-unit test-integration test-perf test-notifier test-e2e audit licenses secrets build pypi-clean-build-marker changelog ci-logs pr review sync docs-plugins docs-record docs-site docs-publish-check release clean update-template preview

# Quiet by default: one line per passing step, the tail of a failing one. VERBOSE=1 streams everything.
# A step starting with & runs concurrently with the one before it; the first failure cancels the rest.
STEPS := @uv run --quiet --script scripts/steps.py
# Programs the build needs that aren't Python packages (gitleaks, gh, ...): pinned in scripts/tools.toml, installed
# into .tools/bin by `make install`, and found there first by every target.
TOOLS := uv run --quiet --script scripts/tools.py
export PATH := $(CURDIR)/.tools/bin:$(PATH)
# The repository pull requests go to: `upstream` in a fork's clone, otherwise `origin`.
REMOTE := $(shell git remote 2>/dev/null | grep -qx upstream && echo upstream || echo origin)
BASE ?= $(REMOTE)/main

# Cheapest checks first, so a typo fails in a second; the rest runs in parallel.
GATE := "lock=uv lock --check" "&format=uv run ruff format --check ." "&ruff=uv run ruff check --no-fix ."
LINT := \
		"&docs-plugins=uv run --quiet hz system plugin docs build --directory docs/plugins --check" \
		"&zizmor=uv run zizmor --offline --quiet .github/workflows" \
		"&actionlint=$(TOOLS) run actionlint" \
		"&typos=uv run typos" \
		"&confidential=uv run --script scripts/check_confidentiality.py"
TYPES := "&mypy=uv run mypy" "&pyright=uv run pyright"
# Unit, then integration (the suite orders them), on 4 workers, stopping at the first failure; coverage is in make test.
# Match the covered suite's deadline: the full suite can exceed two minutes on a busy developer machine.
TESTS := "&tests=uv run pytest -m '(unit or integration) and not perf' -n 4 -x -q --session-timeout=300"
# The same suite measured, on the same 4 workers: coverage's subprocess tracing can exceed the unmeasured 300s
# deadline on a busy developer machine. Match `make test`'s 420s allowance; coverage still measures every worker.
# ([tool.coverage.run] patch = ["subprocess"]) writes each worker to its own file; `coverage combine` merges them.
COVERED := uv run coverage erase && uv run coverage run -m pytest -m '(unit or integration) and not perf' -n 4 -q --session-timeout=420

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install what the build needs: Python with every dependency group (.venv) and the pinned tools (.tools)
	@test -f uv.lock || uv lock --quiet
	@uv sync --frozen --all-groups --quiet && echo "✓ .venv: Python and every dependency group, as locked in uv.lock"
	@$(TOOLS) install

setup: install ## Install what the build needs, and the git hooks (prek: lint on commit, conventional commits)
	@uv tool run --from prek==0.5.7 prek install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push >/dev/null
	@echo "✓ git hooks: pre-commit, commit-msg, pre-push"

doctor: ## What the build needs, what is missing, and the command that fixes it
	@$(TOOLS) doctor

tools-verify: ## Download every pinned tool for every platform and check its checksum and binary (needs network)
	@$(TOOLS) verify

check: ## Everything CI checks that runs offline, in parallel: lint, types, tests
	$(STEPS) $(GATE) $(LINT) $(TYPES) $(TESTS)

# The pre-push hook (scripts/scoped_checks.py push) narrows UNIT_TARGETS to the tests related to the branch and sets
# FAST_TYPES to the type checks; by hand both stay empty and this is the full unit run.
FAST_TYPES ?=
UNIT_TARGETS ?=
check-fast: ## Seconds-fast feedback for the edit loop: format, lint, generated pages, unit tests
	$(STEPS) \
		"format=uv run ruff format --check ." \
		"&ruff=uv run ruff check --no-fix ." \
		"&docs-plugins=uv run --quiet hz system plugin docs build --directory docs/plugins --check" \
		$(FAST_TYPES) \
		"unit=uv run pytest $(UNIT_TARGETS) -m 'unit and not perf' -n auto -x -q --session-timeout=120"

preflight: ## What CI checks, before you push: check with coverage, build, commits (PREFLIGHT=full adds e2e, audit)
	$(STEPS) $(GATE) $(LINT) $(TYPES) \
		"&tests=$(COVERED) -x && uv run coverage combine --quiet && uv run coverage report --format=total --fail-under=90" \
		"&build=uv build --quiet --out-dir .preflight-dist && rm -rf .preflight-dist" \
		"&commits=$(MAKE) --no-print-directory commits"
	@if [ "$(PREFLIGHT)" = full ]; then $(MAKE) --no-print-directory audit test-e2e; fi

fix: ## Fix what tools can fix: lockfile, formatting, lint autofixes, generated files; then list changed files
	$(STEPS) \
		"lock=uv lock --quiet" \
		"&docs-plugins=uv run --quiet hz system plugin docs build --directory docs/plugins" \
		"format=uv run ruff format --quiet ." \
		"ruff=uv run ruff check --fix --quiet --exit-zero ." \
		"format-final=uv run ruff format --quiet ."
	@git status --short

lint: ## Check formatting, lint and workflows
	$(STEPS) $(GATE) $(LINT)

format: ## Format and autofix
	uv run ruff format .
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Type-check with mypy and pyright (both strict)
	$(STEPS) "mypy=uv run mypy" "&pyright=uv run pyright"

test: ## Run unit and integration tests with coverage on 4 workers (offline; what the CI Coverage job runs)
	$(STEPS) \
		"tests=$(COVERED) --junitxml=reports/junit.xml && uv run coverage combine --quiet" \
		"coverage=uv run coverage report --skip-covered --skip-empty" \
		"coverage-xml=uv run coverage xml -q"
	@uv run --quiet coverage report --format=total | xargs printf '  coverage %s%%\n'

# Leave headroom under CI's 10-minute job timeout for the full unit/integration suite on slower runners.
# TEST_LEVELS picks a shard: CI's macOS cells set unit or integration, each under the same session cap.
TEST_LEVELS ?= unit or integration
# macOS runners have 3 vCPUs shared with browsers and subprocesses; fewer workers keep the fixed time budgets honest there.
PARALLEL_WORKERS := $(if $(filter Darwin,$(shell uname -s)),3,4)
test-parallel: ## Unit and integration tests on 4 workers (3 on macOS), stopping at the first failure; no coverage (the CI test matrix)
	@echo "Runner: $$(getconf _NPROCESSORS_ONLN) CPUs"
	$(STEPS) "tests=uv run pytest -m '($(TEST_LEVELS)) and not perf' -n $(PARALLEL_WORKERS) -x -q --session-timeout=420 --durations=20"

commits: ## Commit messages since BASE, and $$TITLE and $$BRANCH when set: conventional, no confidential terms
	$(STEPS) \
		'conventional=[ -z "$$(git rev-list $(BASE)..HEAD)" ] || uv run cz check --rev-range $(BASE)..HEAD' \
		"&confidential=uv run --script scripts/check_confidentiality.py --messages $(BASE)..HEAD" \
		'&title=[ -z "$$TITLE" ] || { uv run cz check --message "$$TITLE" && uv run --script scripts/check_confidentiality.py --text "$$TITLE"; }' \
		'&branch=[ -z "$$BRANCH" ] || uv run --script scripts/check_confidentiality.py --text "$$BRANCH"'

test-all-pythons: ## Run unit and integration tests on every supported Python, in isolated environments
	$(STEPS) $(foreach python,3.12 3.13 3.14 3.15,"python-$(python)=uv run --isolated --python $(python) pytest -m '(unit or integration) and not perf' -q -p no:cacheprovider")

test-unit: ## Run unit tests
	uv run pytest -m unit

test-integration: ## Run integration tests
	uv run pytest -m integration

test-perf: ## Run the performance tests (timing assertions such as linear time); CI only, they flake on a loaded machine
	$(STEPS) "perf=uv run pytest -m perf -q --session-timeout=120"

test-notifier: ## Real Heinzel Notifier launches leave no process behind (macOS; build it first: make notifier)
	$(STEPS) "notifier=uv run pytest tests/e2e/notifier_instances_test.py -q -p no:cacheprovider -o timeout=120"

test-e2e: ## Run end-to-end tests (needs network), on 3 workers
	@echo "Runner: $$(getconf _NPROCESSORS_ONLN) CPUs"
	$(STEPS) "e2e=uv run pytest -m e2e -n 3 -q --session-timeout=600 -x"

audit: ## Audit runtime dependencies: known vulnerabilities (pip-audit) and licenses (.license-allowlist)
	$(STEPS) "licenses=$(MAKE) --no-print-directory licenses" "&vulnerabilities=uv export --quiet --no-dev --no-emit-project --no-emit-package heinzel --format requirements-txt --output-file .audit-requirements.txt && uv run pip-audit --strict --disable-pip -r .audit-requirements.txt; status=\$$?; rm -f .audit-requirements.txt; exit \$$status"

secrets: ## Scan the whole git history for credentials with gitleaks (the pre-commit hook scans each commit)
	@$(TOOLS) run gitleaks git --redact --verbose --exit-code 1 .

licenses: ## Check that runtime dependencies' licenses are on .license-allowlist, so releases stay MIT-compatible
	bash scripts/check_licenses.sh

docs-plugins: ## Write the page of every installed plugin into docs/plugins with `hz system plugin docs build` (ADR 0082)
	uv run --quiet hz system plugin docs build --directory docs/plugins

docs-record: ## Record every plugin page's scenes (CLI with VHS, UI with Playwright) into docs/plugins/*/assets; PLUGIN=name, SCENE=name narrow it
	uv run --quiet --script scripts/docs_record.py $(if $(PLUGIN),--plugin $(PLUGIN)) $(if $(SCENE),--scene $(SCENE))

docs-site: ## Build the documentation site (MkDocs Material) into site/ from docs/; SERVE=1 serves it with reload instead
	uv run --quiet --script scripts/site_build.py $(if $(SERVE),serve,build --strict)

docs-publish-check: ## Validate publication with fresh public metadata, or build a standalone plugin's ordinary docs
	@if [ -f docs/catalog-sources.toml ]; then \
		if [ "$${REFRESH_PUBLIC:-false}" = true ]; then $(MAKE) docs-catalog || exit $$?; else unset HZ_SITE_CATALOG; fi; \
		$(MAKE) docs-site-check; \
	else $(MAKE) docs-site; fi

build: ## Build this plugin's wheel and sdist into dist/
	uv build

PYPI_DIST ?= dist
pypi-clean-build-marker: ## Remove uv's generated ignore marker from the PyPI staging directory
	@rm -f "$(PYPI_DIST)/.gitignore"

changelog: ## Preview the changelog for unreleased commits
	uv run git-cliff --unreleased

ci-logs: ## The failing steps of the latest CI run of this branch (waits for a running one); JSON=1 for machines
	@uv run --quiet --script scripts/ci_logs.py $(if $(JSON),--json)

pr: ## Push this branch and open a pull request for review: make pr TITLE="feat: ..." (DRAFT=1: draft; BODY=... or BODY_FILE=path: a new PR's description)
	@bash scripts/pr.sh "$(TITLE)"

sync: ## Rebase this branch onto the latest main (upstream's, in a fork)
	@git fetch --quiet $(REMOTE) && git rebase --quiet $(REMOTE)/main && git log --oneline $(REMOTE)/main..HEAD

# Keep release prose literal: Make must not interpret dollar expressions in a command-line INTRO.
RELEASE_INTRO := $(value INTRO)
unexport INTRO
export RELEASE_INTRO

release: ## Open a release pull request on GitHub (workflow "Prepare release"): BUMP=auto|patch|minor|major (default auto), INTRO="prose" (required)
	@if ! printf '%s' "$$RELEASE_INTRO" | grep -q '[^[:space:]]'; then echo 'error: INTRO is required: summarize what changed for users.' >&2; exit 1; fi
	@since="$$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
		gh workflow run prepare-release.yml -f bump=$(or $(BUMP),auto) -f intro="$$RELEASE_INTRO" && \
		for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 2; \
			url="$$(gh run list --workflow prepare-release.yml --event workflow_dispatch --limit 5 --json url,createdAt --jq "map(select(.createdAt >= \"$$since\")) | .[0].url // empty")"; \
			[ -z "$$url" ] || { echo "$$url"; exit 0; }; done; echo "dispatched; see the Actions tab"

release-local: ## Open a release pull request from this machine, from an up-to-date main: BUMP=auto|patch|minor|major, INTRO="prose" (required)
	@if ! printf '%s' "$$RELEASE_INTRO" | grep -q '[^[:space:]]'; then echo 'error: INTRO is required: summarize what changed for users.' >&2; exit 1; fi
	@echo "Local path: no CI is dispatched and nothing auto-merges; merge the pull request after its checks (dispatch ci.yml on the branch)."
	INTRO="$$RELEASE_INTRO" bash scripts/release.sh $(or $(BUMP),auto) --open-pr

clean: ## Remove build and test artifacts
	rm -rf dist build .coverage* coverage.xml htmlcov reports .pytest_cache .mypy_cache .ruff_cache site

.PHONY: docs

docs: docs-plugins ## Regenerate this plugin's documentation

preview: ## Launch this plugin using isolated application state
	uv run --no-project --script scripts/plugin_preview.py

update-template: ## Update this plugin to the latest Heinzelmännchen template (needs a clean working tree)
	uvx copier update --defaults
