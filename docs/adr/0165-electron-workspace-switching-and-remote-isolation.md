---
status: accepted
date: 2026-11-15
implementation: partial
decision-makers: maintainer
amends: 0078, 0135
amends-0078: permit explicit remote workspaces without local native authority
amends-0135: retain the owned service while another workspace is selected
related: 0147
---

# 0165: Electron workspace switching and remote isolation

## Context and Problem Statement

Electron owns a local service and previously assumed every workspace was that service. The Swift Mac shell also
offers private HTTPS workspaces, recent hosts and connection recovery. Electron needs the same choice without
giving a remote page access to local credentials or service control.

## Considered Options

- **A trusted picker and isolated remote sessions** (chosen). Keep workspace selection outside the pages it opens.
- **Reuse the local window and native bridge**. This exposes host capabilities to remote workspace content.
- **Keep Electron local-only**. This leaves workspace switching available only in the Swift shell.

## Decision Outcome

A bundled, sandboxed connection screen offers the local workspace and private HTTPS sign-in links. It follows
the Swift shell's paper palette, serif headings, rounded panels, Connection shortcut and recovery language.
Only the bundled screen's exact main frame can select a workspace or read recent origins. A remote page cannot
open this IPC surface, read the directory, retrieve another workspace's token or start the local service.

Remote links must name one HTTPS origin with no userinfo, query or non-root path. Authentication uses the existing
fragment-token protocol after an authenticated lifecycle probe that refuses redirects and validates TLS normally.
No certificate exception is offered.

Tokens are saved only with operating-system-backed Electron safeStorage;
the Linux basic_text fallback is refused. Without secure storage, tokens last for this process only and the screen
explains that signing in again will be necessary. The directory contains only origins and encrypted sign-in values.

Every newly opened workspace has a fresh nonpersistent session. Once the new page loads, switching destroys the
previous renderer and clears its session. Remote pages get only appearance metadata from a dedicated preload, with no IPC functions.
Local browser automation, native capture and service control remain available only to the owned local workspace.

The browser bridge stops before a switch and only starts for the local workspace. Remote sessions never receive
the local service token or bridge token. No remote-controlled hostname or executable enters local process launch.

The owned local service stays running while another workspace is selected, so returning locally preserves host
work. The app's existing quit path still stops that owned process. Startup failures and failed remote connections
leave the connection screen usable. Failed verification never replaces a working workspace. Explicit switching
may interrupt work in its renderer; the picker names the current workspace before the user chooses another.

### Consequences

The remote web application remains usable, while native host integrations are intentionally unavailable there.
iCloud sign-in synchronization remains a Swift feature. No new dependency or public deep-link handler is added.

### Confirmation

Verify link rejection, redirect refusal, ciphertext storage, main-frame authorization, failed-switch retention,
session teardown and local service ownership with focused tests. Capture the bundled screen with fictional hosts.
Signed desktop acceptance and a real authenticated remote host remain separate from mock or browser evidence.

The application menu names the entry **Switch Workspace…** (Shift-Command-comma), the same words as the native app.
