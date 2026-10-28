---
status: proposed
amended-by: 0090
date: 2026-10-25
amends: 0035, 0039, 0046, 0048, 0056
related: 0019, 0027, 0030, 0035, 0072
---

# 0084: Private web sharing admits exact HTTPS origins and keeps device authentication

## Context and Problem Statement

The web head binds to loopback and checks the browser's Host, Origin and port before bearer authentication. A TLS
reverse proxy preserves the external Host while forwarding to a different loopback port, so merely adding an API
host does not make the authenticated web app work. The maintainer requests Tailscale Serve in the same release as
UI completion, with one sharing toggle, a verified URL and no accidental change to another service.
[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) terminates HTTPS and forwards to a local service.

## Considered Options

- **Exact user-configured HTTPS origins plus a first-party Tailscale adapter** (chosen).
- **Trust forwarded headers or all tailnet host names**: a caller could choose the authority; this would widen the
  unauthenticated REST head or mistake network identity for permission to open the person's shell.
- **Expose hz on all interfaces or enable Funnel**: changes the listener and public-sharing boundary unnecessarily.

## Decision Outcome

Chosen option: **exact HTTPS origins and a first-party adapter**, because the existing authenticated web head can
remain the only API and terminal owner while sharing is deliberate and reversible.

- `[hz] ui_allowed_origins` is a user-only set of canonical HTTPS origins. It rejects credentials, wildcards,
  paths, query strings, fragments, controls and invalid authorities. Project configuration cannot change it.
  Reading/editing it through configuration operations requires the existing authenticated owner's permissions.
  It is separate from `api_allowed_hosts`, which continues to configure the restricted REST head only.
- One origin policy guards web pages, the mounted authenticated API, pairing, operation/notice streams and terminal
  WebSockets. A configured external authority has its own HTTPS port, independent of the loopback backend port.
  A presented Origin must be that exact origin, including its effective port. Cross-site/same-site fetch metadata,
  unapproved authorities and malformed/opaque origins are refused. WebSockets still require an Origin.
- The listener stays on `127.0.0.1`. Forwarded headers and Tailscale identity/capability headers grant no authority.
  The existing session bearer, paired-device digest/revocation, operation exposure and terminal first-frame token
  rules remain mandatory. CSP stays same-origin; no cookie or URL-query token is introduced.
- Configuration is loaded at backend construction; the shared Restart and apply flow must verify the replacement
  before a new origin is advertised as ready. Browser storage is origin-scoped; bearer credentials keep their existing authentication semantics. A callable URL never
  includes a credential; an unpaired browser still needs the session fragment from its owner, or an existing
  paired-device credential for that origin. Removing an origin removes its routes' reachability, not other devices.
- The first-party `heinzel-tailscale` plugin uses the installed Tailscale CLI through bounded argument-list calls.
  It reads installation/login/DNS/Serve state and exposes typed status, enable and disable operations through the
  existing CLI, API and MCP heads. A single UI toggle is the person's authorization to perform its corresponding
  operation; no second plan/confirmation wizard or caller-supplied `human=true` flag is added.
- Enabling internally checks a fresh plan, refuses a conflicting root mapping, configures only hz's HTTPS Serve
  mapping and user origin, then verifies the resulting mapping and authenticated serving instance before reporting
  ready. Genuine login/HTTPS consent stays interactive. No login, OS installation, Funnel, reset-all command or
  sharing mutation runs during discovery, rendering, status reads or development tests.
- Disabling removes only the mapping the plugin owns after checking it still matches; unrelated paths, ports,
  services and origin configuration are preserved. Concurrent external edits, partial changes and uncertain command
  outcomes are reported and reconciled from actual state; no blind overwrite or automatic broad rollback.
- The plugin declares its settings/navigation/status link through normal provider metadata. Network reachability
  does not make other tailnet users the local owner; the URL opens the same owner's application and shell only
  after the existing credential checks. Multi-user hosting needs its own identity/permission design.

**Conditional local writes and configuration ownership.**

The separate wheel is available through Discover or `hz system plugin add tailscale --confirm`. The core PyPI
distribution does not bundle the plugin wheel and its ordinary install remains independent. The desktop install set
must package or select this plugin through its own verified channel. First-party core/plugin versions follow the
existing synchronized release process.

The CLI's ordinary Serve mutation does not expose the conditional version needed to preserve another process's
changes. A narrow exception to ADR 0035 allows this plugin to use Tailscale's own ordinary-user LocalAPI for
`GET`/`POST /localapi/v0/serve-config`: require a bounded unambiguous ETag and send its exact `If-Match` on writes.
HTTP 412 stops without a retry or rollback. No other LocalAPI route or remote vendor API is available to this
transport. This uses a small plugin-only `httpx` dependency; vendor client SDKs remain unnecessary.

