---
status: proposed
date: 2026-11-05
implementation: partial
decision-makers: maintainer
related: 0039, 0045, 0115
---

# 0138: Isolate sidebar browsing from the workspace

## Context and Problem Statement

A person wants a small browser beside their work, like the existing terminal panel. The authenticated web workspace
has a strict content policy and must never share credentials or native privileges with a third-party website.
Many websites also refuse embedding in another web page. A fetching proxy would introduce unnecessary server authority.

## Considered Options

- **Separate native desktop surface and an honest web link launcher** (chosen).
- **A web iframe**: requires broadening the workspace policy and cannot reliably embed arbitrary sites.
- **A server-side proxy**: creates a network and authentication boundary unrelated to this user interaction.

## Decision Outcome

Use an Electron WebContentsView with a fresh nonpersistent session, sandbox, context isolation and no preload or
Node integration. The authenticated product main frame controls this surface through narrowly validated commands.
Do not grant guest permissions, downloads, popups or external protocol dispatch. Keep ordinary certificate checks.

HTTPS navigation and subrequests reject credentials, literal IP addresses, local names and the product origin.
Only the default TLS port is supported. Before creating the guest view, configure its fresh session with a fixed,
authenticated local CONNECT broker and explicitly subtract Chromium's implicit loopback bypass. Close pooled
connections before the first navigation.

The broker resolves both address families, rejects any nonpublic answer,
and connects a selected numeric address without a second DNS lookup. It does not decrypt TLS or receive product
credentials. It rejects ordinary HTTP, arbitrary ports and unauthenticated clients, bounds connection/header/time
resources, and closes all sockets when the guest closes. Proxy failure has no configured direct fallback.

This is a guest-session network policy, not an operating-system egress sandbox. Qualification must include
Chromium routing outside ordinary navigation, especially WebRTC, workers, WebTransport and proxy failure.
The feature remains disabled until independent review and those native negative probes are complete.

Place the Browser tab and its toolbar button beside Terminal in the existing workspace panel. Switching to another
tab only hides the native surface, so the page keeps its state. Its native surface is visible only inside
its selected content area, hidden for product dialogs, navigation and background windows, and closed with the panel
or product window.

Display the actual guest location separately from the editable destination. Closing the panel
clears its separate session. Unsupported sites receive an explicit status, not a simulated successful page.

The ordinary web application offers an address field and a user-clicked external link with no opener or referrer.
It explains that browsing inside the panel needs the desktop application. No CSP, iframe, backend, plugin or service
permission changes are part of this feature. The `sidebar_browser` flag keeps the implementation dark until review
and native containment qualification finish.

### Consequences

The desktop gets genuine browsing without using the workspace's authenticated session. Popups, downloads and some
login flows are intentionally unavailable in this first version. Browser-hosted workspaces retain an external-tab
workflow rather than an unreliable frame. Native geometry, overlays and session cleanup need real desktop tests.

### Confirmation

Source implementation is partial and unpublished. Policy tests cover denied URLs, foreign frames and invalid bounds.
Complete controller, UI and native session-isolation tests remain required before activation. The research note
records the containment contract and evidence limits; no live workspace was changed to validate this prototype.

## More Information

[WebContentsView](https://www.electronjs.org/docs/latest/api/web-contents-view) provides the native surface.
[Electron security](https://www.electronjs.org/docs/latest/tutorial/security) describes untrusted-content isolation.
