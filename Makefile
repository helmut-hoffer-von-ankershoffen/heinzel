.DEFAULT_GOAL := help
.PHONY: help install setup release-local doctor tools-verify check check-fast preflight fix lint format typecheck test test-parallel commits test-all-pythons test-unit test-integration test-e2e audit licenses secrets template-test template-sync docs attributions fuzz demo notifier icons web-build web-dev test-web build changelog ci-logs pr sync release clean

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
		"contracts=uv run lint-imports" \
		"&template=uv run --script scripts/sync_template.py --check" \
		"&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py --check" \
		"&demo=uv run --script scripts/demo_tape.py --check" \
		"&attributions=uv run --quiet --script scripts/attributions.py --check" \
		"&zizmor=uv run zizmor --offline --quiet .github/workflows" \
		"&actionlint=uv run actionlint" \
		"&typos=uv run typos" \
		"&confidential=uv run --script scripts/check_confidentiality.py"
TYPES := "&mypy=uv run mypy" "&pyright=uv run pyright"
# Unit, then integration (the suite orders them), on 4 workers, stopping at the first failure; coverage is in make test.
TESTS := "&tests=uv run pytest -m 'unit or integration' -n 4 -x -q --session-timeout=120"
# The same suite measured, on the same 4 workers: each xdist worker is a Python subprocess, which coverage measures
# ([tool.coverage.run] patch = ["subprocess"]) into its own data file; `coverage combine` merges them afterwards.
COVERED := uv run coverage erase && uv run coverage run -m pytest -m 'unit or integration' -n 4 -q --session-timeout=300
# The web head's app in web/ (ADR 0039): pnpm from scripts/tools.toml on the Node.js of uv.lock, through scripts/web_build.py,
# which installs web/'s dependencies first when they are stale. Plugins have no web/; the template leaves these out.
WEB := uv run --quiet --script scripts/web_build.py
WEB_PROJECT := uv run --quiet python scripts/web.py
# The client is generated from built-in modules and default settings, like the docs, whatever HZ_* are set.
WEB_PROJECT_CLEAN := env -u HZ_SYSTEM_AGENT__SKILL_DIRS $(WEB_PROJECT)
WEB_LINT := "&web-lint=$(WEB) exec biome ci ." "&web-client=$(WEB_PROJECT_CLEAN) client --check"
WEB_TYPES := "&web-types=$(WEB) exec tsc --noEmit -p ."
WEB_TESTS := "&web-tests=$(WEB) exec vitest run"

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install what the build needs: Python with every dependency group (.venv) and the pinned tools (.tools)
	@uv sync --all-groups --quiet && echo "✓ .venv: Python and every dependency group, as locked in uv.lock"
	@$(TOOLS) install build web
	@$(WEB) install

setup: install ## Install what the build needs, and the git hooks (prek: lint on commit, conventional commits)
	@uv run --quiet prek install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push >/dev/null
	@echo "✓ git hooks: pre-commit, commit-msg, pre-push"

doctor: ## What the build needs, what is missing, and the command that fixes it
	@$(TOOLS) doctor

tools-verify: ## Download every pinned tool for every platform and check its checksum and binary (needs network)
	@$(TOOLS) verify

check: ## Everything CI checks that runs offline, in parallel: lint, import contracts, types, tests
	$(STEPS) $(GATE) $(LINT) $(WEB_LINT) $(TYPES) $(WEB_TYPES) $(TESTS) $(WEB_TESTS)

check-fast: ## Seconds-fast feedback for the edit loop: format, lint, contracts, generated files, unit tests
	$(STEPS) \
		"format=uv run ruff format --check ." \
		"&ruff=uv run ruff check --no-fix ." \
		"&contracts=uv run lint-imports" \
		"&template=uv run --script scripts/sync_template.py --check" \
		"&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py --check" \
		"&demo=uv run --script scripts/demo_tape.py --check" \
		"&attributions=uv run --quiet --script scripts/attributions.py --check" \
		$(WEB_LINT) $(WEB_TYPES) \
		"unit=uv run pytest -m unit -n auto -x -q --session-timeout=60" $(WEB_TESTS)

