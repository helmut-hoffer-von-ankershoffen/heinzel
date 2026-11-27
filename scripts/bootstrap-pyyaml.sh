#!/usr/bin/env bash
# Python 3.15 has no upstream PyYAML wheel. Build its hash-pinned pure-Python distribution explicitly,
# without credentials, in a disposable environment whose backend wheels are also hash-pinned.
# The source, its hash and the backend pins live in one place, the standard-library module this runs as a file:
# src/heinzel/bootstrap_pyyaml.py in Core (the Core wheel ships it for the plugin toolkit), next to this script in a
# project generated from the template.
set -euo pipefail
module="$(dirname "$0")/../src/heinzel/bootstrap_pyyaml.py"
[ -f "$module" ] || module="$(dirname "$0")/bootstrap_pyyaml.py"
# A new plugin locks the reviewed source even when Core already uses an upstream wheel.
if [ "${1:-}" = "--plugin-wheel-dir" ]; then
  exec python3 -I -S "$module" build --wheel-dir "${2:?--plugin-wheel-dir needs a directory}" >/dev/null
fi
# `bootstrap-pyyaml.sh --wheel-dir DIR` only builds the wheel into DIR (for UV_FIND_LINKS); it installs nothing.
if [ "${1:-}" = "--wheel-dir" ]; then
  exec python3 -I -S "$module" build --lock uv.lock --wheel-dir "${2:?--wheel-dir needs a directory}" >/dev/null
fi
project_python="${UV_PROJECT_ENVIRONMENT:-.venv}/bin/python"
# A newly generated project resolves its own lock: the module refuses a lock that selects a different PyYAML.
exec "$project_python" -I -S "$module" install --lock uv.lock --python "$project_python"
