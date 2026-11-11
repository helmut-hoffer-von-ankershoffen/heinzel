#!/usr/bin/env bash
set -uo pipefail
out="${RUNNER_TEMP:?}/h17g-node-output"
mkdir -p "$out"
phase=bootstrap
finish() { code=$?; printf 'END_UTC=%s PHASE=%s EXIT=%s\n' "$(date -u +%FT%TZ)" "$phase" "$code"; }
trap finish EXIT
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_OS:-} == Linux && $(uname -m) == x86_64 ]] || exit 2
[[ $(git rev-parse HEAD) == 700d2d634062b41c6336ad2774ec53a4b385b25b ]] || exit 2
printf 'RUN=%s ATTEMPT=%s CANDIDATE=%s IMAGE=%s IMAGE_VERSION=%s\n' "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" "$(git rev-parse HEAD)" "${ImageOS:-}" "${ImageVersion:-}"
cp /etc/apt/sources.list.d/ubuntu.sources "$out/original-ubuntu.sources" || exit 2
sha256sum /usr/share/keyrings/ubuntu-archive-keyring.gpg > "$out/keyring.sha256" || exit 2
phase=source-selection
cp "$RUNNER_TEMP/h17g-node-receipt.json" "$out/python-v5-receipt.json" || exit 2
python3 - "$out" <<'PYTHON'
import hashlib, json, pathlib, sys
out = pathlib.Path(sys.argv[1])
r = json.loads((out / "python-v5-receipt.json").read_text())
for field, path in (("original_source_sha256", out / "original-ubuntu.sources"),
                    ("keyring_sha256", pathlib.Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg"))):
    if hashlib.sha256(path.read_bytes()).hexdigest() != r[field]:
        raise SystemExit(f"RECEIPT_BINDING_MISMATCH={field}")
    print(f"RECEIPT_BINDING_OK={field}")
PYTHON
binding_rc=$?
[[ $binding_rc == 0 ]] || exit "$binding_rc"
sudo python3 -I "$RUNNER_TEMP/h17g-node-mirror.py" || exit 2
cp /etc/apt/sources.list.d/ubuntu.sources "$out/selected-ubuntu.sources" || exit 2
python3 - "$out" <<'PYTHON'
import hashlib, json, pathlib, sys
out = pathlib.Path(sys.argv[1])
receipt = json.loads((out / "python-v5-receipt.json").read_text())
files = {"original_source_sha256": out / "original-ubuntu.sources",
         "alternate_source_sha256": out / "selected-ubuntu.sources",
         "keyring_sha256": pathlib.Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")}
for name, path in files.items():
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != receipt[name]:
        raise SystemExit(f"RECEIPT_BINDING_MISMATCH={name} actual={actual}")
    print(f"RECEIPT_BINDING_OK={name} SHA256={actual}")
PYTHON
binding_rc=$?
[[ $binding_rc == 0 ]] || exit "$binding_rc"
# The installer must fetch fresh authenticated indexes and packages on this runner.
phase=apt-clean
sudo apt-get clean || exit 2
sudo find /var/lib/apt/lists -mindepth 1 -maxdepth 1 ! -name lock -exec rm -rf -- {} + || exit 2
sudo mkdir -p /var/lib/apt/lists/partial /var/cache/apt/archives/partial || exit 2
sudo chown _apt:root /var/lib/apt/lists/partial /var/cache/apt/archives/partial || exit 2
sudo -u _apt test -w /var/lib/apt/lists/partial || exit 2
sudo -u _apt test -w /var/cache/apt/archives/partial || exit 2
phase=install
pin_epoch=$(cat "$out/pin-epoch.txt")
remaining=$((pin_epoch + 840 - $(date +%s)))
printf 'EVIDENCE_BUDGET_SECONDS=%s\n' "$remaining"
if (( remaining < 800 )); then
  printf '%s\n' 'INSTALL=NOT_ATTEMPTED BROWSERS=NOT_ATTEMPTED (insufficient evidence budget)'
  exit 1
fi
start=$(date +%s)
timeout -k 5s 700s make test-browser-deps > "$out/node-install.log" 2>&1
install_rc=$?
printf 'INSTALL_RC=%s INSTALL_SECONDS=%s\n' "$install_rc" "$(($(date +%s)-start))"
if grep -Eiq 'unsandboxed as root|couldn.t be accessed by user ._apt.|permission denied' "$out/node-install.log"; then
  printf '%s\n' 'APT_UNPRIVILEGED_INSTALL=FAILED'; exit 1
fi
if grep -Eq '^(Err:|W:|E:)' "$out/node-install.log"; then
  printf '%s\n' 'APT_INDEX_OR_INSTALL_WARNING=FAILED'; exit 1
fi
if [[ $install_rc == 0 ]]; then
  for suite in noble noble-updates noble-backports noble-security; do
    mapfile -t indexes < <(find /var/lib/apt/lists -maxdepth 1 -type f -name "*mirrors.edge.kernel.org*ubuntu_dists_${suite}_InRelease" -print)
    if [[ ${#indexes[@]} != 1 ]]; then printf 'MISSING_OR_AMBIGUOUS_FRESH_INDEX=%s\n' "$suite"; exit 1; fi
    sudo sha256sum "${indexes[0]}" >> "$out/fresh-indexes.sha256" || exit 1
    printf 'SUITE=%s\n' "$suite" >> "$out/index-freshness.txt"
    grep -E '^(Date|Valid-Until):' "${indexes[0]}" >> "$out/index-freshness.txt" || exit 1
  done
fi
if [[ $install_rc != 0 ]]; then printf '%s\n' 'CHROMIUM=NOT_ATTEMPTED WEBKIT=NOT_ATTEMPTED'; exit "$install_rc"; fi
phase=smoke
failed=0
for browser in chromium webkit; do
  timeout -k 3s 20s uv run --quiet --script scripts/web_build.py exec node "$RUNNER_TEMP/h17g-node-smoke.cjs" "$browser" > "$out/$browser.log" 2>&1
  rc=$?
  printf '%s_RC=%s\n' "${browser^^}" "$rc"
  cat "$out/$browser.log"
  [[ $rc == 0 ]] || failed=1
done
exit "$failed"
