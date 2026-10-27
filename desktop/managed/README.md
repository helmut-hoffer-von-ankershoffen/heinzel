# Managed backend foundation

This private verification library owns a backend process for an explicitly selected private profile. It starts only
a hash-verified runtime, keeps a broker-owned loopback listener and returns an authenticated readiness endpoint.
It is independent of the initial Electron window and is not an installer or shipped desktop application.

From the repository root:

```sh
make managed-backend-setup
make managed-backend-check
```

`RuntimeInventory` reads complete private deployment/environment inventories without changing activation or journal
state. `OwnedBackend.start()` takes an optional exact activation fence and refuses stale generations, foreign
listeners, changed bytes and an already starting controller. `stop()` closes the parent pipe and joins the owned
broker/worker; forceful or uncertain cleanup cannot report a graceful stop. A broker retains its listener over
bounded worker recovery. Parent death revokes its lease. It never signals a remembered, already-reaped PID.

The private endpoint includes a fresh bearer token in its fragment. The token stays in the host and is never a
log message or installer credential. The broker checks the actual authenticated lifecycle instance/version and
UI response digest before reporting ready. Process creation, a port, a PID or a local hash alone is insufficient.
These checks establish local ownership and byte identity; publisher authenticity belongs to later trusted setup.

The shared Python lifecycle authority remains the only transition writer. No activation, update, uninstall,
background scheduler, login item, notification or audio permission is added here. Fixtures use temporary profiles
and an offline HTTP worker. Native source packaging, signed bootstrap/catalog inputs, real app/core/plugin updates,
full uninstall and macOS/Linux application lifecycle remain separate release gates.
