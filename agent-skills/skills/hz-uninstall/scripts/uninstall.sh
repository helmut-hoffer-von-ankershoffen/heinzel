#!/usr/bin/env bash
# Uninstall Heinzelmännchen: its agent skills, then the CLI with its plugins. With --purge, also the user configuration file,
# the data directory and the cache directory. Run by the hz-uninstall skill.
#
# Usage: uninstall.sh [--purge]
set -euo pipefail

purge=""
case "${1:-}" in
  "") ;;
  --purge) purge=1 ;;
  -h | --help) sed -n '2,6p' "$0"; exit 0 ;;
  *) echo "error: unknown argument $1" >&2; exit 2 ;;
esac

command -v uv >/dev/null || { echo "error: uv is required" >&2; exit 1; }
hz="$(uv --color never tool dir --bin)/hz"
[[ -x "$hz" ]] || { echo "Heinzelmännchen is not installed."; exit 0; }

paths=()
if [[ -n "$purge" ]]; then
  # Ask Heinzelmännchen where its own files are, from $HOME and without overrides, so no project hz.toml or variable can point
  # the purge somewhere else. Only directories named hz below $HOME, and the user config file, are ever removed.
  report="$(cd "$HOME" && env -u HZ_CONFIG -u HZ_DATA_DIR -u HZ_CACHE_DIR "$hz" system config show --json)"
  while IFS= read -r path; do paths+=("$path"); done < <(
    printf '%s' "$report" | "$(uv --color never tool dir)/heinzel/bin/python" -c '
import json, sys
report = json.load(sys.stdin)
for f in report["files"]:
    if f["scope"] == "user" and f["exists"]:
        print(f["path"])
for s in report["settings"]:
    if s["section"] == "hz" and s["key"] in ("data_dir", "cache_dir"):
        print(s["value"])
')
fi

echo "==> Removing agent skills"
"$hz" system agent uninstall

echo "==> Uninstalling Heinzelmännchen and its plugins"
uv tool uninstall heinzel

for path in ${paths[@]+"${paths[@]}"}; do
  [[ -e "$path" ]] || continue
  case "$path" in
    "$HOME"/*/hz | "$HOME"/*/hz/config.toml) ;;
    *) echo "Keeping $path: not a Heinzelmännchen directory below \$HOME." >&2; continue ;;
  esac
  echo "==> Removing $path"
  rm -rf -- "$path"
done
echo "==> Done"
