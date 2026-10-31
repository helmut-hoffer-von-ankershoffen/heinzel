---
status: proposed
date: 2026-10-26
decision-makers: maintainer
related: 0006, 0031, 0046, 0083
amended-by: 0093
implemented-by: 188
---

# 0085: Web updates verify the reloaded frontend

## Context and Problem Statement

The existing updater retains the uv tool receipt and resolves core and plugins together. Its result describes the
installation on disk, while the web process and browser still execute old code. A reload can discard an unsent chat
draft, and deleting unrelated caches or overriding deliberate installation pins would change the person's intent.

## Considered Options

- **Compose the existing updater with verified restart and frontend identity** (chosen).
- **Reload after the updater returns**: installed code does not prove the running backend or loaded browser assets.
- **Add another updater or force a release source**: duplicates installation policy and overrides deliberate pins.

## Decision Outcome

Chosen option: **compose the existing updater with verified restart and frontend identity**, because each stage
then proves its own result without creating another installation policy.

- A compact footer control reads cached release availability only. It respects `update_check`, makes no new
  background network check and shows unsupported development/local/deliberately pinned installations honestly.
- A deliberate click invokes the existing `system.update` with no source or channel override. Core/plugin resolution
  and failures remain that operation's responsibility. There is no automatic replay after an uncertain mutation.
- Before mutation, the composer stores its unsent text/model in this tab's session storage. Active chat turns,
  terminals and modal editors block the action. Storage failure blocks updating before user work can be lost.
- The existing lifecycle controller requires a different server instance and the exact resulting code version.
  An authenticated frontend descriptor identifies installed HTML entrypoints and asset SHA256 hashes. The browser
  checks those files, clears only `hz-shell-` caches, asks only its exact root worker to update and reloads.
- A per-tab receipt survives that reload. Completion requires the replacement instance/version, the DOM's loaded
  script/styles entrypoints and freshly checked asset hashes to agree. The receipt contains no credential. A failed
  stage remains visible and offers verification rather than automatically repeating installation.
- CLI and MCP retain the existing head-agnostic update operation. Frontend identity/cache/reload work belongs to
  the web head. This does not implement desktop packaging or a second update coordinator.

### Consequences

- Good: neither an installed version, retiring HTTP200 nor stale browser shell can claim update completion.
- Good: deliberate pins, unrelated origin caches and unsent chat text survive the flow.
- Bad: update waits until transient work is safe; a tool/development installation must use its existing update path.
- Bad: a network/permission/storage failure can require manual inspection after installation has already changed.

### Confirmation

`tests/integration/web_update_test.py` checks cached policy, pins, authentication and frontend descriptors.
`web/src/updates/controller.test.ts` and `web/src/layout/UpdateStatus.test.tsx` check stage ordering, uncertain outcomes,
reload verification and the explicit click. Composer draft tests check restoration and refusal on storage failure.
An actual mounted rebuilt-server/browser proof and independent security review remain release gates.
