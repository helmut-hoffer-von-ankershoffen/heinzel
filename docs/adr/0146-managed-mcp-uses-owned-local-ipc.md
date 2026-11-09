---
status: proposed
date: 2026-11-08
implementation: partial
related: 0019, 0062, 0068, 0133, 0138
---

# 0146: Managed MCP uses owned local IPC

## Context and Problem Statement

Vendor clients need to call reviewed Heinzel operations through a connection the owner can install, inspect, start,
stop and remove. Start must verify MCP readiness while every vendor client is closed. Existing MCP exposure,
Claude settings discovery and human approval have separate contracts.

## Considered Options

- Supervise one server per registration and admit clients over owned local IPC.
- Start a server on demand from each vendor's stdio command. Readiness would depend on the vendor connecting.
- Register a loopback HTTP server. Direct HTTP needs its own audience and authorization review.

## Decision Outcome

- Use one supervised local server per registration on macOS and Linux. Vendor integration requires a standalone
  bridge with a pinned absolute interpreter and `-I -S`, plus a trusted bootstrap that delivers a private broker
  channel. Vendor launches that can only resolve the named socket remain unadmitted. The bridge never starts
  the server or boots the application. Readiness requires authenticated MCP initialization, discovery and identity
  verification.
  The isolated activation primitive checks a real owner-verified Unix socket pair and issues each connection
  through a separate retained descriptor pair. Scope exit fences leases and shuts down the owned descriptors.
  At trusted single-thread startup, a forked Unix helper can bind a fresh short generation path relative to an
  inherited directory descriptor. Creation-time permissions and a private listener-descriptor handoff keep the
  caller's cwd and umask unchanged. Stop closes the listener and retains its generation directory and socket
  node. The adapter performs no pathname chmod, unlink or rename, including after failure. The named listener
  provides transport probes only. The foreground supervisor can also fork trusted bridge code at isolated
  startup with an inherited private descriptor. The bridge exchanges exact versioned bytes bound to the
  supervisor's fresh in-memory incarnation and one channel. It forwards client bytes only after the broker
  publishes the reviewed lease and acknowledges admission. This channel is separate from the named listener.
  The foreground bridge provides byte transport. MCP readiness and vendor bootstrap still require integration.
  A bounded exchange on the already admitted private bridge verifies MCP initialization, returns the exact
  registration and generation, and answers one empty tool list. Its replies share the grant revocation fence.
  The exchange provides no invocation path. A separately scoped host readiness lease and runtime attestation
  are required before Start can report MCP readiness.
  Unsupported platforms and fixed caller-selected socket paths refuse activation.
- Authorize lifecycle effects through a separate host capability bound to the reviewed plan and action. Trusted
  native review and explicit local CLI review use the same service. Browser and device sessions can prepare a
  plan and see authorized status. The broker admits private connections only after host review of the exact
  pending instance. Owner UID is one prerequisite and does not confer a grant or assert human approval.
  The local terminal reviewer runs in the supervisor's already-trusted, isolated CLI process. It uses the
  same source bytes it verifies against the digest embedded in the trusted SDK. The supervisor allocates the
  control descriptor only after that check and keeps the descriptor within the trusted process.
  `hz system mcp review PENDING_ID` accepts only the opaque locator and stays outside tool discovery. Exit status
  0 means the broker accepted the exact terminal review, 1 means review was unavailable or declined, and 2 means
  invalid syntax. The active host runtime supplies and retains the supervisor in process. Standalone invocation
  without that handle refuses before project discovery. Native activation must still supply the handle.
  `hz system mcp start|check|stop REGISTRATION_ID` also routes before discovery when a trusted installed host
  supplies the foreground registration. The standard installed CLI can also load a committed dormant receipt
  after verifying its installed launcher and SDK continuity and checking for a foreground controlling terminal.
  `hz system mcp register REGISTRATION_ID` records an explicitly reviewed transport-only registration. Register
  never starts a worker or writes vendor configuration. `recover` completes a verified interrupted registration
  with a fresh review. The locator alone cannot supply an installed registration. Start owns a direct child
  with an inherited control channel. Check reports the manager observation, owned worker and transport separately
  from MCP readiness. Stop closes the supervisor and proves the exact child's exit before returning success.
  The standalone Start session retains its private channel for separately reviewed `check` and `stop` actions.
  These foreground Start and Check observations report unverified MCP readiness. A separate CLI cannot stop
  a worker owned by another foreground session.
