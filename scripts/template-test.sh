#!/usr/bin/env bash
# Render the plugin template from this working tree and run the rendered plugin's checks against this Heinzelmännchen checkout.
# Usage: [TEMPLATE_AUDIT=1] bash scripts/template-test.sh [destination]   (default: a temporary directory)
# TEMPLATE_AUDIT=1 also runs the rendered plugin's `make audit` (pip-audit), which needs network.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
destination="${1:-$(mktemp -d)/heinzel-example}"

uv run --frozen copier copy --defaults --vcs-ref HEAD --quiet \
  --data author_name="Template Test" --data author_email=template-test@localhost \
  --data github_owner=helmut-hoffer-von-ankershoffen \
  "$repo_root" "$destination"
cd "$destination"
git init --quiet -b main

# Test against this checkout instead of the released Heinzelmännchen the template points at.
uv run --quiet --no-project python - "$repo_root" <<'PY' # uv's Python: a fresh Linux may have none
import pathlib, re, sys
path = pathlib.Path("pyproject.toml")
local = f'"heinzel @ {pathlib.Path(sys.argv[1]).as_uri()}",'
path.write_text(re.sub(r'^    "heinzel @ git\+https://[^"]*",$', "    " + local, path.read_text(), flags=re.M))
PY

unset VIRTUAL_ENV UV_FROZEN # the rendered plugin has no lockfile yet, and is not this project
uv lock --quiet
git add --all
git -c user.name=template-test -c user.email=template-test@localhost commit --quiet -m "chore: scaffold from template"
make check
if [ -n "${TEMPLATE_AUDIT:-}" ]; then make audit; fi
echo "Template rendered and checked in $destination"
