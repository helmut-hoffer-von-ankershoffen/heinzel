# Electron window slice

This package opens a sandboxed Electron window and reads `/lifecycle` from an already running Heinzelmännchen UI
server. Start the local server yourself, then launch Electron with its displayed URL:

```sh
pnpm install
pnpm start -- --hz-url='http://127.0.0.1:8000/#token=…'
pnpm test
```

Run `pnpm test:native` to launch the real Electron 45 app against a temporary loopback lifecycle endpoint. On Linux,
run it in a graphical session or under Xvfb.

The token is accepted only in the local URL fragment and is used by the main process as a Bearer credential. The
renderer receives one fixed lifecycle read through a context-isolated preload. This package does not launch or
enable a daemon, install or update a runtime, request operating system permissions, or provide signed production
catalogs or installers.
