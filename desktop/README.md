# Electron desktop shell

This development shell starts the installed `hz` CLI as an owned foreground child, verifies its authenticated
lifecycle and opens the actual product UI in a sandboxed Electron window. It stops that child when the app quits.
Install Heinzelmännchen from PyPI so `hz` is
on `PATH` before launching the desktop app:

```sh
uv tool install heinzel
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
its temporary listener are gone. It does not simulate installation, update or background operation.

The web head adopts its initial bearer fragment into the window's session storage, as in the browser. The native
preload exposes only lifecycle status, checks the owning main frame and exact loopback origin, and exposes no token
or generic native operation. Main-document navigation and redirects stay on that owned origin; new windows remain
blocked. Notification and audio-only microphone requests require a separate native confirmation for the current
document; other permission requests remain denied. That confirmation does not establish an OS grant or prove
delivery or audio capture. These restrictions are development limitations, not a completed native permission lifecycle.

`pnpm test:navigation` separately exercises real Electron page navigation and redirects, including an initial
fragment-bearing redirect, against isolated temporary servers. Cross-origin destinations receive no request.

## Local unsigned packaging

Node.js 24.19.0 and pnpm 12.15.0 are pinned by repository CI. From this directory, run
`pnpm install --node-linker=hoisted --frozen-lockfile`, then `pnpm build` and `pnpm verify:package`. Packaging takes
the app version from the core `pyproject.toml` without editing either project's version files. The build targets the
current host by default; CI builds Linux x64 on Ubuntu and macOS arm64 on macOS. To select a target explicitly, use
`pnpm build -- linux x64` or `pnpm build -- darwin arm64`. The app is written under `desktop/dist/` with files in
ASAR; Linux emits an unpacked app directory and macOS emits an `.app` bundle. CI checks that both contain the expected
ASAR payload.

These are unsigned local/CI verification artifacts. They are not notarized, published, or suitable for a trusted
production install. Gatekeeper may block macOS launches, and Linux desktop integration/package-manager installation
is not included. There is no auto-update, login item, background service, OS permission request, installer/uninstaller,
or Python runtime provisioning. The shell invokes `hz ui --port 0 --no-browser` without a shell, accepts only its
single verified loopback URL line, passes its fragment only to the owned web UI, and stops only its owned child on
Quit. This does not install the `hz` CLI or enable background operation; the CLI must already be installed and the
user must launch the app while it is on `PATH`.
