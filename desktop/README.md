# Electron window slice

This proof of concept starts the installed `hz` CLI as an owned foreground child, opens a sandboxed Electron window
for its authenticated local UI, and stops that child when the app quits. Install Heinzelmännchen so `hz` is on
Electron's `PATH` before launching the desktop app:

```sh
pnpm install
pnpm start
pnpm test
```

Run `pnpm test:native` to launch the real Electron 45 app against a temporary loopback lifecycle endpoint. On Linux,
run it in a graphical session or under Xvfb.

The CLI runs as `hz ui --port 0 --no-browser`; Electron accepts only its single verified loopback URL line on
stdout. The bearer token stays in main-process memory and is never written to disk or logged. Electron invokes
`hz` directly without a shell, owns only that child process, requests graceful termination on Quit, and escalates
after a bounded wait. This proof of concept does not enable background operation, install or update a runtime,
request operating system permissions, or provide signed production catalogs or installers.
