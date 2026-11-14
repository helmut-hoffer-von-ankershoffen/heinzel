---
status: proposed
date: 2026-11-05
implementation: partial
decision-makers: maintainer, owner (device and security-key prompts, kept sign-ins, player shares the sign-in)
related: 0039, 0045, 0115
---

# 0140: Isolate sidebar browsing from the workspace

## Context and Problem Statement

A person wants a small browser beside their work, like the existing terminal panel. The authenticated web workspace
has a strict content policy and must never share credentials or native privileges with a third-party website.
Many websites also refuse embedding in another web page. A fetching proxy would introduce unnecessary server authority.

## Considered Options

- **Separate native desktop surface and an honest web link launcher** (chosen).
- **A web iframe**: requires broadening the workspace policy and cannot reliably embed arbitrary sites.
- **A server-side proxy**: creates a network and authentication boundary unrelated to this user interaction.

## Decision Outcome

Use an Electron WebContentsView with its own website session, sandbox, context isolation and no preload or
Node integration. The authenticated product main frame controls this surface through narrowly validated commands.
Do not grant guest permissions, downloads, popups or external protocol dispatch. Keep ordinary certificate checks.

Bluetooth, HID, serial and USB prompts open a native chooser naming the requesting origin (an embed's own): the
person picks one listed device, each labelled as a device, or cancels. The device permission handler grants only a
picked device to the origin and device type that asked, never an automatic choice. A prompt from a background window,
from no guest or with no device grants nothing and shows a visible note. Camera, microphone, screen sharing and every
other permission stay refused.

All guests share one persistent partition (`persist:hz-browser`), never the product's session, so website sign-ins
survive closing the panel and restarting the app. "Clear browsing data" takes open pages off their sites, then empties
its cookies, storage, cache and authentication cache, revokes device grants and reloads the pages; a failed wipe is
shown. Electron keeps a device without a serial number outside the permission handler, so that grant lasts until the
device is unplugged or the app quits.

WebAuthn is not refused. Electron serves no platform passkey (iCloud Keychain, Touch ID) for third-party sites: that
needs a signed app whose identifier each relying party lists in its `apple-app-site-association`. The page then only
sees `NotAllowedError`, so a passkey step or WebAuthn failure shows a visible note that passkeys may not work, with
the choice of another sign-in option or "Open in browser", which hands the guest's current public HTTPS address to the
default browser after the person's click, under the window's external-link rate limit.

In the desktop the media player's YouTube embed is a guest on the same partition, so a sign-in in the browser counts
in the player and the other way round; the web app keeps its iframe and the browser's own cookies. The embed is a
top-level page there, so a narrow bridge carries only open, play, pause, volume and the player state, as fixed
scripts without a user activation. The player follows these guest rules and refuses Bluetooth choosers; its links
open in the default browser after the person's input, and "Clear browsing data" blanks and reloads it too.

The owner decided to allow device and security-key prompts with a user choice ("don't refuse device or security key
prompts") and to keep website sign-ins ("we want to stay signed in"). This replaces the earlier refusal of every
chooser and the per-window session wiped on close.

HTTPS navigation, every redirect hop and subrequests reject credentials, literal IP addresses, local names
(`localhost`, `*.local`, `*.lan`, `*.home.arpa`, `*.internal`, `*.corp`, `*.intranet`, tailnet `*.ts.net`), non-HTTPS schemes and the product origin.
Only the default TLS port is supported. Before creating the guest view, configure its session with a fixed,
authenticated local CONNECT broker and explicitly subtract Chromium's implicit loopback bypass. Close pooled
connections before the first navigation.

The broker resolves both address families, rejects any nonpublic answer (loopback, private, link-local, CGNAT and
tailnet 100.64/10, ULA, documentation and transition ranges),
and connects a selected numeric address without a second DNS lookup. It does not decrypt TLS or receive product
credentials. It rejects ordinary HTTP, arbitrary ports and unauthenticated clients, bounds connection/header/time
resources, serves every guest of the shared partition, and closes all sockets when the last guest closes.
Proxy failure has no configured direct fallback.

This is a guest-session network policy, not an operating-system egress sandbox. Qualification must include
Chromium routing outside ordinary navigation, especially WebRTC, workers, WebTransport and proxy failure.
The feature remains disabled until independent review and those native negative probes are complete.

Place the Browser tab and its toolbar button beside Terminal in the existing workspace panel. Switching to another
tab only hides the native surface, so the page keeps its state. Its native surface is visible only inside
its selected content area, hidden for product dialogs, navigation and background windows, and closed with the panel
or product window.

Display the actual guest location separately from the editable destination. Closing the panel
keeps the website session; only "Clear browsing data" ends it. Unsupported sites receive an explicit status, not a simulated successful page.

The ordinary web application offers an address field and a user-clicked external link with no opener or referrer.
It explains that browsing inside the panel needs the desktop application. No CSP, iframe, backend, plugin or service
permission changes are part of this feature. The former `sidebar_browser` preview flag is retired: the panel is always available in the desktop application
(a setting that still names it is ignored). Every safety rule here stays in force.

### Consequences

The desktop gets genuine browsing without using the workspace's authenticated session. Popups, downloads and platform
passkeys are unavailable; other sign-ins persist until the person clears browsing data. Browser-hosted workspaces
retain an external-tab workflow rather than an unreliable frame. Native geometry, overlays and session cleanup need real desktop tests.

### Confirmation

Source implementation is partial and unpublished. Policy tests cover denied URLs, foreign frames and invalid bounds.
Complete controller, UI and native session-isolation tests remain required before activation. The research note
records the containment contract and evidence limits; no live workspace was changed to validate this prototype.

## More Information

[WebContentsView](https://www.electronjs.org/docs/latest/api/web-contents-view) provides the native surface.
[Electron security](https://www.electronjs.org/docs/latest/tutorial/security) describes untrusted-content isolation.
