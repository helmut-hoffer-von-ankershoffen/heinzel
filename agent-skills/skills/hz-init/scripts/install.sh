#!/usr/bin/env bash
# Install or update Heinzelmännchen: the CLI (uv tool), plugins, a starter configuration and Heinzelmännchen's agent skills.
# Run by the hz-init skill; safe to run again. Honours uv's UV_TOOL_DIR / UV_TOOL_BIN_DIR and Heinzelmännchen's HZ_* settings.
#
# Usage: install.sh [--version vX.Y.Z] [--plugin SPEC]...
#   HZ_SOURCE overrides where Heinzelmännchen comes from (default: the GitHub repository; a wheel or path works too).
set -euo pipefail

source_spec="${HZ_SOURCE:-git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel}"
version=""
plugins=()

while (($#)); do
  case "$1" in
    --version) version="${2:?--version needs a value}"; shift 2 ;;
    --plugin) plugins+=("${2:?--plugin needs a value}"); shift 2 ;;
    -h | --help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "error: unknown argument $1" >&2; exit 2 ;;
  esac
done

command -v uv >/dev/null || { echo "error: uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }

spec="$source_spec${version:+@$version}"
hz="$(uv --color never tool dir --bin)/hz"
if [[ -x "$hz" ]]; then
  echo "==> Updating Heinzelmännchen from $spec, keeping its plugins"
  "$hz" system update --source "$spec"
else
  echo "==> Installing Heinzelmännchen from $spec"
  uv tool install "$spec"
fi

for plugin in ${plugins[@]+"${plugins[@]}"}; do
  echo "==> Adding plugin $plugin"
  "$hz" system plugin add "$plugin"
done

echo "==> Configuration"
"$hz" system config init 2>/dev/null || echo "Keeping the existing user configuration."

echo "==> Agent skills"
"$hz" system agent install

echo "==> Done"
"$hz" --version
"$hz" system plugin list
case ":$PATH:" in
  *":$(uv --color never tool dir --bin):"*) ;;
  *) echo "Note: $(uv --color never tool dir --bin) is not on PATH; run 'uv tool update-shell' and open a new shell." ;;
esac
