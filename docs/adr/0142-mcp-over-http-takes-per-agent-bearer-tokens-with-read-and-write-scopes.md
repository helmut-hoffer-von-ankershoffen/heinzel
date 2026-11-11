---
status: proposed
date: 2026-11-08
implementation: built
decision-makers: maintainer
related: 0019, 0047, 0068, 0138
---

# 0142: MCP over HTTP takes per-agent bearer tokens with read and write scopes

## Context and Problem Statement

`hz system serve mcp --transport streamable-http` serves every exposed operation on a port without any
authentication ([ADR 0019](0019-mcp-and-rest-heads.md)). Whatever reaches the port can call every tool, including
the ones that change state; only the stdio-only operations and private operations are left out
([ADR 0047](0047-plugins-are-managed-from-the-web-app-after-a-confirmation.md)). Coding agents on other machines of
the owner's tailnet (a desktop Codex or Claude) should attach to one `hz` through this head, and each of their calls
must say which agent made it. The MCP SDK already supports a bearer-token verifier on its streamable-HTTP app and
exposes the verified token to the tool handler for the duration of a call.

## Decision Drivers

- Nothing reachable on a port runs an operation without a credential.
- Least privilege: an agent that only reads cannot change anything, and the line between reading and writing is the
  operation's own `read_only` flag, not a second list to keep in sync.
- Every call is attributed to a named agent in the log, without the secret ever reaching a log or the disk.
- The default stays on this machine; reaching it from the tailnet goes through Tailscale Serve, never a public
  interface by accident.

## Considered Options

- **Per-agent bearer tokens, hashed at rest, with `read` or `write` scope, checked per tool call** (chosen)
- **One shared server token, like the private REST head's** ([ADR 0068](0068-human-approval-proofs-and-head-parity.md)):
  no per-agent actor, no per-agent revocation, and one scope for everyone.
- **OAuth with the SDK's authorization-server provider**: the right shape for a public endpoint, far more surface
  than a tailnet needs today; revisit when a public endpoint is decided.
- **Rely on the tailnet or loopback alone**: any process on the machine or any tailnet device could call write tools.

## Decision Outcome

Chosen option: **per-agent bearer tokens with read and write scopes**, because it gives an actor per call and least
privilege with the SDK's own verifier hook and no new dependency.

- The streamable-HTTP MCP head requires `Authorization: Bearer <token>` on every request; a missing, unknown,
  revoked or expired token gets HTTP 401 before any MCP message is handled. It refuses to start while no token
  exists.
- A token belongs to one named actor and has the scope `read` or `write`. `read` runs only operations whose
  `read_only` is true; `write` runs every operation the head exposes. A read token calling any other tool gets a tool
  error and the operation does not run.
- Tokens are made, listed and revoked from the CLI only (`hz system serve mcp-token`), never through an MCP tool or the
  REST head. The token is shown once; the state directory keeps only its SHA-256, in an owner-only file (0700
  directory, 0600 file). Tokens expire (90 days by default) and unknown or expired tokens are compared in constant
  time.
- Every tool call logs one start and one outcome event `mcp.http.call` with `actor`, `key_id`, `tool`, `scope`,
  `read_only`, `outcome` and `duration_ms`; the token and its digest are never logged.
- A refused token logs one `mcp.http.auth` event with `outcome` `refused` and `reason` (`unknown` or `expired`).
- The head binds to loopback by default. Any other interface needs `--expose` together with the `--allowed-host`
  names other devices use; Tailscale Serve proxies to loopback and its host name is allowed with `--allowed-host`.
  The SDK's DNS-rebinding protection stays on, and no browser origin is accepted except a Serve name's HTTPS origin.
- Over HTTP, stdio-only and private operations stay out, as before; a token never grants the owner's private scopes
  or approval proofs.

### Consequences

- Good: a tailnet agent can read the board without being able to change it, and every write names its agent.
- Good: revoking one agent leaves the others working.
- Bad: existing unauthenticated streamable-HTTP setups stop working until a token is made and sent.
- Bad: the actor is in the log only; operations do not yet receive it as their call identity, so a write an operation
  records (a tracker note) does not carry the agent's name until the kernel identity gains an MCP bearer kind.

### Confirmation

Unit tests drive the real streamable-HTTP app in process: no token and a wrong token get 401, a read token runs a
read-only tool and is refused a write tool, a write token runs it, and the audit events carry the actor but never the
token. Serving refuses a non-loopback host without `--expose` and refuses to start without tokens.

## More Information

Bead 4p8.6 of the dogfood epic (agents attach to the board through hz). A public endpoint (ChatGPT web) stays out of
scope; it would need OAuth and a separate decision.
