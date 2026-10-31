.DEFAULT_GOAL := help
.PHONY: help install setup release-local doctor tools-verify check check-fast linux-user-manager-proof preflight fix lint format typecheck test test-parallel commits test-all-pythons test-unit test-integration test-perf test-e2e e2e-prepare test-browser-deps audit licenses secrets template-test template-sync docs attributions fuzz demo terminal-art notifier icons web-build web-dev test-web managed-backend-setup managed-backend-check desktop-electron-test desktop-electron-sandbox desktop-electron-native desktop-electron-package build pypi-clean-build-marker changelog ci-logs pr sync docs-plugins docs-record docs-site docs-catalog docs-site-check docs-publish-check release clean

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
# Match the covered suite's deadline: the full suite can exceed two minutes on a busy developer machine.
TESTS := "&tests=uv run pytest -m '(unit or integration) and not perf' -n 4 -x -q --session-timeout=300"
# The same suite measured, on the same 4 workers: coverage's subprocess tracing can exceed the unmeasured 300s
# deadline on a busy developer machine. Match `make test`'s 420s allowance; coverage still measures every worker.
# ([tool.coverage.run] patch = ["subprocess"]) writes each worker to its own file; `coverage combine` merges them.
COVERED := uv run coverage erase && uv run coverage run -m pytest -m '(unit or integration) and not perf' -n 4 -q --session-timeout=420
# The web head's app in web/ (ADR 0039): pnpm from scripts/tools.toml on the Node.js of uv.lock, through scripts/web_build.py,
# which installs web/'s dependencies first when they are stale. Plugins have no web/; the template leaves these out.
WEB := uv run --quiet --script scripts/web_build.py
WEB_PROJECT := uv run --quiet python scripts/web.py
# The client is generated from built-in modules and default settings, like the docs, whatever HZ_* are set.
WEB_PROJECT_CLEAN := env -u HZ_SYSTEM_AGENT__SKILL_DIRS $(WEB_PROJECT)
WEB_LINT := "&web-lint=$(WEB) exec biome ci ." "&web-client=$(WEB_PROJECT_CLEAN) client --check"
# Keep the native TypeScript CLI distinct from the compiler API openapi-typescript imports.
WEB_TYPES := "&web-types=$(WEB) exec node node_modules/@typescript/native/bin/tsc --noEmit -p ."
WEB_TESTS := "&web-tests=$(WEB) exec vitest run"
# The managed backend has no Electron dependency; plugins do not carry its private verification package.
MANAGED := $(WEB) pnpm --dir ../desktop/managed
MANAGED_CHECK := "managed-backend=$(MANAGED) run check"
MANAGED_TYPES := "&managed-types=$(MANAGED) exec tsc --noEmit --project ."

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install what the build needs: Python with every dependency group (.venv) and the pinned tools (.tools)
	@uv sync --all-groups --quiet && echo "✓ .venv: Python and every dependency group, as locked in uv.lock"
	@$(TOOLS) install build web
	@$(WEB) install
	@$(MAKE) --no-print-directory managed-backend-setup

setup: install ## Install what the build needs, and the git hooks (prek: lint on commit, conventional commits)
	@uv run --quiet prek install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push >/dev/null
	@echo "✓ git hooks: pre-commit, commit-msg, pre-push"

doctor: ## What the build needs, what is missing, and the command that fixes it
	@$(TOOLS) doctor

tools-verify: ## Download every pinned tool for every platform and check its checksum and binary (needs network)
	@$(TOOLS) verify

check: ## Everything CI checks that runs offline, in parallel: lint, import contracts, types, tests
	@$(WEB) prepare
	$(STEPS) $(GATE) $(LINT) $(WEB_LINT) $(TYPES) $(WEB_TYPES) $(TESTS) $(WEB_TESTS) $(MANAGED_CHECK)

linux-user-manager-proof: ## Inspect an existing Linux user manager without starting services; set PROBE_PREFIX
	uv run python tests/integration/linux_user_manager_probe.py --prefix "$${PROBE_PREFIX:?Set PROBE_PREFIX to an isolated heinzel-probe- name}"