- Restrict discovery and dispatch to reviewed operations and exact target grants. Give authorized personal and
  contract Heinzels stable individual tools and CLI equivalents. Resolve both heads to the same durable retry
  identity for the selected grant. Retain exact result bytes and record uncertain effects without replaying them.
- Fence admission before stopping owned work and verify exit through the supervisor. Keep registered generations
  in the SDK maintenance inventory even with the feature disabled. Require writer exclusion for automatic client
  config changes. Where that proof is unavailable, review a manual change and verify native effective config.

### Consequences

The owner can check readiness independently and stop one registration while others remain active. The initial
clients require a pinned bridge and a reviewed config adapter for each supported scope.

### Confirmation

The first slice checks the owner on real Unix stream sockets and refuses mismatched owners and invalid sockets.
The feature remains unavailable until admission and revocation, recovery, CLI and UI parity, and the installed
Claude Code, Codex and Cursor journeys have been verified. Real Claude weather and personal Heinzel calls require
separate evidence.

## More Information

The installed activation adapter takes an SDK and profile selected by the authenticated native runtime.
On macOS it obtains the per-user temporary directory through `confstr` with `_CS_DARWIN_USER_TEMP_DIR`.
On Linux it selects `/run/user/<uid>`. It verifies physical ancestry with no-follow directory opens,
selects a fresh directory by parent descriptor, and retains its descriptor for creation-only operations.
The `mkdir` and `open` steps cannot prove creation provenance against another process under the same UID.
The opened directory is locator storage only. Environment overrides and project paths supply no authority. The adapter refuses named
activation when the OS root is unavailable or when the final root exceeds 86
filesystem-encoded bytes. The adapter reserves the remaining 17 bytes of the 103-byte socket pathname limit.
Before publishing the supervisor, the adapter commits a single observation of locator storage and the retained
listener under the SDK maintenance authority outside the SDK. A failed commit retires the listener and leaves
any uncertain receipt for inspection. Activation never adopts a prior receipt or grants cleanup rights from one.
The foreground adapter holds the SDK lock until shutdown. An observed change to a retained anchor or current
receipt retires the supervisor. Native manager integration will register the stop and recovery hooks before
allowing concurrent managed activation and maintenance. Retired and failed nodes remain on disk for reviewed cleanup.

The foreground registration plan derives the native unit name from the selected SDK, profile and registration.
The installed CLI uses `HostServiceManager` and the OS user's definition namespace for read-only collision
detection. Intent and commit records bind the dormant registration to SDK continuity and the retained SDK
maintenance lock identity. Standard update, refresh and uninstall check this inventory under the SDK lock.
An active foreground worker blocks maintenance. An incomplete registration blocks SDK changes until explicit
recovery, and a changed SDK never adopts a prior activation. Uninstall retains dormant receipts and checks the
native namespace in standalone recovery. Durable lifecycle action receipts, native unit publication and
manager-owned bootstrap and recovery, and authenticated native UI routing remain prerequisites for persistent
lifecycle controls.

Standard SDK mutation and rollback enroll the selected ordinary profile before writing its markers or changing
package files. The SDK inventory retains the identities of the profile and its daemon and snapshot directories.
MCP activation checks pending markers across the enrolled profiles, including after a writer exits before
changing SDK bytes. Recovery still uses the ordinary profile's existing authority. An unknown or replaced
inventory entry keeps activation fenced. MCP requires a complete assessment from a trusted host authority.
The authority can report complete, incomplete or recovery required. Observed pending work still fences a complete
assessment. The ordinary installer, update and recovery paths enroll their selected profiles and leave completeness
to the host. A missing host authority keeps the installed CLI fenced. Local receipts, directory creation and
caller configuration confer no completeness. Positive foreground tests supply a fixture authority explicitly.

Production acceptance requires protected provisioning and a broker that mediates all SDK writers, including
refresh, update, rollback and uninstall. A legacy migration must establish the complete inventory and recover
pending work under that same authority before activation. Ordinary install and recovery remain available during
this work. They cannot certify historical profiles or replace the broker.

Direct HTTP is separate follow-up work building on the proposed bearer scope in
[PR #655](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/655). Claude Desktop and Gemini CLI
remain follow-up client integrations with their own acceptance matrix.
