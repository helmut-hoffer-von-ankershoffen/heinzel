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

- Use one supervised local server per registration on macOS and Linux. A standalone bridge runs under a pinned
  absolute interpreter with `-I -S` and connects to the owned Unix socket. It never starts the server or boots the
  application. The broker verifies endpoint ownership, OS peer credentials and the registration generation before
  admission. Readiness requires authenticated MCP initialization, discovery and identity verification.
- Authorize lifecycle effects through a separate host capability bound to the reviewed plan and action. Trusted
  native review and explicit local CLI review use the same service. Browser and device sessions can prepare a
  plan and see authorized status. The broker admits private connections only after host review of the exact
  pending instance. Owner UID is one prerequisite and does not confer a grant or assert human approval.
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

Direct HTTP is separate follow-up work building on the proposed bearer scope in
[PR #655](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/655). Claude Desktop and Gemini CLI
remain follow-up client integrations with their own acceptance matrix.
