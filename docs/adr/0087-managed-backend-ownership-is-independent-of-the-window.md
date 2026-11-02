---
status: proposed
date: 2026-10-27
implementation: built
decision-makers: maintainer, architect and security reviewer
related: 0078, 0083, 0084
implemented-by: 221
---

# 0087: Managed backend ownership is independent of the native window

## Context and Problem Statement

The shared lifecycle authority records immutable deployments and fenced transitions (ADR0078), while the initial
Electron window connects to an already running server. A native host needs a small process owner before it can
install, update or recover a managed runtime. Importing the complete experimental native application would mix
unverified bootstrap, permissions and update policy with this boundary.

## Considered Options

- **A separate managed-backend library beneath `desktop/managed`** (chosen).
- Start whatever `hz` or HTTP listener is available: cannot establish executable, profile or generation ownership.
- Put another updater/scheduler in the window: conflicts with the existing lifecycle authority and daemon.
- Import the entire native experiment: prevents independent review of the process boundary.

## Decision Outcome

Chosen option: **a separate managed-backend library**, because process ownership can be verified before adding a
window, installer or native permission.

- A typed Node parent accepts an explicitly selected private profile and verified immutable runtime inventory.
  It starts only the selected interpreter and hash-bound worker, through sealed standard-library broker code.
  No renderer, environment flag, caller URL or PATH lookup chooses an interpreter or grants lifecycle authority.
- The broker owns the loopback listener, profile controller descriptor and writer lease. Its parent pipe is the
  lifetime authority; EOF or termination joins only its owned worker. It retains the listener over bounded worker
  recovery and authenticates actual version, instance and UI bytes before reporting ready.
- Inventory reads do not write activation pointers or journals. The existing Python authority remains the only
  transition writer. Preparing, switching, removing or updating runtimes is outside this library.
- The existing pinned Node runtime and pnpm execute its private verification package. TypeScript5.9.3, Node24
  types and Biome2.6.2 are development-only tools; no Electron, native capture or application dependency is added.
- Tests use temporary owned state and offline workers. CI runs the same verification target. A listener, PID,
  process start or local hash does not establish publisher trust, a running UI or successful OS integration.

### Consequences

- A later native caller can compose process ownership with exact-plan recovery without importing unrelated UI.
- The library adds a maintained Node/Python protocol and private filesystem checks, with failure-injection duties.
- Authenticated bootstrap/catalog inputs, compatible app/core/plugin updates, migration rollback, complete
  uninstall, background consent, native notification/audio outcomes and signed platform installation remain open.

### Confirmation

`make managed-backend-check` checks strict native types, sealed helper identity, profile/inventory refusal and actual
owned start/stop behavior. `tests/integration/managed_backend_broker_test.py` checks real descriptor ownership,
foreign listener/profile refusal, parent death, crash recovery and cleanup on macOS and Linux. This does not ship
an Electron application or establish signing, Linux application installation or OS permission behavior.
