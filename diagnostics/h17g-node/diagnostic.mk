SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.ONESHELL:
.RECIPEPREFIX := >
.PHONY: pin verify probe

pin:
>printf '%s  %s\n' "$(EXPECTED_DIAGNOSTIC_MK_SHA256)" diagnostics/h17g-node/diagnostic.mk | sha256sum -c -
>test "$$(git rev-parse HEAD)" = "$$GITHUB_SHA"
>sha256sum .github/workflows/h17g-node-proof.yml diagnostics/h17g-node/diagnostic.mk diagnostics/h17g-node/pins.sha256 > "$$RUNNER_TEMP/h17g-node-output/transport.sha256"
>printf '%s\n' \
>  '63e5297b9e40a635942bec5dd994717422fc76581ac0edce888772135b9c72d3  diagnostics/h17g-node/probe.sh' \
>  '9a0b59c087f9a9acfb227224dd2d0c9ba752fa31fb55a3e3149d9b0ee04d0fbd  diagnostics/h17g-node/smoke.cjs' \
>  'dfe5d67f00f06216b6b507d7a925eb14a153f3c4e4f44276e8659383e53fb206  diagnostics/h17g-node/ubuntu_browser_mirror.py' \
>  'f978e13c4ceb2c0963579d5786c93ce2bc6adbec2941f019a82a3ee19787ae01  diagnostics/h17g-node/python-v5-receipt.json' | sha256sum -c -
>cp diagnostics/h17g-node/probe.sh "$$RUNNER_TEMP/h17g-node-probe.sh"
>cp diagnostics/h17g-node/smoke.cjs "$$RUNNER_TEMP/h17g-node-smoke.cjs"
>cp diagnostics/h17g-node/ubuntu_browser_mirror.py "$$RUNNER_TEMP/h17g-node-mirror.py"
>cp diagnostics/h17g-node/pins.sha256 "$$RUNNER_TEMP/h17g-node-pins.sha256"
>cp diagnostics/h17g-node/python-v5-receipt.json "$$RUNNER_TEMP/h17g-node-receipt.json"
>cp diagnostics/h17g-node/diagnostic.mk "$$RUNNER_TEMP/h17g-node.mk"

verify:
>test "$$(git rev-parse HEAD)" = 700d2d634062b41c6336ad2774ec53a4b385b25b
>sha256sum -c "$$RUNNER_TEMP/h17g-node-pins.sha256" | tee "$$RUNNER_TEMP/h17g-node-output/source-pins.log"
>python3 -c 'import json; assert json.load(open("web/package.json"))["devDependencies"]["playwright-core"] == "1.64.1"'

probe:
>set -o pipefail
>bash "$$RUNNER_TEMP/h17g-node-probe.sh" 2>&1 | tee "$$RUNNER_TEMP/h17g-node-output/summary.log"
