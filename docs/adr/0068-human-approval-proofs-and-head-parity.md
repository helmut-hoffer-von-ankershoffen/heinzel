---
status: proposed
date: 2026-10-25
decision-makers: maintainer (direction), architect and security reviewer (protocol)
amends: 0045, 0039
related: 0003, 0019, 0026, 0046, 0047, 0049, 0050
---

# 0068: Human approval is a scoped proof carriable by every authorized head

## Context and Problem Statement

Current chat exposes confirmation on local CLI and paired web, withholding it from MCP and unauthenticated REST.
This prevents the chat's own tools from confirming their proposals, but ties authorization to a transport. The
same data operations must be available through API, CLI and MCP, and an approval
issued by the human can be carried by any head, including MCP. The ordinary REST head has no authentication and
must not acquire private chat or data merely to satisfy parity.

## Decision Drivers

- One operation schema, policy gate and result across heads; streaming need not use identical transports.
- Human intent authorizes exactly the previewed action; a model call, prompt or transport cannot mint that intent.
- Concurrent retries, crash recovery and revoked grants cannot execute an effect twice or change approved arguments.
- Private data and bearer proofs remain private, with honest limits against installed trusted code or a compromised host.

## Considered Options

- Keep confirmation exclusive to UI/CLI: rejected by the maintainer's explicit parity direction.
- Treat an MCP call or boolean argument as human approval: rejected; neither establishes authority.
- Scoped opaque proof issued through a trusted human interaction and consumed by the common service: chosen.
- A permanent bearer granting future arbitrary writes: rejected; too broad and difficult to review or revoke safely.

## Decision Outcome

Chosen option: **one authenticated identity seam and scoped approval proofs shared by authorized heads**, because
transport choice must not change data ownership or permit an agent to authorize its own effects.

1. **Identity foundation.** A private installation owns a stable random user id, stored privately with no links or
   unsafe ownership/permissions. A trusted local head or authenticated bearer establishes an immutable principal
   and call context; no model argument, header claiming an owner, username or project setting supplies it. A
   context variable is set/reset per call, never a shared kernel binding changed per request. Authentication proves
   identity, not human approval or permission to issue proof. Identity and default credential paths use user-scope
   settings, never an effective project override. Relative user paths are anchored to the trusted home directory
   and tilde paths are expanded, so repository CWD cannot select the owner or default credential destination.
   This protects against repository-selected identity storage; it
   does not sandbox a same-user attacker who can replace trusted parent directories.
2. **Private API.** Explicit private operations require that context before invocation and are excluded from the
   unauthenticated REST and HTTP-MCP surfaces. The private API accepts server-audience-bound, expiring bearer
   credentials and retains Host/Origin/Fetch-Site guards. Local CLI and stdio MCP establish the installation owner
   through the same seam; paired web authenticates its existing session/device credential. Credentials go to a
   private file or private request body, never command arguments, URLs or logs. New private HTTP mutations and
   streaming invocations use a flat JSON body of declared public parameters, with unknown fields refused; paired
   web and private API have the same binding. Existing operation wire shapes remain unchanged.
3. **Common service.** Every head calls the same proposal/decision/effect service. Proposals store the operation
   identity and canonical validated arguments privately; the preview is derived from those bytes. The agent cannot
   replace preview text or arguments after the decision. Hashes use a versioned canonical encoding that preserves
   JSON types; booleans, numbers and strings cannot compare equal accidentally.
4. **Issuance.** A trusted human interaction presents that authoritative preview, obtains an explicit decision,
   and issues a cryptographically random opaque proof. The issuer records its hash, a nonce, principal/provenance,
   session, action, arguments hash, contract/grant/policy revision, decision, intended service audience, issue time
   and expiry. Proposed default lifetime: 5 minutes, configured only by the user/admin. Issuance is not an ordinary
   model-visible operation. No model tool can mint proof by asking to confirm its own proposal. A user may provide
   a proof to a CLI, API client or MCP client to carry the already authorized decision.
5. **Consumption.** Every authorized head accepts the same proof in a private request body or file/stdin input.
   The service authenticates the caller separately, verifies ownership/audience, current policy and every bound
   value, then acquires the owning action/session writer lock. One durable authoritative event records authorization
   consumption and effect intent before execution. Derived SQLite caches are never the only record that a nonce was
   consumed. Multiple processes cannot consume one proof twice.