# Unit tests take every core and have a 5 s budget each; Vitest beside them starved them into timeouts, so it runs after.
check-fast: ## Seconds-fast feedback for the edit loop: format, lint, contracts, generated files, unit tests
	@$(WEB) prepare
	$(STEPS) \
		"format=uv run ruff format --check ." \
		"&ruff=uv run ruff check --no-fix ." \
		"&contracts=uv run lint-imports" \
		"&template=uv run --script scripts/sync_template.py --check" \
		"&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py --check" \
		"&demo=uv run --script scripts/demo_tape.py --check" \
		"&attributions=uv run --quiet --script scripts/attributions.py --check" \
		$(WEB_LINT) $(WEB_TYPES) \
		"unit=uv run pytest -m 'unit and not perf' -n auto -x -q --session-timeout=120" $(subst &web-tests,web-tests,$(WEB_TESTS)) $(MANAGED_CHECK)

preflight: ## What CI checks, before you push: check with coverage, template, build, commits (PREFLIGHT=full adds e2e, audit)
	@$(WEB) prepare
	$(STEPS) $(GATE) $(LINT) $(WEB_LINT) $(TYPES) $(WEB_TYPES) $(WEB_TESTS) \
		"&tests=$(COVERED) -x && uv run coverage combine --quiet && uv run coverage report --format=total --fail-under=90" \
		"&template=TEMPLATE_AUDIT=$(if $(filter full,$(PREFLIGHT)),1) bash scripts/template-test.sh" \
		"&build=uv build --quiet --out-dir .preflight-dist && rm -rf .preflight-dist" \
		"&commits=$(MAKE) --no-print-directory commits" $(MANAGED_CHECK)
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
		"ruff=uv run ruff check --fix --quiet --exit-zero ." \
		"format-final=uv run ruff format --quiet ."
	@git status --short

lint: ## Check formatting, lint, architecture contracts and workflows
	@$(WEB) prepare
	$(STEPS) $(GATE) $(LINT) $(WEB_LINT)

format: ## Format and autofix
	uv run ruff format .
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Type-check with mypy and pyright (both strict)
	$(STEPS) "mypy=uv run mypy" "&pyright=uv run pyright" $(WEB_TYPES) $(MANAGED_TYPES)

managed-backend-setup: ## Install locked private backend verification tools; no application or OS service
	@$(MANAGED) install --frozen-lockfile --ignore-scripts --prefer-offline --reporter=silent
	@echo "✓ managed backend: locked verification dependencies"

managed-backend-check: ## Check sealed helpers, strict Node types and owned process/inventory behavior
	$(STEPS) $(MANAGED_CHECK)

desktop-electron-test: ## Install the locked Electron runtime and run its unit suite
	pnpm --dir desktop install --frozen-lockfile
	pnpm --dir desktop exec node -p 'require("electron")'
	pnpm --dir desktop test

desktop-electron-package: ## Build and verify an unsigned Electron artifact for this CI host
	pnpm --dir desktop install --node-linker=hoisted --frozen-lockfile
	pnpm --dir desktop build
	pnpm --dir desktop verify:package

desktop-electron-sandbox: ## Set Electron's Linux sandbox helper ownership and mode for CI smoke tests
	packaged_sandbox="$$PWD/desktop/dist/Heinzelmaennchen-linux-$$(node -p 'process.arch')/chrome-sandbox"; \
	if [ ! -f "$$packaged_sandbox" ] || [ -L "$$packaged_sandbox" ]; then \
		echo "::error::Expected packaged Electron sandbox helper not found: $$packaged_sandbox"; \
		exit 1; \
	fi; \
	if ! sudo python3 desktop/ci/restore_linux_sandbox.py --configure-file "$$packaged_sandbox" "$$(pnpm --dir desktop exec node -p 'require("electron/package.json").version')"; then \
		echo "::error::Could not configure packaged Electron sandbox helper at $$packaged_sandbox"; \
		exit 1; \
	fi; \
	if [ "$$(stat -c '%u:%a' "$$packaged_sandbox")" != "0:4755" ]; then \
		echo "::error::Packaged Electron sandbox helper must be root-owned with mode 4755: $$packaged_sandbox"; \
		exit 1; \
	fi

