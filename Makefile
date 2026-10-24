.DEFAULT_GOAL := help
.PHONY: help install setup check check-fast preflight fix lint format typecheck test test-parallel commits test-all-pythons test-unit test-integration test-e2e audit licenses template-test template-sync docs fuzz demo build changelog ci-logs pr sync release clean

# Quiet by default: one line per passing step, the tail of a failing one. VERBOSE=1 streams everything.
# A step starting with & runs concurrently with the one before it; the first failure cancels the rest.
STEPS := @uv run --quiet --script scripts/steps.py
BASE ?= origin/main

# Cheapest checks first, so a typo fails in a second; the rest runs in parallel.
GATE := "lock=uv lock --check" "&format=uv run ruff format --check ." "&ruff=uv run ruff check --no-fix ."
LINT := \
		"contracts=uv run lint-imports" \
		"&template=uv run --script scripts/sync_template.py --check" \
		"&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py --check" \
		"&demo=uv run --script scripts/demo_tape.py --check" \
		"&zizmor=uv run zizmor --offline --quiet .github/workflows" \
		"&actionlint=uv run actionlint" \
		"&typos=uv run typos" \
		"&confidential=uv run --script scripts/check_confidentiality.py"
TYPES := "&mypy=uv run mypy" "&pyright=uv run pyright"
# Unit, then integration (the suite orders them), on 4 workers, stopping at the first failure; coverage is in make test.
TESTS := "&tests=uv run pytest -m 'unit or integration' -n 4 -x -q --session-timeout=120"

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Create the virtualenv with all dependencies
	uv sync --all-groups

setup: install ## Install dependencies and git hooks (prek: lint on commit, conventional commit messages)
	uv run prek install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push

check: ## Everything CI checks that runs offline, in parallel: lint, import contracts, types, tests
	$(STEPS) $(GATE) $(LINT) $(TYPES) $(TESTS)

check-fast: ## Seconds-fast feedback for the edit loop: format, lint, contracts, generated files, unit tests
	$(STEPS) \
		"format=uv run ruff format --check ." \
		"&ruff=uv run ruff check --no-fix ." \
		"&contracts=uv run lint-imports" \
		"&template=uv run --script scripts/sync_template.py --check" \
		"&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py --check" \
		"&demo=uv run --script scripts/demo_tape.py --check" \
		"unit=uv run pytest -m unit -n auto -x -q --session-timeout=60"

preflight: ## What CI checks, before you push: check with coverage, template, build, commits (PREFLIGHT=full adds e2e, audit)
	$(STEPS) $(GATE) $(LINT) $(TYPES) \
		"&tests=uv run coverage run -m pytest -m 'unit or integration' -x -q --session-timeout=300 && uv run coverage report --format=total --fail-under=90" \
		"&template=TEMPLATE_AUDIT=$(if $(filter full,$(PREFLIGHT)),1) bash scripts/template-test.sh" \
		"&build=uv build --all-packages --quiet --out-dir .preflight-dist && rm -rf .preflight-dist" \
		"&commits=$(MAKE) --no-print-directory commits"
	@if [ "$(PREFLIGHT)" = full ]; then $(MAKE) --no-print-directory audit test-e2e; fi

fix: ## Fix what tools can fix: lockfile, formatting, lint autofixes, generated files; then list changed files
	$(STEPS) \
		"lock=uv lock --quiet" \
		"template=uv run --script scripts/sync_template.py" \
		"&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py" \
		"&demo=uv run --script scripts/demo_tape.py" \
		"format=uv run ruff format --quiet ." \
		"ruff=uv run ruff check --fix --quiet --exit-zero ."
	@git status --short

lint: ## Check formatting, lint, architecture contracts and workflows
	$(STEPS) $(GATE) $(LINT)

format: ## Format and autofix
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Type-check with mypy and pyright (both strict)
	$(STEPS) "mypy=uv run mypy" "&pyright=uv run pyright"

test: ## Run unit and integration tests with coverage (offline)
	$(STEPS) \
		"tests=uv run coverage run -m pytest -m 'unit or integration' -q --session-timeout=300 --junitxml=reports/junit.xml" \
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
	$(STEPS) "licenses=$(MAKE) --no-print-directory licenses" "&vulnerabilities=uv export --quiet --no-dev --no-emit-project --no-emit-package heinzel --format requirements-txt --output-file .audit-requirements.txt && uv run pip-audit --strict --disable-pip -r .audit-requirements.txt; status=\$$?; rm -f .audit-requirements.txt; exit \$$status"

licenses: ## Check that runtime dependencies' licenses are on .license-allowlist, so releases stay MIT-compatible
	bash scripts/check_licenses.sh

template-test: ## Render the plugin template and run its checks against this checkout (KEEP=dir keeps it; AUDIT=1 adds make audit, which needs network)
	TEMPLATE_AUDIT=$(AUDIT) bash scripts/template-test.sh $(KEEP)

docs: ## Regenerate docs/CLI_REFERENCE.md from the code
	uv run python scripts/generate_docs.py

fuzz: ## Fuzz the workflow contract parser with atheris for FUZZ_SECONDS (default 60; Linux, Python 3.12 to 3.14)
	uv run --group fuzz python fuzz/contract_fuzz.py -max_total_time=$(or $(FUZZ_SECONDS),60) -timeout=10

demo: ## Assemble docs/assets/demo.tape from the intro and the modules' demo.tape snippets and render the GIF with VHS
	bash scripts/demo.sh

template-sync: ## Regenerate the template's Makefile, CI workflow and pyproject from Heinzelmännchen's
	uv run --script scripts/sync_template.py

build: ## Build wheels and sdists into dist/ (the core and every first-party plugin)
	uv build --all-packages

changelog: ## Preview the changelog for unreleased commits
	uv run git-cliff --unreleased

ci-logs: ## The failing steps of the latest CI run of this branch (waits for a running one); JSON=1 for machines
	@uv run --quiet --script scripts/ci_logs.py $(if $(JSON),--json)

pr: ## Push this branch and open a squash-merged, auto-merging pull request to main: make pr TITLE="feat: ..." (DRAFT=1: draft, no auto-merge)
	@bash scripts/pr.sh "$(TITLE)"

sync: ## Rebase this branch onto the latest main
	@git fetch --quiet origin && git rebase --quiet origin/main && git log --oneline origin/main..HEAD

release: ## Open a release pull request from main: BUMP=auto|patch|minor|major (default auto), INTRO="prose"
	bash scripts/release.sh $(or $(BUMP),auto) --open-pr

clean: ## Remove build and test artifacts
	rm -rf dist build .coverage* coverage.xml htmlcov reports .pytest_cache .mypy_cache .ruff_cache
