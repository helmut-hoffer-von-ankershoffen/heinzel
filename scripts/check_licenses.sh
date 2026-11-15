#!/usr/bin/env bash
# Fail when a runtime dependency's license is not in .license-allowlist (dev tools are not distributed, so they don't
# count). Builds a throwaway environment with only the project and its runtime dependencies, then lets pip-licenses read the installed metadata (SPDX License-Expression first, classifiers as
# fallback). `make licenses` checks the web app's bundled packages the same way (scripts/web_build.py licenses).
# Usage: bash scripts/check_licenses.sh
set -euo pipefail

allowed="$(grep -v '^\s*#' .license-allowlist | grep -v '^\s*$' | paste -sd ';' -)"
# The owning project is MIT. Concrete plugins are checked in their own projects.
ours="$(uv version --color never | cut -d' ' -f1)"
# A throwaway environment instead of an isolated `uv run`: Python 3.15 has no PyYAML wheel, and this one gets the
# reviewed, hash-pinned PyYAML build instead of a source build with an unpinned backend.
environment="$(mktemp -d)/licenses"
trap 'rm -rf "$(dirname "$environment")"' EXIT
uv venv --quiet "$environment"
# `uv sync` is exact and would remove a PyYAML installed before it, so sync first and build the reviewed one after.
UV_PROJECT_ENVIRONMENT="$environment" uv sync --quiet --frozen --no-dev --no-install-package pyyaml
UV_PROJECT_ENVIRONMENT="$environment" bash "$(dirname "$0")/bootstrap-pyyaml.sh"
uv pip install --quiet --python "$environment/bin/python" "pip-licenses>=5,<6"
# shellcheck disable=SC2086 # one package name per word
"$environment/bin/pip-licenses" \
  --from=mixed --partial-match --allow-only="$allowed" \
  --ignore-packages $ours pip-licenses prettytable wcwidth \
  --format=plain >/dev/null
echo "Runtime dependency licenses: all allowed."
