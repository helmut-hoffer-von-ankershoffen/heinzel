# Electron desktop shell

This development shell starts the installed `hz` CLI as an owned foreground child, verifies its authenticated
lifecycle and opens the actual product UI in a sandboxed Electron window. It stops that child when the app quits.
Run `uvx heinzel@latest` once to install the standard app and its default plugins. After the workspace opens,
stop that foreground session with Ctrl-C. Then, from this directory:

```sh
pnpm install
pnpm start
pnpm test
```

Run `pnpm test:native` to launch the real Electron 45 app against a temporary loopback lifecycle endpoint. On Linux,
run it in a graphical session or under Xvfb. The packaged-app variant is restricted to explicitly disposable CI
because its profile isolation is not yet verified; local native tests use the isolated development bootstrap.

To exercise the installed CLI rather than the fake lifecycle server, first run the repository's `make setup` and
`pnpm install` here, then run `HZ_REAL_CLI="$(command -v hz)" pnpm test:real-backend`. This opt-in smoke uses a
temporary Electron profile and temporary CLI home, configuration, data, state and cache directories. It verifies
authenticated lifecycle, reload, and owned-child shutdown on a fresh loopback port, and reports the installed CLI
version. It also verifies the rendered Today page, authenticated browser API access and reload, with renderer Node
access disabled. It does not test a packaged, signed or notarized app.

`HZ_REAL_CLI="$(command -v hz)" pnpm test:real-backend-crash` uses the same isolation and kills only the verified
test-owned child after the UI loads. It checks that the shell reports the unexpected stop and that the child and
its temporary listener are gone. If launch or shutdown leaves the controller's state uncertain, the harness retains
and reports the temporary profile for inspection. It does not simulate installation, update or background operation.

The web head adopts its initial bearer fragment into the window's session storage, as in the browser. The native
preload exposes only lifecycle status, checks the owning main frame and exact loopback origin, and exposes no token
or generic native operation. Main-document navigation and redirects stay on that owned origin; new windows remain
blocked. A public `http(s)` link (target=_blank, `window.open()`, middle-click or a same-window link) or a `mailto:`
address (subject, body, cc and bcc only) opens in the default browser through `shell.openExternal`, at most one per
2 s per window. Local services (loopback, private, link-local and tailnet IPs, `.local`-style and single-label names),
credentials in the URL and every other scheme are denied. Notification and audio-only microphone requests require a separate native confirmation for the current
document; other permission requests remain denied. That confirmation does not establish an OS grant or prove
delivery or audio capture. These restrictions are development limitations, not a completed native permission lifecycle.

`pnpm test:navigation` separately exercises real Electron page navigation and redirects, including an initial
fragment-bearing redirect, against isolated temporary servers. Cross-origin destinations receive no request.

## Installing on a Mac: `hz app install`

`hz app install` downloads the macOS arm64 app from the official release, checks its sha256 against the release
manifest and its build provenance, and installs `Heinzelmaennchen.app` into `/Applications`, where Finder,
Launchpad and Spotlight expect apps. It uses `/Applications` when this user can write there without a password
(administrator accounts can); it never runs `sudo` or asks for one. Otherwise it installs into `~/Applications`
and says why; once the account is an administrator, `hz app install` moves it.
`hz app install` moves an earlier `~/Applications` install into `/Applications` in one rename while the app is
closed, so there is only ever one copy. Automatic updates keep the app where it is, `hz app uninstall` removes it
from whichever folder holds it, and both commands print the real path. Heinzel Notifier stays in `~/Applications`:
it is a per-user helper that Heinzel launches itself, not an app people open. The app is not signed
with an Apple Developer ID and not notarized by Apple. Every macOS build carries an ad-hoc code seal
(`codesign --sign -`, no identity), because Apple silicon refuses to run arm64 code without a valid signature.

What macOS does with it (macOS 15 to 27 on Apple silicon):

- Valid seal, not quarantined: a double-click opens it. This is the default after `hz app install`, since Heinzel
  downloaded and verified the bytes itself and macOS never quarantined them.
- Valid seal, quarantined (`hz app install --keep-quarantine`, or a copy downloaded with a browser): macOS says it
  cannot verify the app. Choose Done, open System Settings, Privacy & Security, and click Open Anyway next to
  Heinzelmaennchen. Right-click, Open no longer skips this step on current macOS.
- Broken seal and quarantined: macOS says the app "is damaged and can't be opened" and offers only Move to Trash.
  Releases up to 0.9.26 shipped without a bundle seal and the installer quarantined them, which produced this.

`hz app doctor` reports the signature, quarantine and Gatekeeper's `spctl --assess` verdict in plain words without
launching anything (an unnotarized app is always "rejected" there; that is expected). `hz app doctor --fix`, with the
app closed, reseals a broken installed bundle ad hoc and removes only the quarantine mark Heinzel
itself added. Installs and automatic updates check the seal of the verified download before it reaches
the Applications folder, and reseal it locally if a release lacks one.

## Local unsigned packaging

Node.js 24.19.0 and pnpm 12.15.0 are pinned by repository CI, which (`CI=true`) requires exactly that Node.js. Local
builds accept Node.js 24 or newer and print a notice when it differs from the pin. Set `HZ_DESKTOP_STRICT_NODE=1`
to require exactly 24.19.0 locally too. From this directory, run
`pnpm install --node-linker=hoisted --frozen-lockfile`, then `pnpm build` and `pnpm verify:package`. Packaging takes
the app version from the core `pyproject.toml` without editing either project's version files. The build targets the
current host by default; CI builds Linux x64 on Ubuntu and macOS arm64 on macOS. To select a target explicitly, use
`pnpm build -- linux x64` or `pnpm build -- darwin arm64`. The app is written under `desktop/dist/` with files in
ASAR; Linux emits an unpacked app directory and macOS emits an `.app` bundle. CI checks that both contain the expected
ASAR payload.

These are unsigned (ad-hoc sealed on macOS) local/CI verification artifacts. They are not notarized, published, or suitable for a trusted
production install. Gatekeeper may block macOS launches, and Linux desktop integration/package-manager installation
is not included. There is no auto-update, login item, background service, OS permission request, installer/uninstaller,
or Python runtime provisioning. The shell invokes `hz ui serve --port 0 --no-browser` without a shell, accepts only its
single verified loopback URL line, passes its fragment only to the owned web UI, and stops only its owned child on
Quit or on SIGTERM, SIGINT or SIGHUP (the owned service gets SIGINT first and SIGKILL only after a timeout). A signal-initiated
shutdown exits non-zero when the child exits uncleanly or does not stop; it never waits for a GUI error dialog.
This does not install the `hz` CLI or enable background operation; the CLI must already be installed and the
user must launch the app while it is on `PATH`.
