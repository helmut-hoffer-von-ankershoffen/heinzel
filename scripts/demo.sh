#!/usr/bin/env bash
# Assemble docs/assets/demo.tape and render docs/assets/demo.gif with VHS, running the real hz from this checkout.
# Usage: bash scripts/demo.sh   (make demo)
# hz is installed editable into a throwaway uv tool directory, with its own configuration, data and state, so the
# recording neither touches your own hz nor shows your plugins or settings.
set -euo pipefail

missing=()
for tool in vhs jq uv; do command -v "$tool" >/dev/null || missing+=("$tool"); done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "make demo needs ${missing[*]} on PATH. Install: brew install vhs jq (macOS), or see" >&2
  echo "https://github.com/charmbracelet/vhs#installation and https://jqlang.org/download/ (uv: https://docs.astral.sh/uv/)." >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --quiet --script scripts/demo_tape.py

# A short, fixed path: `hz system doctor` shows these directories in the recording.
sandbox=/tmp/hz-demo
rm -rf "$sandbox"
trap 'rm -rf "$sandbox"' EXIT
while read -r name; do unset "$name"; done < <(compgen -e | grep '^HZ_' || true)
export UV_TOOL_DIR="$sandbox/tools" UV_TOOL_BIN_DIR="$sandbox/bin"
export HZ_CONFIG_HOME="$sandbox/config" HZ_DATA_DIR="$sandbox/data" HZ_CACHE_DIR="$sandbox/cache" HZ_STATE_DIR="$sandbox/state" \
  HZ_LOG_DIR="$sandbox/log"
export PATH="$UV_TOOL_BIN_DIR:$PATH"
uv tool install --quiet --editable .
vhs docs/assets/demo.tape
echo "✓ docs/assets/demo.gif; look at the last frames before committing it"
