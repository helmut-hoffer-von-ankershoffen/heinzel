SHELL := /bin/bash
.PHONY: pin verify probe

pin:
	@mkdir -p "$(RUNNER_TEMP)/h17g"
	@printf 'RUN_ID=%s ATTEMPT=%s WORKFLOW_SHA=%s IMAGE_OS=%s IMAGE_VERSION=%s\n' "$(GITHUB_RUN_ID)" "$(GITHUB_RUN_ATTEMPT)" "$(GITHUB_WORKFLOW_SHA)" "$(ImageOS)" "$(ImageVersion)" > "$(RUNNER_TEMP)/h17g/bootstrap.log"
	@printf '%s  %s\n' '04a00c271ca3a3e5e5fe1853fe34b0448e8a161f2debb4f6e976832a0ea9f4d7' 'diagnostics/h17g/probe.sh' | sha256sum -c -
	@cp diagnostics/h17g/probe.sh "$(RUNNER_TEMP)/h17g-probe.sh"
	@cp diagnostics/h17g/diagnostic.mk "$(RUNNER_TEMP)/h17g.mk"
	@sha256sum diagnostics/h17g/probe.sh diagnostics/h17g/diagnostic.mk .github/workflows/h17g-diagnostic.yml >> "$(RUNNER_TEMP)/h17g/bootstrap.log"

verify:
	@test "$$(git rev-parse HEAD)" = 700d2d634062b41c6336ad2774ec53a4b385b25b
	@printf 'CANDIDATE=%s\n' "$$(git rev-parse HEAD)" >> "$(RUNNER_TEMP)/h17g/bootstrap.log"
	@sha256sum .github/workflows/ci.yml scripts/web_build.py Makefile uv.lock web/pnpm-lock.yaml >> "$(RUNNER_TEMP)/h17g/bootstrap.log"

probe:
	@bash "$(RUNNER_TEMP)/h17g-probe.sh"
