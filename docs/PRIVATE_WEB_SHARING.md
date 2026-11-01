# Share the authenticated web app through a private HTTPS proxy

Keep `hz ui` listening on `127.0.0.1` and allow the proxy's exact HTTPS origin in your user configuration. The
restricted REST head's `api_allowed_hosts` setting does not configure the web app.

For an existing private reverse proxy, configure its actual origin and start the backend on its stable port:

```sh
hz system config set hz.ui_allowed_origins '["https://workstation.example.ts.net"]'
hz ui --no-browser
```

Restart an already-running backend after changing the allowed origins. Until that replacement has loaded the
setting, a proxy request remains refused. A nondefault HTTPS port must be included, for example
`https://workstation.example.ts.net:9443`. The backend's loopback port is independent of this external HTTPS port.
Use a fixed backend port for a persistent proxy; `--port 0` is for temporary sessions.

An origin contains a scheme, host and optional port. Only HTTPS is accepted; credentials, wildcard hosts, non-root
paths, queries, fragments, escapes and control characters are rejected. A project's `hz.toml` cannot add origins.
`HZ_UI_ALLOWED_ORIGINS` accepts a comma-separated list and takes precedence over the configuration file.
Configuration operations expose this network trust setting only to the authenticated owner; the unauthenticated
REST head cannot read, approve or remove origins.

## Authentication and pairing

The proxy URL does not sign someone in. Access from your private network still requires the web app's session
bearer or a paired-device credential. The terminal opens the backend owner's shell after those checks, so share an
authenticated link only with devices you trust with that account.

For a fresh browser, use the session link printed by your own `hz ui`, replacing only its loopback origin with the
exact approved HTTPS origin and retaining its `/#token=…` fragment. The fragment is read by the app; it is not sent
to the proxy or server. Never put a token in a URL query, configuration, screenshot or log. Pairing and notification
permissions are stored separately by each browser origin; moving to a new origin does not copy them automatically.
`hz ui --devices` and `hz ui --revoke ID` retain their existing meaning, including remote browser credentials.

Pages, operations, pairing, notification/operation streams and terminal WebSockets all use the same authority
policy. A mismatched Host/Origin, opaque Origin, duplicate authority header or cross-site fetch is refused before
operation authentication. Forwarded and Tailscale identity headers do not replace the bearer credential. The proxy
must preserve the original Host; the app does not infer trust from forwarded headers or widen its CSP.

## Tailscale Serve

[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) provides a private HTTPS proxy to a local
service. Its network login and HTTPS consent are separate from hz's browser authentication. This change does not
install Tailscale, log in, enable sharing, change existing Serve mappings or enable public Funnel automatically.
The [first-party plugin](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/tree/main/plugins/heinzel-tailscale/README.md) provides one **Share over Tailscale** control in
Settings and its footer popover, with the same operations for CLI and agents: `hz tailscale status`, `hz tailscale enable` and `hz tailscale disable`.
ON means the exact mapping and authenticated serving instance were verified; a separate process reports pending
until that owner confirms the loaded configuration. Copy URL remains nonsecret. Copy sign-in link is a separate
session-owner action; enabling never copies credentials automatically. Manual origin configuration remains useful
for an already-managed reverse proxy.