preflight: ## What CI checks, before you push: check with coverage, template, build, commits (PREFLIGHT=full adds e2e, audit)
	$(STEPS) $(GATE) $(LINT) $(WEB_LINT) $(TYPES) $(WEB_TYPES) $(WEB_TESTS) \
		"&tests=$(COVERED) -x && uv run coverage combine --quiet && uv run coverage report --format=total --fail-under=90" \
		"&template=TEMPLATE_AUDIT=$(if $(filter full,$(PREFLIGHT)),1) bash scripts/template-test.sh" \
		"&build=uv build --all-packages --quiet --out-dir .preflight-dist && rm -rf .preflight-dist" \
		"&commits=$(MAKE) --no-print-directory commits"
	@if [ "$(PREFLIGHT)" = full ]; then $(MAKE) --no-print-directory audit test-e2e; fi

fix: ## Fix what tools can fix: lockfile, formatting, lint autofixes, generated files; then list changed files
	$(STEPS) \
		"lock=uv lock --quiet" \
		"template=uv run --script scripts/sync_template.py" \
		"&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py" \
		"&web-client=$(WEB_PROJECT_CLEAN) client" \
		"&demo=uv run --script scripts/demo_tape.py" \
		"&attributions=uv run --quiet --script scripts/attributions.py" \
		"format=uv run ruff format --quiet ." \
		"ruff=uv run ruff check --fix --quiet --exit-zero ."
	@git status --short

lint: ## Check formatting, lint, architecture contracts and workflows
	$(STEPS) $(GATE) $(LINT) $(WEB_LINT)

format: ## Format and autofix
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Type-check with mypy and pyright (both strict)
	$(STEPS) "mypy=uv run mypy" "&pyright=uv run pyright" $(WEB_TYPES)

test: ## Run unit and integration tests with coverage on 4 workers (offline; what the CI Coverage job runs)
	$(STEPS) \
		"tests=$(COVERED) --junitxml=reports/junit.xml && uv run coverage combine --quiet" \
		"coverage=uv run coverage report --skip-covered --skip-empty" \
		"coverage-xml=uv run coverage xml -q"
	@uv run --quiet coverage report --format=total | xargs printf '  coverage %s%%\n'

test-parallel: ## Unit and integration tests on 4 workers, stopping at the first failure; no coverage (the CI test matrix)
	$(STEPS) "tests=uv run pytest -m 'unit or integration' -n 4 -x -q --session-timeout=300"

commits: ## Commit messages since BASE, and $$TITLE and $$BRANCH when set: conventional, no confidential terms
	$(STEPS) \
		'conventional=[ -z "$$(git rev-list $(BASE)..HEAD)" ] || uv run cz check --rev-range $(BASE)..HEAD' \
		"&confidential=uv run --script scripts/check_confidentiality.py --messages $(BASE)..HEAD" \
		'&title=[ -z "$$TITLE" ] || { uv run cz check --message "$$TITLE" && uv run --script scripts/check_confidentiality.py --text "$$TITLE"; }' \
		'&branch=[ -z "$$BRANCH" ] || uv run --script scripts/check_confidentiality.py --text "$$BRANCH"'

test-all-pythons: ## Run unit and integration tests on every supported Python, in isolated environments
	$(STEPS) $(foreach python,3.12 3.13 3.14 3.15,"python-$(python)=uv run --isolated --python $(python) pytest -m 'unit or integration' -q -p no:cacheprovider")

test-unit: ## Run unit tests
	uv run pytest -m unit

test-integration: ## Run integration tests
	uv run pytest -m integration

test-e2e: ## Run end-to-end tests (needs network), on 3 workers
	$(STEPS) "e2e=uv run pytest -m e2e -n 3 -q --session-timeout=600"

audit: ## Audit runtime dependencies: known vulnerabilities (pip-audit) and licenses (.license-allowlist)
	$(STEPS) "licenses=$(MAKE) --no-print-directory licenses" "&vulnerabilities=uv export --quiet --no-dev --no-emit-project --no-emit-package heinzel --format requirements-txt --output-file .audit-requirements.txt && uv run pip-audit --strict --disable-pip -r .audit-requirements.txt; status=\$$?; rm -f .audit-requirements.txt; exit \$$status" "&web-vulnerabilities=$(WEB) pnpm audit --prod"

secrets: ## Scan the whole git history for credentials with gitleaks (the pre-commit hook scans each commit)
	@$(TOOLS) run gitleaks git --redact --verbose --exit-code 1 .