On an explicit enable/disable action only, bounded `tailscale debug local-creds` discovers its local endpoint.
The known curl example is parsed as data, never executed. Only a local Unix socket or literal loopback TCP port
is accepted. The ephemeral local credential stays in memory, outside models, URLs, logs and configuration;
redirects and inherited HTTP proxy settings are disabled. Unsupported formats or missing conditional support
stop safely. This unstable discovery interface is checked against
[Tailscale's implementation](https://github.com/tailscale/tailscale/blob/main/cmd/tailscale/cli/debug.go), and the
conditional route against [its LocalAPI handler](https://github.com/tailscale/tailscale/blob/main/ipn/localapi/serve.go).
No CLI token store is read. There is no privileged helper, sudo call, automatic login or HTTPS consent. This
adapter checks required local capabilities rather than trusting a version number: absent ETag support, an unknown
endpoint format or unavailable HTTPS eligibility is a refusal, including on newer vendor versions.

One owner-only action lock serializes plugin writes across local heads. The existing `ConfigEditor` gains a
locked string-list change returning immutable `ConfigListChange` membership deltas, so an origin is merged/removed
without replacing unrelated user configuration. Only entries actually inserted under that lock may be claimed;
a manual grant interleaved before the lock remains unowned. A crash before persisting that result leaves the grant
unclaimed rather than guessing ownership. Ordinary
configuration writes use the same advisory lock and exclusive private atomic temporary file. Higher-priority
environment/explicit policy is refused rather than silently overridden. The persisted intent records the exact
requested target, origin claim and mapping phase: enabling, owned, disabling or released. A matching pre-existing
handler is never adopted. Owned requires acknowledgment plus fresh verification of a create; an uncertain create
remains unverified and cannot authorize deletion. No-intent/released OFF never removes a matching manual mapping.
Verified removal persists released before origin cleanup, ending the mapping claim; a later OFF cannot delete a
manual recreation or replay old cleanup. An origin-cleanup failure leaves any remaining grant for owner inspection.
Reads never replay intent. Partial writes and unknown
vendor outcomes remain visible for reconciliation; automatic broad rollback could
overwrite another owner's changes and is forbidden.

**Runtime facts and the generic explicit control.**

The public immutable `LocalWebRuntime` contains only the actual bound port and loaded allowed origins. The web
head supplies it before resolving providers; other heads receive absent facts. It contains no bearer, listener,
restart handle, process registry or origin-membership probe. CLI/stdio MCP can persist the same request and configure
the known target but return verification pending; a previously observed override is preserved until a web owner
supplies fresh actual facts. Ready requires an owned exact Serve mapping, configured and loaded origin, matching runtime
port, authenticated owning web request and certificate-capable DNS name reported by Tailscale. Missing login or
HTTPS capability names the vendor action required and never produces an ON state.

`StatusItem.toggle` is a generic typed `ToggleControl`, not plugin JavaScript or a plugin-name branch. The host
resolves its enable/disable operations through the owning module's existing exposure/private JSON-body rules. Its
checked value is actual readiness; an optional requested value exposes a pending request without claiming ON and
keeps its disable action available. Bounded nonsecret `apply_settings` values are expectations for the existing
shared lifecycle validation; the explicit toggle gesture authorizes the required apply/restart. No provider gets
independent restart authority. A separate Copy sign-in link gesture may attach the existing session fragment only
to an exact loaded approved origin. The host computes eligibility, overwrites any provider assertion, and refuses
to use a paired-device bearer as session pairing authority. Enabling never copies a credential automatically.

### Consequences

- Good: a private HTTPS proxy works with HTTP operations, pairing, streams and the real terminal without weakening
  the default localhost or public REST policy.
- Bad: a configured origin and reverse proxy become part of the owner's trusted deployment. Each origin has its own
  browser storage, permissions and pairing; changing it requires deliberate recovery rather than silently copying tokens.
- Bad: Tailscale CLI behavior and concurrent changes must be checked explicitly; a successful process exit is not
  evidence that the URL is ready. Real tailnet/OS consent remains a deployment proof beyond offline tests.

### Confirmation

`tests/integration/web_origin_test.py` checks reverse-proxy authorities, bearer/device pairing, HTTP streams and
terminal WebSockets plus negative Host/Origin/fetch-metadata/forwarded-header cases. Kernel settings tests reject
unsafe origin grammar and project overrides. Plugin tests exercise conflicting/unrelated mappings, bounded CLI
failures, private locks, conditional local writes and disabled/login/HTTPS-required states with fake vendor calls.
Offline CLI/web integration tests exercise persisted intent, actual port overrides, shared restart expectations,
manual origin preservation, concurrent conflicts and uncertain outcome reconciliation. Actual OS sharing
is never enabled by these tests. Independent security review is required before composing this slice into the UI PR.
