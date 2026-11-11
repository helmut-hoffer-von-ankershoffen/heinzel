#!/usr/bin/env bash
# One disposable GitHub Ubuntu runner; no custom resolvers, addresses, or trust changes.
set -uo pipefail
out="${RUNNER_TEMP:?}/h17g-python-output"
mkdir -p "$out"
phase=bootstrap
finish() { code=$?; printf 'END_UTC=%s PHASE=%s EXIT=%s\n' "$(date -u +%FT%TZ)" "$phase" "$code"; }
trap finish EXIT
printf 'START_UTC=%s RUN=%s ATTEMPT=%s TRANSPORT=%s\n' "$(date -u +%FT%TZ)" "${GITHUB_RUN_ID:-}" "${GITHUB_RUN_ATTEMPT:-}" "${GITHUB_SHA:-}"
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_OS:-} == Linux && $(uname -m) == x86_64 ]] || exit 2
# The hosted Linux /tmp parent is traversable to _apt; never place APT state under runner-temp ancestors.
state=$(mktemp -d /tmp/h17g-python-XXXXXXXX) || exit 2
chmod 755 "$state" || exit 2
printf 'APT_SCRATCH=%s\n' "$state"
[[ $(git rev-parse HEAD) == 700d2d634062b41c6336ad2774ec53a4b385b25b ]] || exit 2
[[ $(dpkg --print-architecture) == amd64 ]] || exit 2
[[ -r /usr/share/keyrings/ubuntu-archive-keyring.gpg ]] || exit 2
sha256sum /usr/share/keyrings/ubuntu-archive-keyring.gpg | tee "$out/keyring.sha256"
cp /etc/apt/sources.list.d/ubuntu.sources "$out/original-ubuntu.sources" || exit 2
cp /etc/apt/apt-mirrors.txt "$out/original-apt-mirrors.txt" || exit 2
printf 'ARCH=%s IMAGE=%s IMAGE_VERSION=%s\n' "$(dpkg --print-architecture)" "${ImageOS:-}" "${ImageVersion:-}"
dpkg --print-foreign-architectures > "$out/foreign-architectures.txt" || exit 2
phase=control
printf '%s\n' '=== DNS and benign HTTPS control ==='
for host in archive.ubuntu.com mirrors.edge.kernel.org github.com; do
  printf 'DNS_HOST=%s\n' "$host"
  timeout -k 1s 4s getent ahosts "$host" > "$out/dns-$host.log" 2>&1
  printf 'DNS_RC=%s\n' "$?"
done
curl --fail --location --silent --show-error --max-time 6 --output "$state/control-body" --write-out 'CONTROL_HTTP=%{http_code} CONTROL_BYTES=%{size_download}\n' https://github.com/robots.txt > "$out/control.log" 2>&1
printf 'CONTROL_RC=%s\n' "$?"
cat "$out/control.log"