desktop-electron-native: ## Launch the Electron window against a temporary authenticated local service
	pnpm --dir web install --frozen-lockfile
	@case "$$(uname -s)" in \
		Linux) target="linux-$$(node -p 'process.arch')"; executable="$$PWD/desktop/dist/Heinzelmaennchen-$$target/Heinzelmaennchen"; runner='xvfb-run --auto-servernum' ;; \
		Darwin) target="darwin-$$(node -p 'process.arch')"; executable="$$PWD/desktop/dist/Heinzelmaennchen-$$target/Heinzelmaennchen.app/Contents/MacOS/Heinzelmaennchen"; runner='' ;; \
		*) echo "Unsupported Electron native smoke-test host: $$(uname -s)"; exit 2 ;; \
	esac; \
	if [ ! -x "$$executable" ]; then echo "Packaged Electron executable is missing or not executable: $$executable"; exit 1; fi; \
	HEINZEL_ELECTRON_EXECUTABLE="$$executable" $$runner pnpm --dir desktop test:native
	# Packaging can replace pnpm's development-runtime directory; provision it again before development navigation tests.
	pnpm --dir desktop exec node node_modules/electron/install.js
	@if [ "$$(uname -s)" = Linux ]; then \
		if [ "$$CI" != true ] || [ "$$HZ_DISPOSABLE_NATIVE_CI" != 1 ]; then echo "Development sandbox setup requires disposable native CI."; exit 1; fi; \
		sudo python3 desktop/ci/restore_linux_sandbox.py --configure "$$(realpath desktop/node_modules/electron)"; \
	fi
	@case "$$(uname -s)" in Linux) xvfb-run --auto-servernum pnpm --dir desktop test:navigation ;; Darwin) pnpm --dir desktop test:navigation ;; esac
	@case "$$(uname -s)" in Linux) xvfb-run --auto-servernum pnpm --dir desktop test:permissions ;; Darwin) pnpm --dir desktop test:permissions ;; esac

test: ## Run unit and integration tests with coverage on 4 workers (offline; what the CI Coverage job runs)
	$(STEPS) \
		"tests=$(COVERED) --junitxml=reports/junit.xml && uv run coverage combine --quiet" \
		"coverage=uv run coverage report --skip-covered --skip-empty" \
		"coverage-xml=uv run coverage xml -q"
	@uv run --quiet coverage report --format=total | xargs printf '  coverage %s%%\n'

# Leave headroom under CI's 10-minute job timeout for the full unit/integration suite on slower runners.
# TEST_LEVELS picks a shard: CI's macOS cells set unit or integration, each under the same session cap.
TEST_LEVELS ?= unit or integration
test-parallel: ## Unit and integration tests on 4 workers, stopping at the first failure; no coverage (the CI test matrix)
	$(STEPS) "tests=uv run pytest -m '($(TEST_LEVELS)) and not perf' -n 4 -x -q --session-timeout=420 --durations=20"

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

test-browser-deps: ## Install Chromium and WebKit; include Linux host libraries when needed
	@if [ "$$(uname -s)" = Linux ]; then \
		$(WEB) exec playwright-core install --with-deps chromium webkit; \
	else \
		$(WEB) exec playwright-core install chromium webkit; \
	fi

test-e2e: e2e-prepare ## Run end-to-end tests (needs network), on 3 workers
	$(STEPS) "e2e-readme=uv run pytest -m e2e -n 3 -q --session-timeout=600 -x tests/e2e/docs_test.py::test_readme_instructions" "e2e=uv run pytest -m e2e -n 3 -q --session-timeout=600 -x --deselect=tests/e2e/docs_test.py::test_readme_instructions"

e2e-prepare: ## Prepare current build assets before end-to-end procedure deadlines
	@set -eu; dir="$$(mktemp -d "$${TMPDIR:-/tmp}/heinzel-e2e.XXXXXXXX")"; trap 'rm -rf "$$dir"' EXIT; uv build --quiet --wheel --out-dir "$$dir"

