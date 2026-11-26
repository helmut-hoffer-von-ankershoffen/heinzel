#!/usr/bin/env bash
# Render the plugin template from this working tree and check the generated plugin against a built core wheel.
# Usage: [TEMPLATE_AUDIT=1] bash scripts/template-test.sh [destination]   (default: a temporary directory)
# TEMPLATE_AUDIT=1 also runs the rendered plugin's `make audit` (pip-audit), which needs network.
set -euo pipefail
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX # a caller's hook must not redirect the disposable repository

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
destination="${1:-$(mktemp -d)/heinzel-example}"

uv run --no-build --quiet --script scripts/build.py prepare
candidate_dir="$(mktemp -d)"
uv run --no-build --quiet --script scripts/build.py --wheel --quiet --out-dir "$candidate_dir"
core_wheel="$(find "$candidate_dir" -name 'heinzel-*.whl' -print -quit)"
[[ -n "$core_wheel" && -f "$core_wheel" ]]

# The starter journey reads the same built artifact and template tree, but owns its output and cache directories.
pyyaml_wheels="$(mktemp -d)"
bash scripts/bootstrap-pyyaml.sh --wheel-dir "$pyyaml_wheels"
starter_destination="$(mktemp -d)/starter"
mkdir -p "$starter_destination"
starter_pid=""
starter_grouped=0
stop_starter() {
  [[ -n "$starter_pid" ]] || return 0
  local attempt
  if (( starter_grouped )); then
    kill -TERM -- "-$starter_pid" 2>/dev/null || true
  else
    kill -TERM "$starter_pid" 2>/dev/null || true
  fi
  for ((attempt = 0; attempt < 100; attempt++)); do
    if (( starter_grouped )); then
      kill -0 -- "-$starter_pid" 2>/dev/null || break
    else
      kill -0 "$starter_pid" 2>/dev/null || break
    fi
    sleep 0.05
  done
  if (( attempt == 100 )); then
    if (( starter_grouped )); then
      kill -KILL -- "-$starter_pid" 2>/dev/null || true
    else
      kill -KILL "$starter_pid" 2>/dev/null || true
    fi
  fi
  wait "$starter_pid" 2>/dev/null || true
  starter_pid=""
}
on_exit() {
  local status=$?
  trap - EXIT INT TERM HUP
  stop_starter
  exit "$status"
}
on_signal() {
  local status=$1
  trap - EXIT INT TERM HUP
  stop_starter
  exit "$status"
}
trap on_exit EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP
if command -v setsid >/dev/null 2>&1; then
  starter_grouped=1
  (
    unset VIRTUAL_ENV UV_FROZEN
    export UV_FIND_LINKS="$pyyaml_wheels" UV_CACHE_DIR="$starter_destination/uv-cache"
    export UV_TOOL_DIR="$starter_destination/uv-tool"
    exec setsid uv run --no-project --script scripts/starter-test.py \
      --core "$core_wheel" --template "$repo_root" --destination "$starter_destination"
  ) &
else
  (
    unset VIRTUAL_ENV UV_FROZEN
    export UV_FIND_LINKS="$pyyaml_wheels" UV_CACHE_DIR="$starter_destination/uv-cache"
    export UV_TOOL_DIR="$starter_destination/uv-tool"
    exec uv run --no-project --script scripts/starter-test.py \
      --core "$core_wheel" --template "$repo_root" --destination "$starter_destination"
  ) &
fi
starter_pid=$!

uv run --frozen copier copy --defaults --vcs-ref HEAD --quiet \
  --data preset=governed --data plugin_name=sprocket --data author_name="Template Test" --data author_email=template-test@example.test \
  --data github_owner=helmut-hoffer-von-ankershoffen \
  "$repo_root" "$destination"
cd "$destination"
git init --quiet -b main

# Install the candidate artifact; the plugin never imports or builds the core checkout.
uv run --quiet --no-project python - "$core_wheel" <<'PY' # uv's Python: a fresh Linux may have none
import pathlib, re, sys
path = pathlib.Path("pyproject.toml")
local = f'"heinzel @ {pathlib.Path(sys.argv[1]).as_uri()}"'
path.write_text(re.sub(r'"heinzel==[^"]*"', local, path.read_text()))
PY

unset VIRTUAL_ENV UV_FROZEN # the rendered plugin has no lockfile yet, and is not this project
[[ ! -e uv.lock ]] # a fresh project: the documented `make setup` creates the lock itself, then syncs frozen
make setup
[[ -f uv.lock ]]
make fix
git add --all
git -c user.name=template-test -c user.email=template-test@example.test commit --quiet -m "chore: scaffold from template"
make docs-plugins # the plugin's page (ADR 0082), which `make check` then checks; `make fix` writes it for people
make check
HZ_SPEC="$core_wheel" make test-e2e
if [ -n "${TEMPLATE_AUDIT:-}" ]; then make audit; fi
wait "$starter_pid"
starter_pid=""
echo "Template rendered and checked in $destination"
