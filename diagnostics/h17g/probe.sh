#!/usr/bin/env bash
set -uo pipefail
# Diagnostic only. Executed on one disposable Ubuntu runner, after exact source checkout.
root="${RUNNER_TEMP:?}/h17g"
mkdir -p "$root"
chmod 755 "$root"
exec > >(tee "$root/summary.log") 2>&1
echo "RUN_ID=${GITHUB_RUN_ID:-unknown} ATTEMPT=${GITHUB_RUN_ATTEMPT:-unknown} WORKFLOW_SHA=${GITHUB_WORKFLOW_SHA:-unknown} RUNNER_OS=${RUNNER_OS:-unknown} IMAGE_OS=${ImageOS:-unknown} IMAGE_VERSION=${ImageVersion:-unknown} CACHE_HIT=${CACHE_HIT:-unknown}"
echo "UTC_START=$(date -u +%FT%TZ)"
echo "SOURCE=$(git rev-parse HEAD)"
[ "$(git rev-parse HEAD)" = 700d2d634062b41c6336ad2774ec53a4b385b25b ] || exit 2
[ "$(uname -m)" = x86_64 ] || exit 2
[ -f /usr/share/keyrings/ubuntu-archive-keyring.gpg ] || exit 2
# Show original configured hosts and resolver path before the candidate override.
echo '=== runner apt configuration ==='
cat /etc/apt/apt-mirrors.txt /etc/apt/sources.list.d/ubuntu.sources
cat /etc/resolv.conf
if ! grep -Fq 'http://azure.archive.ubuntu.com/ubuntu' /etc/apt/apt-mirrors.txt /etc/apt/sources.list.d/ubuntu.sources; then
  echo 'CONFIGURED_MIRROR_MISMATCH=original Azure source not found; comparison not valid'
  exit 4
fi
echo 'CONFIGURED_MIRROR_MATCH=original Azure source observed'
for host in azure.archive.ubuntu.com archive.ubuntu.com security.ubuntu.com; do
  echo "=== DNS $host ==="
  timeout 8s getent ahosts "$host" || echo "DNS_RC=$?"
done
# Probe signed metadata and one package in isolated apt list/cache directories; do not touch system lists.
# APT verifies Release signatures with the standard Ubuntu keyring and verifies package checksums.
for item in 'original http://azure.archive.ubuntu.com/ubuntu' 'primary https://archive.ubuntu.com/ubuntu'; do
  label="${item%% *}"; url="${item#* }"; dir="$root/$label"
  mkdir -p "$dir/lists/partial" "$dir/archives/partial" "$dir/download"
  chmod 755 "$dir" "$dir/lists" "$dir/lists/partial" "$dir/archives" "$dir/archives/partial" "$dir/download"
  printf 'deb [arch=amd64 signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] %s noble main universe\ndeb [arch=amd64 signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] %s noble-updates main universe\n' "$url" "$url" > "$dir/sources.list"
  opts=(-o "Dir::Etc::sourcelist=$dir/sources.list" -o Dir::Etc::sourceparts=- -o "Dir::State::lists=$dir/lists" -o "Dir::Cache::archives=$dir/archives" -o Dir::Cache::pkgcache= -o Dir::Cache::srcpkgcache= -o Acquire::Retries=0 -o Acquire::http::Timeout=8 -o Acquire::https::Timeout=8 -o APT::Update::Error-Mode=any)
  echo "=== SIGNED_APT_UPDATE $label $url ==="
  timeout -k 3s 45s sudo apt-get "${opts[@]}" update > "$dir/update.log" 2>&1
  update_rc=$?; echo "UPDATE_RC=$update_rc"; tail -20 "$dir/update.log"
  if [ "$update_rc" -eq 0 ]; then
    echo "=== APT_POLICY_AND_PACKAGE_URI $label libnss3 ==="
    apt-cache "${opts[@]}" policy libnss3 > "$dir/policy.log" 2>&1
    policy_rc=$?; echo "POLICY_RC=$policy_rc"; cat "$dir/policy.log"
    (cd "$dir/download" && apt-get "${opts[@]}" --print-uris download libnss3) > "$dir/uris.log" 2>&1
    uri_rc=$?; echo "URI_RC=$uri_rc"; cat "$dir/uris.log"
    if [ "$policy_rc" -ne 0 ] || [ "$uri_rc" -ne 0 ]; then
      echo 'PACKAGE_NOT_ATTEMPTED=package policy or URI unavailable'
      continue
    fi
    echo "=== AUTHENTICATED_PACKAGE_DOWNLOAD $label libnss3 ==="
    (cd "$dir/download" && timeout -k 3s 30s apt-get "${opts[@]}" download libnss3) > "$dir/download.log" 2>&1
    download_rc=$?; echo "DOWNLOAD_RC=$download_rc"; tail -12 "$dir/download.log"
    if [ "$download_rc" -eq 0 ]; then sha256sum "$dir"/download/*.deb; fi
  else
    echo 'PACKAGE_NOT_ATTEMPTED=signed metadata unavailable'
  fi
done
# Exact #511/#727 override against both original runner files. No alternate mirror is substituted.
echo '=== CANDIDATE_MIRROR_OVERRIDE ==='
sudo sed -i 's|http://azure\.archive\.ubuntu\.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' /etc/apt/apt-mirrors.txt /etc/apt/sources.list.d/ubuntu.sources || exit 3
cat /etc/apt/apt-mirrors.txt /etc/apt/sources.list.d/ubuntu.sources
echo '=== CANDIDATE_BROWSER_SETUP ==='
start=$(date +%s)
# Diagnostic cap retains time for failure evidence; candidate job itself has only its 15m job timeout.
timeout -k 5s 540s make test-browser-deps > "$root/setup.log" 2>&1
setup_rc=$?; echo "SETUP_RC=$setup_rc SECONDS=$(($(date +%s)-start))"; tail -35 "$root/setup.log"
echo 'CHROMIUM_STATUS=NOT_ATTEMPTED WEBKIT_STATUS=NOT_ATTEMPTED'
smoke_rc=0
if [ "$setup_rc" -eq 0 ]; then
  for browser in chromium webkit; do
    echo "=== HEADLESS_SMOKE $browser ==="
    BROWSER_NAME="$browser" timeout -k 5s 40s uv run --quiet --script scripts/web_build.py exec node --input-type=module -e '
      import {chromium, webkit} from "playwright-core";
      const name = process.env.BROWSER_NAME;
      const type = {chromium, webkit}[name];
      const browser = await type.launch({headless: true, timeout: 20000});
      try {
        const page = await browser.newPage();
        page.setDefaultTimeout(10000);
        await page.setContent("<title>h17g</title><p>ready</p>", {timeout: 10000});
        if ((await page.locator("p").textContent()) !== "ready") throw new Error("content mismatch");
        console.log(name + " PASS");
      } finally { await browser.close(); }
    ' > "$root/$browser.log" 2>&1
    browser_rc=$?; echo "${browser}_RC=$browser_rc"; cat "$root/$browser.log"
    if [ "$browser_rc" -ne 0 ]; then smoke_rc=1; fi
  done
else
  echo 'SMOKE_NOT_ATTEMPTED=setup did not succeed'
  smoke_rc=1
fi
echo "UTC_END=$(date -u +%FT%TZ)"
# Preserve failure status: no masked setup or smoke result.
[ "$setup_rc" -eq 0 ] && [ "$smoke_rc" -eq 0 ]