audit: ## Audit runtime dependencies: known vulnerabilities (pip-audit) and licenses (.license-allowlist)
	$(STEPS) "licenses=$(MAKE) --no-print-directory licenses" "&vulnerabilities=uv export --quiet --no-dev --no-emit-project --no-emit-package heinzel --format requirements-txt --output-file .audit-requirements.txt && uv run pip-audit --strict --disable-pip -r .audit-requirements.txt; status=\$$?; rm -f .audit-requirements.txt; exit \$$status" "&web-vulnerabilities=$(WEB) pnpm audit --prod"

secrets: ## Scan the whole git history for credentials with gitleaks (the pre-commit hook scans each commit)
	@$(TOOLS) run gitleaks git --redact --verbose --exit-code 1 .

licenses: ## Check that runtime dependencies' licenses are on .license-allowlist, so releases stay MIT-compatible
	bash scripts/check_licenses.sh
	@$(WEB) licenses

template-test: ## Render the plugin template, run its checks and audit against this checkout (KEEP=dir keeps it; AUDIT=0 skips the audit, which needs network)
	TEMPLATE_AUDIT=$(if $(filter 0,$(AUDIT)),,1) bash scripts/template-test.sh $(KEEP)

docs: ## Regenerate the references (MODULES, CLI_REFERENCE, CONFIGURATION) and every plugin's page from the code
	uv run python scripts/generate_docs.py

attributions: ## Regenerate ATTRIBUTIONS.md: every package hz ships (Python and web app), with its license text
	uv run --quiet --script scripts/attributions.py

fuzz: ## Fuzz the workflow contract parser with atheris for FUZZ_SECONDS (default 60; x86_64 Linux, Python 3.12 to 3.14)
	uv run --group fuzz python fuzz/contract_fuzz.py -max_total_time=$(or $(FUZZ_SECONDS),60) -timeout=10

demo: ## Render the README's intro, docs/assets/demo.{gif,webp,mp4}: the demo tape's CLI part, then the UI scenes of docs/scenes.toml
	uv run --quiet --script scripts/demo_tape.py
	uv run --quiet --script scripts/docs_record.py --intro-only

docs-plugins: ## Write the page of every installed plugin into docs/plugins (ADR 0082); here part of `make docs`, which also writes the gallery
	env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py

docs-record: ## Record every plugin page's scenes (CLI with VHS, UI with Playwright) into docs/plugins/*/assets, and the README's intro; PLUGIN=name, SCENE=name narrow it
	uv run --quiet --script scripts/docs_record.py --intro $(if $(PLUGIN),--plugin $(PLUGIN)) $(if $(SCENE),--scene $(SCENE))

docs-site: ## Build the documentation site (MkDocs Material) into site/ from docs/; SERVE=1 serves it with reload instead
	uv run --quiet --script scripts/site_build.py $(if $(SERVE),serve,build --strict)

docs-catalog: ## Refresh the public metadata-only website catalog; offline site builds use its checked-in snapshot
	uv run --quiet --script scripts/site_catalog.py --output "$${HZ_SITE_CATALOG:-docs/_data/catalog.json}"

docs-site-check: docs-site ## Check the built site's catalog, keyboard flow, mobile layout and accessibility
	$(WEB) install
	uv run --quiet --script scripts/site_check.py --install $(if $(CI),--with-deps)
	uv run --quiet --script scripts/site_check.py

docs-publish-check: ## Validate publication with fresh public metadata, or build a standalone plugin's ordinary docs
	@if [ -f docs/catalog-sources.toml ]; then \
		if [ "$${REFRESH_PUBLIC:-false}" = true ]; then $(MAKE) docs-catalog || exit $$?; else unset HZ_SITE_CATALOG; fi; \
		$(MAKE) docs-site-check; \
	else $(MAKE) docs-site; fi

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

terminal-art: ## Redraw Heinzelmännchen for the terminal from web/src/components/heinzel.ts (needs node, rsvg-convert, magick)
	@uv run --quiet --script scripts/terminal_art.py && uv run --quiet ruff format --quiet src/heinzel/heads/cli/_art_data.py

build: web-build ## Build the core wheel and sdist into dist/ (the web app first)
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
	rm -rf dist build .coverage* coverage.xml htmlcov reports .pytest_cache .mypy_cache .ruff_cache site src/heinzel/heads/web/static