6. **Replay and crashes.** A repeat of the same consumed proof/action returns its existing disposition or result.
   A changed action, payload, contract or grant is refused. An intent without a completion is an explicit unknown/
   interrupted effect; it is reconciled or replayed only under a declared replay-safe edge idempotency key. Never
   claim distributed exactly-once behavior. Expiry blocks a new execution; it does not erase prior decisions or
   authorize a second run. Reading an old result still requires current session access.
7. **Revocation.** A user can revoke an unconsumed proof or the grant/rule that produced it. Check revocation and
   policy revisions immediately before recording intent. A deterministic user-authored auto-approval rule is off
   by default, scoped and revocable; when a matching action occurs it records the rule identity/version and bound
   decision. The model cannot create or widen its own rule. A crash-recovered rule decision follows the same ledger.
8. **Private transports.** Private API data/proposals/proof consumption require authentication. Reuse or extend the
   paired web's established local/paired identity seam through a reviewed authentication design; do not expose
   sensitive operations on unauthenticated REST. Stdio MCP runs under its explicit local principal; remote MCP
   requires authenticated transport. MCP transport by itself is never evidence of human approval.
9. **Secret handling.** Never place a raw proof in a URL, log, telemetry, event spine, process command line,
   notification deep-link or automatically generated model/tool result. The human explicitly transfers it when
   needed; only a hash and decision provenance are retained in metadata. HTTP and MCP consumers must redact proof
   fields in request tracing and error paths. Notifications identify the pending action, without embedding authority.
10. **Parity gate.** Add a generated operation-surface manifest from the kernel's operation registry and exposure
   rule. Every new API data operation must have CLI and MCP equivalents with the same parameter/result schemas,
   read/write classification and service policy. Explicit lifecycle/streaming/transport-control endpoints may differ,
   with a tested rationale in the manifest. Human-proof issuance is a trusted interaction primitive; proposal and
   proof consumption retain shared semantics. Missing parity or undocumented exclusions fail CI. Transport
   authentication requirements do not count as silently omitting a head.
11. **Threat boundary.** Installed Python and same-realm plugin JavaScript are trusted with the user's environment.
   A compromised trusted host can act as that user; this protocol does not prove physical human presence against
   such a compromise. It prevents model-visible tools from self-authorizing within the supported tool boundary,
   binds the decision to a reviewed operation, and provides revocation and auditability. Do not market a UI click or
   ordinary session token as an independent hardware-backed presence proof.

### Consequences

- A person may approve once and have the exact action executed through any supported authorized head.
- The effect gate becomes transport-neutral and versioned; heads remain views rather than permission engines.
- The authenticated private API, issuance/consumption service, secret-redaction checks and parity manifest are new
  implementation work. This ADR does not make the current unauthenticated API safe for private chat.
- CLI clients need secure proof input; a convenience `--proof SECRET` argument is deliberately unsupported.

### Confirmation

- Same fixture proposal yields equivalent preview/schema/result through authenticated API, CLI and MCP.
- Model tool list cannot issue approval; a forged proof, prompt “I approve”, changed arguments, wrong session/user,
  stale contract/grant, expired/revoked proof or nonce conflict is refused without executing the effect.
- Concurrent cross-head consumers execute one effect; a killed process at intent/completion boundaries produces
  the documented recoverable disposition. Reindexing cannot resurrect a consumed nonce.
- A scoped auto-approval rule matches only approved operations/targets/limits; revocation takes effect before intent;
  a model-proposed widening remains a human proposal.
- Proofs never appear in logs, errors, model-visible results, notification URLs, exported metadata or process arguments.
- Generated exposure tests reject an API-only data operation and an undocumented exception, and preserve authenticated
  private data restrictions. Schema parity does not require SSE bytes to look like CLI stdout.

## More Information

The first implementation slice supplies trusted principal contexts and the authenticated private API. The personal
five-minute check-in now has a production issuer behind the internal `ApprovalIssuer` kernel service. It is called
only after the CLI or paired web directly confirms, authorizes for one later MCP run, or a direct stdio MCP request
receives a fresh controlling-terminal challenge for a stored exact-intent proposal; there is no proof-issue operation
exposed to MCP. The issuer rechecks the schedule under its owner-scoped writer lock,
records a five-minute proof hash, and the normal `schedule_control` operation consumes the proof with the schedule
effect. The web UI creates and inspects the proposal in the personal-session panel, then presents the exact action
card with direct-confirm and authorize-for-MCP choices. CLI users can stage and confirm the same operation, or
authorize its one-use MCP handoff. The proof remains in private owner state and
does not appear in chat arguments, results, or the event spine. If confirmation is interrupted, the CLI and web can
reconcile its saved `running` action against the schedule journal. Only an exact recorded effect becomes `done`; all
other outcomes become `uncertain` and cannot be retried from that action. Recovery observes state and never invokes
the schedule operation. Native CLI execution and Pydantic model bridges have separate conformance requirements; an
unrestricted native process is not made safe by receiving a principal id.

