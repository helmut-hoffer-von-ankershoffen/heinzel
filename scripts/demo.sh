#!/usr/bin/env bash
# Assemble docs/assets/demo.tape and render docs/assets/demo.gif with VHS, running the real hz from this checkout.
# Usage: bash scripts/demo.sh   (make demo)
# hz is installed editable into a throwaway uv tool directory, with its own configuration, data and state, so the
# recording neither touches your own hz nor shows your plugins or settings.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
# vhs, ttyd, ffmpeg and jq: the demo group of scripts/tools.toml, pinned and checksummed into .tools/bin. A platform
# without a pinned build (ttyd on macOS) needs the program on PATH, and the line for it says how to install it.
uv run --quiet --script scripts/tools.py install demo || { echo "make demo needs the tools above." >&2; exit 2; }
export PATH="$repo_root/.tools/bin:$PATH"
uv run --quiet --script scripts/demo_tape.py

# A short, fixed path: `hz system doctor` shows these directories in the recording.
sandbox=/tmp/hz-demo
rm -rf "$sandbox"
trap 'rm -rf "$sandbox"' EXIT
while read -r name; do unset "$name"; done < <(compgen -e | grep '^HZ_' || true)
export UV_TOOL_DIR="$sandbox/tools" UV_TOOL_BIN_DIR="$sandbox/bin"
export HZ_CONFIG_HOME="$sandbox/config" HZ_DATA_DIR="$sandbox/data" HZ_CACHE_DIR="$sandbox/cache" HZ_STATE_DIR="$sandbox/state" \
  HZ_LOG_DIR="$sandbox/log"
# No update notice: it would depend on the latest release at recording time, and the check can add up to 2 seconds.
export HZ_UPDATE_CHECK=false
export PATH="$UV_TOOL_BIN_DIR:$PATH"
uv tool install --quiet --editable .
vhs docs/assets/demo.tape
echo "✓ docs/assets/demo.gif; look at the last frames before committing it"
