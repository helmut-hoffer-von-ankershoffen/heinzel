#!/usr/bin/env bash
# Render the plugin template from this working tree and check the generated plugin against a built core wheel.
# Usage: [TEMPLATE_AUDIT=1] bash scripts/template-test.sh [destination]   (default: a temporary directory)
# TEMPLATE_AUDIT=1 also runs the rendered plugin's `make audit` (pip-audit), which needs network.
set -euo pipefail
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX # a caller's hook must not redirect the disposable repository

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
destination="${1:-$(mktemp -d)/heinzel-example}"

uv run --frozen copier copy --defaults --vcs-ref HEAD --quiet \
  --data preset=governed --data plugin_name=sprocket --data author_name="Template Test" --data author_email=template-test@example.test \
  --data github_owner=helmut-hoffer-von-ankershoffen \
  "$repo_root" "$destination"
candidate_dir="$(mktemp -d)"
uv build --wheel --quiet --out-dir "$candidate_dir"
core_wheel="$(find "$candidate_dir" -name 'heinzel-*.whl' -print -quit)"
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
uv lock --quiet
make setup
make fix
git add --all
git -c user.name=template-test -c user.email=template-test@example.test commit --quiet -m "chore: scaffold from template"
make docs-plugins # the plugin's page (ADR 0082), which `make check` then checks; `make fix` writes it for people
make check
HZ_SPEC="$core_wheel" make test-e2e
if [ -n "${TEMPLATE_AUDIT:-}" ]; then make audit; fi
echo "Template rendered and checked in $destination"

# New authors use the small presets. Exercise their actual installed-artifact commands in both layouts too.
cd "$repo_root"
uv run --no-project --script scripts/starter-test.py --core "$core_wheel"