The human can approve a pending exact schedule action through CLI or paired web for one later MCP execution. That
trusted boundary asks the existing issuer to create its normal five-minute proof and writes it to a private, mode-0600
per-chat handoff file; the chat record exposes only an `mcp_authorized` boolean. A chat-scoped stdio MCP tool consumes
the proof by session/action id. It accepts no proof argument, returns no proof, is hidden from non-chat MCP, and rejects
calls without a live grant. The session lock, exact issuer intent, proof expiry, running-before-effect state, and
one-use journal consumption continue to apply. The end-to-end test verifies refusal before approval, one execution,
no proof in API/MCP results, and replay refusal.

This is a schedule-specific server-side handoff, not a bearer token exported into MCP parameters. MCP clients can
retain tool arguments and transcripts, so passing the raw proof through a tool parameter would not meet the
no-token-in-logs requirement; the installed MCP SDK has no elicitation channel for a human answer. Direct stdio MCP
schedule activation and resume therefore display the exact intent on `/dev/tty`, require a fresh random challenge
within 60 seconds, mint and consume the proof internally, and omit the proof field from the tool schema. Calls without
a controlling terminal fail closed. The MCP tool caller cannot supply the challenge answer as an argument. This is
application-level confirmation, not hardware-backed proof of presence; a hostile process running as the same owner
can access that user's controlling terminal and state. CLI/web authorization and MCP consumption must run under the
same local principal and configured state directory; the normal chat runner carries the project directory
used to open the chat. Web authorization accepts only the paired `web-session` or `web-device` identity. CLI
authorization requires an interactive terminal and a typed session/action challenge; a
headless CLI call is refused. A different profile, state directory, host or principal has no handoff and fails closed.
The chat-scoped MCP consumer still requires a CLI/web-issued private grant; direct MCP stdio schedule-control uses
the separate local-terminal flow above. MCP over HTTP cannot access these private operations.
Other action types do not yet have this handoff. The MCP model can choose when to consume an already-approved exact
action, but cannot change or create that intent.

**Approval issuance and verification.**

The internal approval verifier checks an opaque proof against a trusted stored record, the authenticated principal,
current intent and revisions, revocation and expiry. The schedule action calls the journal helper under the owner-scoped
writer lock. It stores the exact proof hash, intent and schedule snapshot in one `schedule.activated` or
`schedule.resume` event, and recognizes a prior event without authorizing a second effect. Test-only synthetic records
are not human decisions. The record and current intent come from trusted services, never a request or model response.
A retry resolves the existing disposition from that journal, including after proof expiry. The private API and CLI/web
heads accept the same proof-bearing schedule operation. Stdio MCP strips the proof field and issues/consumes it
internally only after its TTY challenge; the user-facing chat issuer either executes directly after confirmation or
keeps the proof private for the MCP handoff. No head returns a transferable proof. The CLI still accepts `--approval-proof -` and reads
external proof input from hidden input or stdin instead of argv.

The human confirmation boundary is an explicit app or local-terminal decision, not hardware-backed presence: the CLI
`confirm` command and paired web button run the exact stored arguments; a model-facing MCP call cannot issue a human
decision, and direct stdio MCP confirmation is obtained only from the controlling terminal. The chat MCP head can
also consume the schedule-specific private handoff above. Authentication alone does not mint proof. A compromised trusted host or same-origin
web code can still act as the owner; this flow does not claim otherwise. Passkey enrollment is not required for this
five-minute use case. An optional stronger factor could add WebAuthn user verification later, with the extra enrollment
and recovery cost. The private API still accepts proof-bearing inputs from trusted user clients; stdio MCP does not
accept proof input, and no human UI exports raw proof for manual transfer. The issuer, revocation and all revisions needed at consumption are
coordinated with the action's authoritative journal, so a stale schedule fails closed.