phase=route-comparison
probe_route() {
  route="$1"
  conf="$state/$route/config"; lists="$state/$route/lists"; archives="$state/$route/archives"; download="$state/$route/download"
  mkdir -p "$conf" "$lists/partial" "$archives/partial" "$download"
  chmod 755 "$state" "$state/$route" "$conf" "$lists" "$archives"
  if ! chmod 755 "$lists/partial" "$archives/partial" "$download" ||
     ! sudo chown _apt:root "$lists/partial" "$archives/partial" "$download"; then
    printf 'APT_PERMISSION_SETUP=FAILED ROUTE=%s\n' "$route"; return 1
  fi
  printf '=== ROUTE=%s ===\n' "$route"
  python3 "${RUNNER_TEMP:?}/h17g-python-sources.py" "$route" "$out/original-ubuntu.sources" "$out/original-apt-mirrors.txt" "$conf" > "$out/$route-config.log" 2>&1
  config_rc=$?
  printf 'CONFIG_RC=%s\n' "$config_rc"
  if [[ $config_rc != 0 ]]; then cat "$out/$route-config.log"; return 1; fi
  if ! chmod 644 "$conf/ubuntu.sources" "$conf/apt-mirrors.txt"; then
    printf 'APT_CONFIG_MODE=FAILED ROUTE=%s\n' "$route"; return 1
  fi
  if ! bash "${RUNNER_TEMP:?}/h17g-python-access.sh" "$state" "$route" > "$out/$route-access.log" 2>&1; then
    printf 'APT_UNPRIVILEGED_ACCESS=FAILED ROUTE=%s\n' "$route"
    cat "$out/$route-access.log"
    return 1
  fi
  cat "$out/$route-access.log"
  cp "$conf/ubuntu.sources" "$out/$route-ubuntu.sources"
  cp "$conf/apt-mirrors.txt" "$out/$route-apt-mirrors.txt"
  opts=(-o Dir::Etc::sourcelist=/dev/null -o "Dir::Etc::sourceparts=$conf" -o "Dir::State::lists=$lists" -o "Dir::Cache::archives=$archives" -o Dir::Cache::pkgcache= -o Dir::Cache::srcpkgcache= -o Acquire::Retries=0 -o Acquire::http::Timeout=8 -o Acquire::https::Timeout=8 -o APT::Update::Error-Mode=any -o Acquire::Check-Date=true -o Acquire::Check-Valid-Until=true -o Acquire::AllowInsecureRepositories=false -o APT::Get::AllowUnauthenticated=false)
  # Only the isolated index update gets one retry; package download and the
  # original Docs installer retain their own existing retry behavior.
  update_opts=("${opts[@]}" -o Acquire::Retries=1 -o Acquire::Languages=none -o Acquire::IndexTargets::deb::CNF::DefaultEnabled=false -o Acquire::IndexTargets::deb::Packages::DefaultEnabled=true)
  # Confirm the runner's CNF target is only command-not-found metadata before disabling it.
  apt-config "${update_opts[@]}" shell CNF_META 'Acquire::IndexTargets::deb::CNF::MetaKey' RETRIES 'Acquire::Retries' LANGUAGES 'Acquire::Languages' CNF_ENABLED 'Acquire::IndexTargets::deb::CNF::DefaultEnabled' PACKAGES_ENABLED 'Acquire::IndexTargets::deb::Packages::DefaultEnabled' > "$out/$route-apt-config.log" 2>&1 || return 1
  grep -Fq '$(COMPONENT)/cnf/Commands-$(NATIVE_ARCHITECTURE)' "$out/$route-apt-config.log" || { printf '%s\n' 'CNF_TARGET_UNCONFIRMED'; return 1; }
  grep -Fxq "RETRIES='1'" "$out/$route-apt-config.log" || return 1
  grep -Fxq "CNF_ENABLED='false'" "$out/$route-apt-config.log" || return 1
  grep -Fxq "PACKAGES_ENABLED='true'" "$out/$route-apt-config.log" || return 1
  # Languages is a list: apt-config shell reads only its scalar, which can be
  # empty even when list entries exist. Ask APT's target planner instead, using
  # the same options and sources as update, without consulting Release files.
  if ! timeout -k 2s 8s sudo apt-get "${update_opts[@]}" indextargets --no-release-info --format '$(CREATED_BY)|$(IDENTIFIER)|$(METAKEY)|$(LANGUAGE)' > "$out/$route-target-plan.log" 2> "$out/$route-target-plan.err"; then
    printf 'APT_TARGET_PLAN=FAILED ROUTE=%s\n' "$route"; return 1
  fi
  if ! awk -F '|' '
    NF != 4 || $4 != "$(LANGUAGE)" || tolower($1) ~ /translations/ || tolower($2) ~ /translations/ || tolower($3) ~ /(^|\/)i18n\/translation-/ { bad=1 }
    $1 == "Packages" && $2 == "Packages" && $3 ~ /^[^\/]+\/binary-amd64\/Packages$/ { packages=1 }
    END { exit (bad || !packages) }
  ' "$out/$route-target-plan.log"; then
    printf 'APT_TARGET_PLAN=UNEXPECTED ROUTE=%s\n' "$route"; return 1
  fi
  printf 'APT_TARGET_PLAN=PACKAGES_PRESENT_NO_TRANSLATIONS ROUTE=%s\n' "$route"
  timeout -k 2s 35s sudo apt-get "${update_opts[@]}" update > "$out/$route-update.log" 2>&1
  update_rc=$?
  printf 'SIGNED_UPDATE_RC=%s\n' "$update_rc"
  if grep -Eiq 'unsandboxed as root|couldn.t be accessed by user ._apt.|permission denied' "$out/$route-update.log"; then
    printf 'APT_UNPRIVILEGED_UPDATE=FAILED ROUTE=%s\n' "$route"; return 1
  fi
  if [[ $update_rc != 0 ]]; then printf '%s\n' 'PACKAGE_FETCH=NOT_ATTEMPTED'; return 1; fi
  # Require fresh signed InRelease indexes for each suite. Apt verified signatures and freshness.
  missing=0
  for suite in noble noble-updates noble-backports noble-security; do
    index=$(find "$lists" -maxdepth 1 -type f -name "*_dists_${suite}_InRelease" -print -quit)
    if [[ -z $index ]]; then
      printf 'MISSING_SIGNED_INDEX=%s\n' "$suite"; missing=1
    else
      sha256sum "$index" >> "$out/$route-indexes.sha256" || return 1
      printf 'SUITE=%s\n' "$suite" >> "$out/$route-index-freshness.txt"
      grep -E '^(Date|Valid-Until):' "$index" >> "$out/$route-index-freshness.txt" || return 1
    fi
  done
  if [[ $missing != 0 ]]; then printf '%s\n' 'PACKAGE_FETCH=NOT_ATTEMPTED'; return 1; fi
  # APT reports the actual downloaded target paths and trust state; compare each
  # to every configured amd64 Packages index advertised by the signed pockets.
  timeout -k 2s 10s sudo apt-get "${update_opts[@]}" indextargets --format '$(RELEASE)|$(COMPONENT)|$(ARCHITECTURE)|$(TRUSTED)|$(FILENAME)' 'Identifier: Packages' > "$out/$route-packages-targets.log" 2>&1 || return 1
  python3 "${RUNNER_TEMP:?}/h17g-python-check-indexes.py" "$conf/ubuntu.sources" "$lists" "$out/$route-packages-targets.log" > "$out/$route-packages-proof.log" 2>&1 || return 1
  (cd "$download" && timeout -k 2s 10s sudo apt-get "${opts[@]}" --print-uris download libnss3:amd64) > "$out/$route-uris.log" 2>&1 || return 1
  python3 "${RUNNER_TEMP:?}/h17g-python-check-download.py" parse "$out/$route-uris.log" "$conf/apt-mirrors.txt" > "$out/$route-package-record.log" 2>&1 || return 1
  (cd "$download" && timeout -k 2s 15s sudo apt-get "${opts[@]}" download libnss3:amd64) > "$out/$route-download.log" 2>&1
  download_rc=$?
  printf 'PACKAGE_FETCH_RC=%s\n' "$download_rc"
  if grep -Eiq 'unsandboxed as root|couldn.t be accessed by user ._apt.|permission denied' "$out/$route-download.log"; then
    printf 'APT_UNPRIVILEGED_DOWNLOAD=FAILED ROUTE=%s\n' "$route"; return 1
  fi
  if [[ $download_rc != 0 ]]; then return 1; fi
  files=("$download"/*.deb)
  if [[ ! -f ${files[0]} || ${#files[@]} != 1 ]]; then printf '%s\n' 'PACKAGE_FETCH_INVALID=1'; return 1; fi
  sudo python3 "${RUNNER_TEMP:?}/h17g-python-check-download.py" verify "$out/$route-uris.log" "$conf/apt-mirrors.txt" "${files[0]}" > "$out/$route-package-proof.log" 2>&1 || return 1
  package=$(sudo dpkg-deb -f "${files[0]}" Package) || return 1
  version=$(sudo dpkg-deb -f "${files[0]}" Version) || return 1
  arch=$(sudo dpkg-deb -f "${files[0]}" Architecture) || return 1
  printf 'Package: %s\nVersion: %s\nArchitecture: %s\n' "$package" "$version" "$arch" > "$out/$route-package-metadata.txt"
  [[ $package == libnss3 && -n $version && $arch == amd64 ]] || return 1
  printf 'ROUTE_QUALIFIED=%s\n' "$route"
  return 0
}
probe_route unchanged > "$out/unchanged-phase.log" 2>&1 &
unchanged_pid=$!
probe_route alternate > "$out/alternate-phase.log" 2>&1 &
alternate_pid=$!
unchanged_ok=0
alternate_ok=0
wait "$unchanged_pid" && unchanged_ok=1
wait "$alternate_pid" && alternate_ok=1
cat "$out/unchanged-phase.log" "$out/alternate-phase.log"
printf 'UNCHANGED_QUALIFIED=%s ALTERNATE_QUALIFIED=%s\n' "$unchanged_ok" "$alternate_ok"
selected=none
if [[ $unchanged_ok == 1 ]]; then selected=unchanged; elif [[ $alternate_ok == 1 ]]; then selected=alternate; fi
printf 'SELECTED_ROUTE=%s\n' "$selected"
if [[ $selected == none ]]; then
  printf '%s\n' 'INSTALL=NOT_ATTEMPTED BROWSERS=NOT_ATTEMPTED (fresh signed index and actual package fetch required)'
  exit 1
fi
phase=setup
# Initialization is before checkout. Reserve two minutes of the 15-minute job for runner/bootstrap and evidence upload.
# The 700s gate reserves 625s installer including kill, 46s smokes and >=29s evidence.
pin_epoch=$(cat "$out/pin-epoch.txt")
remaining=$((pin_epoch + 780 - $(date +%s)))
printf 'REMAINING_EVIDENCE_BUDGET_SECONDS=%s\n' "$remaining"
if (( remaining < 700 )); then
  printf '%s\n' 'INSTALL=NOT_ATTEMPTED BROWSERS=NOT_ATTEMPTED (insufficient evidence budget)'
  exit 1
fi
# Change only the source URI(s); the transform has preserved all original suites, components and keyring.
if [[ $selected == unchanged ]]; then
  sudo sed -i 's|http://azure\.archive\.ubuntu\.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' /etc/apt/apt-mirrors.txt /etc/apt/sources.list.d/ubuntu.sources || exit 3
else
  sudo cp "$state/alternate/config/ubuntu.sources" /etc/apt/sources.list.d/ubuntu.sources || exit 3
fi
sudo apt-get clean || exit 3
sudo find /var/lib/apt/lists -mindepth 1 -maxdepth 1 ! -name lock -exec rm -rf -- {} + || exit 3
sudo mkdir -p /var/lib/apt/lists/partial /var/cache/apt/archives/partial || exit 3
sudo chown _apt:root /var/lib/apt/lists/partial /var/cache/apt/archives/partial || exit 3
sudo -u _apt test -w /var/lib/apt/lists/partial || exit 3
sudo -u _apt test -w /var/cache/apt/archives/partial || exit 3
printf '%s\n' '=== EXACT PYTHON DOCS INSTALLER (source 600s timeout unchanged) ==='
start=$(date +%s)
timeout -k 5s 620s uv run --quiet --script scripts/site_check.py --install --with-deps > "$out/installer.log" 2>&1
install_rc=$?
printf 'INSTALL_RC=%s INSTALL_SECONDS=%s\n' "$install_rc" "$(($(date +%s)-start))"
if grep -Eiq 'unsandboxed as root|couldn.t be accessed by user ._apt.|permission denied' "$out/installer.log"; then
  printf '%s\n' 'APT_INSTALL_UNPRIVILEGED=FAILED CHROMIUM=NOT_ATTEMPTED WEBKIT=NOT_ATTEMPTED'
  exit 1
fi
if [[ $install_rc != 0 ]]; then printf '%s\n' 'CHROMIUM=NOT_ATTEMPTED WEBKIT=NOT_ATTEMPTED'; exit "$install_rc"; fi
phase=browser-smoke
smoke_failed=0
for browser in chromium webkit; do
  timeout -k 3s 20s uv run --quiet --script "${RUNNER_TEMP:?}/h17g-python-smoke.py" "$browser" > "$out/$browser.log" 2>&1
  browser_rc=$?
  printf '%s_RC=%s\n' "$browser" "$browser_rc"
  [[ $browser_rc == 0 ]] || smoke_failed=1
done
exit "$smoke_failed"
