# Electron desktop shell

This proof of concept starts the installed `hz` CLI as an owned foreground child, opens a sandboxed Electron window
for its authenticated local UI, and stops that child when the app quits. Install Heinzelmännchen from PyPI so `hz` is
on `PATH` before launching the desktop app:

```sh
uv tool install heinzel
pnpm install
pnpm start
pnpm test
```

Run `pnpm test:native` to launch the real Electron 45 app against a temporary loopback lifecycle endpoint. On Linux,
run it in a graphical session or under Xvfb.

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
single verified loopback URL line, keeps the bearer token in main-process memory, and stops only its owned child on
Quit. This does not install the `hz` CLI or enable background operation; the CLI must already be installed and the
user must launch the app while it is on `PATH`.
