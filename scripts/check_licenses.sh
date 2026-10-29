#!/usr/bin/env bash
# Fail when a runtime dependency's license is not in .license-allowlist (dev tools are not distributed, so they don't
# count). Builds a throwaway environment with only the project and its runtime dependencies, then lets pip-licenses read the installed metadata (SPDX License-Expression first, classifiers as
# fallback). `make licenses` checks the web app's bundled packages the same way (scripts/web_build.py licenses).
# Usage: bash scripts/check_licenses.sh
set -euo pipefail

allowed="$(grep -v '^\s*#' .license-allowlist | grep -v '^\s*$' | paste -sd ';' -)"
# The owning project is MIT. Concrete plugins are checked in their own projects.
ours="$(uv version --color never | cut -d' ' -f1)"
# shellcheck disable=SC2086 # one package name per word
uv run --quiet --no-dev --isolated --with "pip-licenses>=5,<6" pip-licenses \
  --from=mixed --partial-match --allow-only="$allowed" \
  --ignore-packages $ours pip-licenses prettytable wcwidth \
  --format=plain >/dev/null
echo "Runtime dependency licenses: all allowed."
