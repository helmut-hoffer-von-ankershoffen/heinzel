---
status: proposed
date: 2026-11-05
implementation: partial
decision-makers: maintainer, owner (device and security-key prompts, kept sign-ins)
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

Since 0.9.45 the panel has tabs: up to 8 of the person's own per window, opened with the plus button or Cmd/Ctrl-T and
closed with Cmd/Ctrl-W. Every tab is its own guest in the one `persist:hz-browser` partition and keeps every rule above.
Each tab carries an `owner` (`person` today), so tabs of agents can be told apart later. Closing the panel hides the
tabs. They end with the product window or its next document, and Clear browsing data covers all of them.

In the desktop app, links open in the built-in browser by default, in a new selected tab with the panel opened. That
covers links in the Heinzel UI (chat messages, cards, "Learn more"), `window.open` and target=_blank from embeds, and
target=_blank from pages inside the browser. Only http and https links go there. Each needs the person's own input
right before it (the gesture rule of the external opener), at most one every half second.

The rules of a typed address apply: a link to a non-public or non-HTTPS target is refused with a visible note. When all
8 tabs are in use the link is not lost: the panel shows a note with the link and a button to open it in the default
browser. Links with `mailto:` and other schemes go to the system as before, and so does the "Open in browser" button.

A switch in Settings > Browser, "Open links in the built-in browser", is on by default and applies with no restart.
Off, links open in the default browser as before. The choice is a setting of the desktop app itself, kept in a small
file in its own data folder by the main process and read by the page through the bridge. A reset of the page's storage
cannot turn it on again. In the PWA nothing changes: a link opens a new browser tab.

Since 0.9.45 the browser has bookmarks. They are owner data in hz, not browser state: one ordered tree of folders and
bookmarks per owner, kept by the `system.bookmark` module in the data directory and changed only through its operations
(`list`, `add`, `folder`, `edit`, `move`, `remove`, `restore`). Nothing of it lives in the `persist:hz-browser`
partition, so Clear browsing data keeps every bookmark. A bookmark is an HTTPS address without sign-in details, as the
browser opens no other. A removed entry, a folder with everything in it, stays restorable until newer removals push it
out.

The address bar has a star that adds the shown page and then edits its name and folder or removes it. The manager at
`/bookmarks` shows the tree: folders, rename, drag and buttons to reorder and move, and remove with undo.

The left navigation shows the whole tree: folders open and close, and which are open is remembered in that browser;
every bookmark has its favicon and title. It is a tree view for the keyboard and screen readers: arrow keys move, right
opens a folder, left folds it, Enter opens. A long tree scrolls inside its own section. The collapsed rail keeps the
top-level bookmarks as icons.

A bookmark opens a new tab of the built-in browser in the desktop app, and a new browser tab in the PWA. Its rows are
buttons, like the rest of the navigation.

A favicon is the icon the page itself named (`page-favicon-updated`): the desktop reads it inside the guest partition,
under the same public-HTTPS rule and proxy as the page, reduces it to a 32-pixel PNG and keeps it per origin for its
tabs. For the origin of a bookmark the page hands that small image to hz, which checks it and caches it per origin in
the cache directory. No third-party favicon service is asked; an origin without an icon shows a globe.

Agents can read the tree and add to it: `list`, `add` and `folder` are MCP tools over stdio, and a chat proposes an
`add` like any other write. Rename, move, remove, restore and the favicon cache are the person's (CLI and web app only),
so no agent can propose them. The unauthenticated REST and HTTP MCP heads get none of the bookmark operations.

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
