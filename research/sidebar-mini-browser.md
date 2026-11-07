# Sidebar mini browser

Prepared on main `22d9560ef`, ported to main `5914dd5b9` on 7 Nov.
This feature is unpublished; native containment and UI integration must be reviewed before activation.

The workspace uses `Shell` and `WorkspacePanels` for stable mounted right-dock views. Add a Browser tab beside
Terminal, with a location field and explicit navigation. In the ordinary web application this is an external-link
launcher with an explanation that in-panel browsing requires the desktop application. Keep the existing CSP,
frame policy and authentication boundaries unchanged; do not add an iframe or server-side fetching proxy.

Desktop browsing uses Electron `WebContentsView`, not the product web contents. It has a fresh in-memory session,
no preload, no Node integration, sandbox and context isolation. Never pass the product connection, bearer token,
cookies or headers into the guest. Deny permissions, popups, downloads and external protocol dispatch. Retain normal
certificate validation. Only HTTPS public host URLs without userinfo are admitted, including redirects and guest
subresources; block the product origin and local/private literal hosts. The guest-only fixed CONNECT broker
validates resolved addresses and pins its numeric connection, because hostname checks alone allowed a real Chromium
host-resolver override to reach loopback. Product authentication must never enter the guest. This remains a
Chromium-session policy rather than a kernel-enforced network sandbox.

The trusted product main frame alone can request guest creation, navigation, geometry or destruction. Authenticate
window membership, frame identity and current owned UI origin on every IPC request. Validate command shapes and
finite clipped bounds. Close the guest on product navigation, window destruction and shutdown. Native surface
visibility must follow the selected tab and all product overlays; never allow external content to cover dialogs or
trusted navigation. Do not auto-open denied popups or suppress TLS errors. Unsupported websites get a fixed,
non-secret status and a user-controlled external link.

The initial guest has no persistent browser profile. Closing it discards the browsing session. This must be visible
in the UI. Site credentials entered inside the guest belong only to that guest; they are never exported to Heinzel.
No backend endpoints, runtime service or plugin authority is added.

Required verification includes forged/subframe IPC, credential-bearing/local URLs, redirects, denied capabilities,
geometry, hide/close cleanup, ordinary browser fallback, real native guest/session isolation, and keyboard/mobile
layout. No site acceptance is inferred from fake controller tests.

References: [Electron WebContentsView](https://www.electronjs.org/docs/latest/api/web-contents-view),
[Electron security](https://www.electronjs.org/docs/latest/tutorial/security), and
[session partitions](https://www.electronjs.org/docs/latest/api/session#sessionfrompartitionpartition-options).

## Guest network prototype

The broker accepts only authenticated CONNECT requests for DNS hostnames on port 443. It resolves A and AAAA,
refuses the entire answer set if any address is nonpublic or belongs to this host, then opens a numeric socket.
The guest uses a fixed proxy with the implicit loopback bypass removed; configuration and pooled-connection
closure finish before creating the view. There is no DIRECT fallback, custom certificate trust or TLS interception.
Each guest receives a separate credential and listener; closing the guest closes the listener and tunnels.
HTTP authentication for websites remains unavailable. Proxy credentials are returned only for the exact owned
loopback proxy challenge. Headers, destinations and authentication values are not logged.

Fifteen focused policy, controller and broker checks pass, including mixed DNS answers, transition addresses,
nonstandard ports, proxy authentication and close during setup. An isolated Electron 45 fixture loaded public
HTTPS and reloaded it successfully, while preserving cookie/preload separation. A native host-resolver override
for a public-looking hostname no longer reached the controlled loopback TCP listener. A separate bounded native probe attempted worker fetch, WebSocket, WebTransport, STUN and TURN routes and
observed no private TCP or UDP connections. This observation is not proof that every transport progressed far
enough to exercise every Chromium networking path. These observations do not establish universal Chromium
egress confinement. Dead-broker controls, Alt-Svc/QUIC and network-service restart qualification, independent
source review and current-main integration remain required before enabling the feature.