licenses: ## Check that runtime dependencies' licenses are on .license-allowlist, so releases stay MIT-compatible
	bash scripts/check_licenses.sh
	@$(WEB) licenses

template-test: ## Render the plugin template, run its checks and audit against this checkout (KEEP=dir keeps it; AUDIT=0 skips the audit, which needs network)
	TEMPLATE_AUDIT=$(if $(filter 0,$(AUDIT)),,1) bash scripts/template-test.sh $(KEEP)

docs: ## Regenerate docs/CLI_REFERENCE.md from the code
	uv run python scripts/generate_docs.py

attributions: ## Regenerate ATTRIBUTIONS.md: every package hz ships (Python and web app), with its license text
	uv run --quiet --script scripts/attributions.py

fuzz: ## Fuzz the workflow contract parser with atheris for FUZZ_SECONDS (default 60; x86_64 Linux, Python 3.12 to 3.14)
	uv run --group fuzz python fuzz/contract_fuzz.py -max_total_time=$(or $(FUZZ_SECONDS),60) -timeout=10

demo: ## Assemble docs/assets/demo.tape from the intro and the modules' demo.tape snippets and render the GIF with VHS
	bash scripts/demo.sh

template-sync: ## Regenerate the template's Makefile, CI workflow and pyproject from Heinzelmännchen's
	uv run --script scripts/sync_template.py

web-build: ## Build the web head's app into src/heinzel/heads/web/static, which hz ui serves and the wheel ships
	@$(WEB) build

web-dev: ## The web head with hot reload: its API on a free port, Vite in front of it at the URL printed
	@$(WEB_PROJECT) dev

test-web: ## Run the web app's component tests (Vitest, with axe for accessibility)
	$(STEPS) "web-tests=$(WEB) exec vitest run"

notifier: ## Build Heinzel Notifier, the macOS notifier app the wheel ships (macOS 14+ with the Command Line Tools)
	@uv run --quiet --script scripts/notifier.py

icons: ## Make every image of Heinzelmännchen from heinzel.svg: the web app's icons, the favicon, the theme's logo (macOS 14+)
	@uv run --quiet --script scripts/notifier.py icons

build: web-build ## Build wheels and sdists into dist/ (the core and every first-party plugin; the web app first)
	uv build --all-packages

changelog: ## Preview the changelog for unreleased commits
	uv run git-cliff --unreleased

ci-logs: ## The failing steps of the latest CI run of this branch (waits for a running one); JSON=1 for machines
	@uv run --quiet --script scripts/ci_logs.py $(if $(JSON),--json)

pr: ## Push this branch and open a squash-merged, auto-merging pull request to main: make pr TITLE="feat: ..." (DRAFT=1: draft, no auto-merge; BODY=... or BODY_FILE=path: a new PR's description)
	@bash scripts/pr.sh "$(TITLE)"

sync: ## Rebase this branch onto the latest main (upstream's, in a fork)
	@git fetch --quiet $(REMOTE) && git rebase --quiet $(REMOTE)/main && git log --oneline $(REMOTE)/main..HEAD

release: ## Open a release pull request on GitHub (workflow "Prepare release"): BUMP=auto|patch|minor|major (default auto), INTRO="prose"
	@since="$$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
		gh workflow run prepare-release.yml -f bump=$(or $(BUMP),auto) -f intro="$$INTRO" && \
		for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 2; \
			url="$$(gh run list --workflow prepare-release.yml --event workflow_dispatch --limit 5 --json url,createdAt --jq "map(select(.createdAt >= \"$$since\")) | .[0].url // empty")"; \
			[ -z "$$url" ] || { echo "$$url"; exit 0; }; done; echo "dispatched; see the Actions tab"

release-local: ## Open a release pull request from this machine, from an up-to-date main: BUMP=auto|patch|minor|major, INTRO="prose"
	@echo "Local path: no CI is dispatched and nothing auto-merges; merge the pull request after its checks (dispatch ci.yml on the branch)."
	bash scripts/release.sh $(or $(BUMP),auto) --open-pr

clean: ## Remove build and test artifacts
	rm -rf dist build .coverage* coverage.xml htmlcov reports .pytest_cache .mypy_cache .ruff_cache src/heinzel/heads/web/static
