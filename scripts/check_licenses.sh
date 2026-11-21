#!/usr/bin/env bash
# Fail when a runtime dependency's license is not in .license-allowlist (dev tools are not distributed, so they don't
# count). Installs only locked runtime dependencies into a throwaway environment, then lets pip-licenses read their metadata (SPDX License-Expression first, classifiers as
# fallback). `make licenses` checks the web app's bundled packages the same way (scripts/web_build.py licenses).
# Usage: bash scripts/check_licenses.sh
set -euo pipefail

allowed="$(grep -v '^\s*#' .license-allowlist | grep -v '^\s*$' | paste -sd ';' -)"
# A throwaway environment instead of an isolated `uv run`, so the check never touches the project environment.
environment="$(mktemp -d)/licenses"
trap 'rm -rf "$(dirname "$environment")"' EXIT
uv venv --quiet "$environment"
UV_PROJECT_ENVIRONMENT="$environment" uv sync --quiet --frozen --no-build --no-dev --no-install-project
uv pip install --quiet --no-build --python "$environment/bin/python" "pip-licenses>=5,<6"
"$environment/bin/pip-licenses" \
  --from=mixed --partial-match --allow-only="$allowed" \
  --ignore-packages pip-licenses prettytable wcwidth \
  --format=plain >/dev/null
echo "Runtime dependency licenses: all allowed."
