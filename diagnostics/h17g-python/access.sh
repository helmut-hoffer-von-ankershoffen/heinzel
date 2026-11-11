#!/usr/bin/env bash
# Every check runs as _apt; report every predicate without exposing APT state to artifacts.
set -uo pipefail
state=${1:?}; route=${2:?}
case "$route" in unchanged|alternate) ;; *) exit 2 ;; esac
bad=0
check() {
  local label=$1 mode=$2 path=$3
  if sudo -u _apt test "$mode" "$path"; then
    printf 'APT_ACCESS_%s=PASS\n' "$label"
  else
    printf 'APT_ACCESS_%s=FAIL\n' "$label"
    bad=1
  fi
}
check TMP_TRAVERSE -x /tmp
check SCRATCH_TRAVERSE -x "$state"
check ROUTE_TRAVERSE -x "$state/$route"
check CONFIG_TRAVERSE -x "$state/$route/config"
check SOURCE_READ -r "$state/$route/config/ubuntu.sources"
check MIRRORS_READ -r "$state/$route/config/apt-mirrors.txt"
check LISTS_TRAVERSE -x "$state/$route/lists"
check LISTS_PARTIAL_TRAVERSE -x "$state/$route/lists/partial"
check LISTS_PARTIAL_WRITE -w "$state/$route/lists/partial"
check ARCHIVES_TRAVERSE -x "$state/$route/archives"
check ARCHIVES_PARTIAL_TRAVERSE -x "$state/$route/archives/partial"
check ARCHIVES_PARTIAL_WRITE -w "$state/$route/archives/partial"
check DOWNLOAD_TRAVERSE -x "$state/$route/download"
check DOWNLOAD_WRITE -w "$state/$route/download"
check KEYRING_READ -r /usr/share/keyrings/ubuntu-archive-keyring.gpg
exit "$bad"
