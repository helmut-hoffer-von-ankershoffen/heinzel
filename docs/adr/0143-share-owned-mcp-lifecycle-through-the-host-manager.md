---
status: proposed
date: 2026-11-11
implementation: planned
decision-makers: maintainer, reviewer
related: 0019, 0020, 0078, 0126, 0133
amends: 0142
amends-0142: A temporary private probe credential for a managed generation
---

# 0143: Share owned MCP lifecycle through the host manager

## Context and Problem Statement

The existing MCP serve commands start a foreground listener or inspect an endpoint. A project stdio entry is
started by the client. Neither supplies one owned server that independent CLI and authenticated web requests can
start, check and stop. The existing host manager and maintenance authority can control a profile-bound job without
introducing another generic daemon or making CLI control depend on an open app window.

## Considered Options

- **One transient job in the existing user service manager** (chosen)
- **A child owned only by the desktop window**: useful for an isolated launch, but a separate CLI process has no
  safe authority to adopt that child.
- **Extend the supervised web control socket**: it exists only for the optional persistent web service and would
  make this CLI depend on that service, excluding ordinary foreground desktop use.

## Decision Outcome

Chosen option: **a transient profile-bound job under the existing host manager**, because CLI and web can verify
the same ownership independently without a remembered PID, public control endpoint or new login service.

- Shared `system mcp start/check/stop` operations are exposed only to CLI and authenticated web. Start and Stop
  require human confirmation. The preview flag defaults off and gates new starts and credentials; checking,
  stopping and maintenance recovery remain available for a previously owned generation after disabling it.
- The first implementation targets macOS user launchd. Its exact private plist lives outside LaunchAgents and
  has no login persistence or automatic restart. The fixed verified CLI runs through `env -i` in an owned
  generation directory with private site/user/project configuration, plugins and update checks off, and a literal
  loopback endpoint. No caller-supplied executable, URL, port, environment or credential is accepted.
- A prepared receipt binds installation, profile, generation and exact unit before bootstrap. Same-generation
  Start reauthenticates an owned healthy job without minting a credential or bootstrapping again. Unknown state,
  changed definitions or a foreign registration are never replaced. Check proves actual authenticated MCP
  initialization and tool discovery with the same manager identity. Stop requires positive manager absence and
  endpoint refusal; arbitrary descendant cleanup remains unverified. No numeric PID or process group is signalled.
- The server token registry still keeps only digests. This managed mode additionally keeps one short-lived
  read-only probe bearer in an owner-only generation file for independent CLI/web checks, outside unit contents,
  maintenance backups, project files and responses. Its value is passed to the check on stdin and never logged.
- The existing SDK-wide and profile locks and provider-free maintenance inventory cover every matching owned
  registration before SDK removal. Active or uncertain MCP generations block SDK mutation until the ordinary
  Stop proves both manager absence and endpoint closure; offline recovery does not invent an MCP probe. No work
  starts while installation mutation is pending. An unsupported host or SDK stays unavailable.

### Consequences

The app and CLI observe one owned server and can stop it without a window or another control service. The first
slice still needs installed supervisor and packaged qualification before enabling it. Client-started stdio and
managed HTTP remain distinct; matching client configuration, real clients, weather and Linux are later work.

### Confirmation

Review only until the focused CLI/web, ownership, isolation, maintenance and rendered consent tests are in place.

## More Information

This builds on [the daemon head](0020-daemon-head.md), [host integration maintenance](0133-owned-host-integrations-join-maintenance.md)
and [per-agent bearer scopes](0142-mcp-over-http-takes-per-agent-bearer-tokens-with-read-and-write-scopes.md).
