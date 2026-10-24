---
status: proposed
date: 2026-10-23
related: 0012, 0017, 0021, 0022, 0023
---

# 0028: Integrations are capability adapters; workflows name capabilities, never vendors

## Context and Problem Statement

Workflows read and write the tools teams already use: a code host, an issue tracker, documents, spreadsheets,
calendars, meetings and their transcripts, chat and mail. Teams use different vendors for each, and many reach them
through different transports: a vendor API, a CLI such as `gh`, or an MCP server, sometimes a company MCP gateway that
fronts several vendors at once. If workflows and core code talk to vendors directly, every workflow is tied to one
vendor and one transport, and switching costs a rewrite. How do workflows reach these tools?

## Decision Drivers

- Tenet 8 (open and independent, no vendor lock-in) and tenet 9 (consistent core, specifics as plugins).
- Workflows written by one team should run for another team that uses different tools.
- Companies already operate MCP servers and gateways; using them must not need a vendor-specific plugin.
- Typed, testable boundaries: the kernel and workflows see the same operations whatever the transport.

## Considered Options

- **Capability protocols in the core, adapters in plugins, binding by configuration** (chosen)
- **One plugin per vendor, with vendor-specific operations used directly by workflows**: simplest to start, but
  every workflow names a vendor, and switching vendors means rewriting workflows.
- **MCP only: workflows call MCP tools by name**: no typed contract, tool names and shapes differ per server, and
  nothing works without an MCP server.
- **A third-party unified-API service**: sends data through another vendor and adds a dependency we do not control
  ([ADR 0027](0027-nothing-is-sent-home.md)).

## Decision Outcome

Chosen option (proposed): **integrations are adapters that implement capability protocols defined by the core, and
configuration binds each capability to an adapter**, because workflows stay vendor-neutral and swapping an
integration becomes a configuration change.

- **Capabilities**: the core defines typed protocols for code host, tracker, documents, spreadsheets, calendar,
  meetings (including transcripts), chat and mail. Each protocol is small, versioned with the plugin API
  ([ADR 0026](0026-plugin-api-versioned-with-semver.md)), and covers what workflows need, not a vendor's whole API.
- **Adapters**: plugins implement one or more capabilities over any transport: a vendor API, a CLI such as `gh`, or
  an MCP server, including a company MCP gateway that fronts several vendors.
- **Generic MCP adapter**: a plugin maps an MCP server's tools to a capability through configuration (which tool
  serves which operation, and how arguments and results map), so an existing MCP server or gateway works without
  code.
- **Workflows name capabilities, never vendors**: a contract asks for `tracker` or `documents`, not for a product.
- **Binding**: configuration binds capability → adapter per team and per person, in the layered configuration
  ([ADR 0012](0012-layered-configuration.md)). `hz system doctor` checks every bound adapter.
- **Dependencies with access modes**: a plugin declares the capabilities it needs and in which mode, for example
  `requires = ["tracker:read", "documents:read,write", "chat:write"]`; an adapter declares what it provides
  (`provides = ["documents:read,write", "calendar:read"]`) and the hosts it reaches. Capabilities, not vendors, so a
  binding stays swappable; a vendor pin (`google-workspace:documents:read`) is allowed where a plugin truly needs a
  vendor-specific feature, and is shown as such.
  - `write` is only reachable through approved publish operations ([ADR 0020](0020-daemon-head.md)); a plugin that
    declares `read` gets a read-only handle and cannot write.
  - Adapters request credentials and OAuth scopes per mode, so a read-only setup never asks for write access.
  - `hz system doctor` reports unmet needs ("`chat:write`: no adapter bound"); `hz system info` shows the matrix of
    who reads and writes what, through which adapter and which hosts, which also answers "where can my data go"
    for a privacy notice ([ADR 0024](0024-people-and-org.md)).
  - Calling a capability or mode that wasn't declared fails, at runtime and in a test.
- **First adapters, planned for 0.2**: GitHub (code host, tracker), Atlassian (tracker, documents), Google Workspace
  (documents, calendar; spreadsheets and mail later), Zoom (meetings and transcripts) and Slack (chat). All five are
  first-party plugins in `plugins/` ([ADR 0017](0017-first-party-plugins-in-the-core-repository.md); the move from
  `packages/` is in flight): maintainer's decision, 2026-10-24.

### Consequences

- Good: workflows are portable between teams and vendors; switching a vendor is a configuration change.
- Good: companies reuse the MCP servers and gateways they already run.
- Bad: the protocols are the lowest common denominator; vendor-specific features need protocol extensions or stay in
  the vendor's plugin as ordinary operations.
- Bad: new work: the capability protocols, the binding configuration and its doctor checks, the generic MCP adapter,
  and contract tests every adapter must pass.

### Confirmation

Partly, for the part built in 0.2 (see the implementation status under More Information): a requester can't use a
capability or mode it didn't declare, through every head, for documents and for chat
(`tests/unit/kernel/bindings_test.py`, `tests/integration/capability_test.py`, with test-only chat, meetings and
calendar adapters that correlate an event with its meeting); requirement strings and the registry stay in step with
the protocols, window reads stay contract-shaped and id reads stay the only others, new datetimes are timezone-aware,
and people stay names and emails (`tests/unit/kernel/capabilities_test.py`); contract inputs name the new methods
(`tests/unit/modules/workflow_contracts_test.py`); the packet builder cleans every string and drops emails
(`tests/unit/modules/workflow_service_test.py`); `clean_text` (`tests/unit/kernel/text_test.py`) and
`assert_adapter` (`tests/unit/testing_test.py`); declarations are checked when a module is imported
(`tests/unit/kernel/module_test.py`); a hostile project `hz.toml` can neither bind nor configure execution, and
doctor names the keys it tried (`tests/integration/capability_test.py`); doctor reports every unmet requirement and
unknown binding; the import-linter contract "The core imports no vendor client or agent SDK" (`pyproject.toml`).
Still planned: a shared contract-test suite per capability that every adapter runs, the generic MCP adapter, an
import-linter contract that workflows and core modules do not import adapter plugins, and doctor checks that call
bound adapters.

## More Information

- The dependency model with read and write modes was proposed by the maintainer on 2026-10-23.

**What 0.2 implements.**

The status stays proposed: the generic MCP adapter, the first vendor adapters, approvals and the shared adapter
contract-test suite are not built yet. Built, with the choices made while building it and in review of the pull
request (director's decisions, 2026-10-24):

- **Protocols** (`heinzel.kernel._protocols`, public in `heinzel`): only what the first workflows need. `CodeHost`
  (`merged_pull_requests` in a `Window`), `Tracker` (`issues_updated`), `Documents` (`read`; `replace_section` as the
  only write), and, for the Slack, Zoom and Google Workspace adapters (below), `Chat`, `Meetings` and `Calendar`.
  Issues belong to `tracker` alone, also when they live on the code host: a GitHub adapter provides both
  capabilities. Results are pydantic models with described fields. No separate protocol version number: the protocols
  are public API and follow the core's SemVer ([ADR 0026](0026-plugin-api-versioned-with-semver.md)), which rejected
  a second API level number for the same reason.
- **Growing a protocol.** Adding a method in a mode a capability already has breaks every adapter serving that mode,
  so it goes through ADR 0026's deprecation cycle; adding a method in a new mode is additive, because adapters declare
  the modes they serve and are only required to implement those modes' methods. Adding a capability is additive too:
  no adapter serves it yet and no requester names it, so the core adds one whenever a first-party adapter needs it,
  in a minor release.
- **Each method has a mode** in the capability registry (`CAPABILITIES`); a unit test keeps the registry and the
  protocols in step. Capability names in requirements and bindings are `agent-runner`, `calendar`, `chat`,
  `code-host`, `documents`, `meetings` and `tracker`; the core defines the set (plugins cannot add capabilities yet)
  and grows it under the growth rule above. An adapter must implement the methods of the modes it declares, checked
  when the kernel registers an adapter class and when it builds one from a factory; `heinzel.testing.assert_adapter`
  runs the same check, plus the parameter names and defaults, in a plugin's own tests (a first piece of the shared
  adapter suite).
- **Chat, meetings and calendar** (director's decisions on the maintainer's instruction to start the Google Workspace,
  Zoom and Slack adapters, 2026-10-24). They serve the first management workflows: a weekly digest and 1:1
  preparation from channels and threads, meeting transcripts, and the calendar that says which meetings happened and
  are coming (revised on review of the pull request, 2026-10-24). Only reads that take a `Window` are
  contract-shaped: a contract input (`{read: <capability>.<method>, args, window}`,
  [ADR 0021](0021-workflow-contracts.md)) passes fixed `args` once, so such a method takes only required `str`
  arguments and exactly one `Window`, and a unit test keeps it so. Reads that take an id (`documents.read`,
  `meetings.transcript`) serve module operations, which know the id; a contract can't. Fewer methods are better:
  every one is frozen under the growth rule.
  - **The window bounds the result.** An adapter pages until the window is covered or a byte budget of its own is
    passed, then raises a `HeinzelError` that says to narrow the window. Results stay plain lists: no truncated
    flag, because a silently partial digest is worse than a run that says what to change.
  - `chat` (Slack first; Teams later): `messages(channel, window)`, the messages posted in the window, top level or
    reply, oldest first (a reply whose parent was posted before the window is included, the parent isn't), `read`;
    `post(channel, text, thread=None)` is the only `write`, reachable from publish operations alone, and in 0.2 only
    from module operations: a contract's `publishes` names an operation, never a capability method. `text` is plain
    text or Markdown; the adapter neutralises broadcast and mention syntax (Slack `<!channel>`, `<!here>`, `<@U…>`;
    Teams `<at>`) so a draft can't page a channel, and applies the vendor's length limit as it sees fit. One channel
    rule: a `channel` argument is an id or a `#name`, the adapter resolves names, and results carry the id. On Teams a
    `#name` is a channel of the configured team; direct messages and group chats are out of scope. Message ids are
    the vendor's (a Slack `ts`). No `channel(name)` lookup and no `thread(channel, message)`: `messages` already
    includes replies and no listed workflow needs a name lookup.
  - `meetings` (Zoom first; Meet and Teams later): `ended(window)` lists the user's meetings that ended in the window
    with their participants; `transcripts(window)` returns the transcripts of those that have one, each carrying its
    `Meeting` (id, title, start, end, participants) and speaker-attributed segments, so a contract reads transcripts
    without knowing ids; `transcript(meeting)` is the operation-level read for a known `Meeting.id`. All `read`;
    nothing schedules or records. There is no plain-text field: it derives from the segments, and a transcript is
    large enough as it is.
  - `calendar` (Google Calendar first; Outlook later): `events(window)`, the user's events overlapping the window as
    expanded single occurrences, with organizer, attendees and status, `read` only. Contract windows end at the run
    in 0.2, so "meetings coming up" waits for ADR 0021's later window semantics; the method is not limited to the past.
  - **Correlation.** A meeting matches an event when both have the same `conference_id` and their times overlap: the
    id recurs across occurrences, the time picks the occurrence. `Meeting.id` identifies the occurrence (a Zoom
    meeting UUID, not its number); `conference_id` is in the meeting tool's notation on both sides, so the calendar
    adapter writes the meeting tool's id (a Zoom number extracted from the conferencing data, location or description).
  - **People** appear as `Participant`: display name and email only, how the vendor shows them; no field counts,
    rates or ranks anyone ([ADR 0024](0024-people-and-org.md)). The email is the join key ADR 0024's directory and
    selectors will resolve to; a transcript segment names its speaker as the transcript labels them. The workflow
    packet builder drops every `Participant.email` and keeps the names, until people selectors need the join key: a
    render argues about work, not addresses. Run records (`.record.json`, receipts) hold the names and the full inputs
    until the user deletes them; retention is an open question for ADR 0024.
  - **Text is data.** Every string an adapter returns passes `heinzel.clean_text`, the one helper: C0 controls but
    tab and newline (so a carriage return too), DEL, C1, zero-width characters, line and paragraph separators,
    direction marks, bidirectional overrides and isolates, and the Unicode tag characters (U+E0000–E007F, invisible
    ASCII a model still reads) are removed, so a channel post or a transcript can't hide or reorder what a terminal
    or an agent's prompt shows. The workflow packet builder runs `clean_text` over every string of every fetched input
    and over an adapter exception's text in one place, whatever the adapter did; the adapter's obligation stands for
    every other reader (operations, `hz` output).
  - **Documents and Google Docs.** `Documents.read(ref)` and `replace_section(ref, marker, text)` fit Google Docs as
    they fit Confluence: the adapter exports the document as text and locates the marker in the document's body for
    the write. A `search(query, window)` read is not added: no 0.2 workflow needs it, and adding a read method later
    goes through the deprecation cycle above, so it waits for a workflow that does.
  - **Mail is deferred.** No 0.2 workflow reads mail: newsletters are outputs, not inputs, and a Gmail read scope is
    the most sensitive one a Google adapter could ask for (Google restricts it and requires its own review). A `mail`
    capability comes with the first workflow that needs it, likely `send` as its first method.
- **Two new let kinds.** `ProviderLet` (`@module.provides("tracker", modes={"read"}, hosts=[...])` on an adapter
  class, named `<adapter>:<capability>`) can't be a `ServiceLet`: several adapters provide the same protocol and
  configuration picks one, while a service type has exactly one provider. `RequirementLet`
  (`Module(..., requires=[...])`, one per capability) makes a module's needs visible before any code runs, like
  `CredentialLet` ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)). Both are consumed by the kernel (binding,
  injection) and by `system.capability` (`hz system capability list`, the `hz system info` section and the doctor
  check). Operations and jobs declare their own needs with `requires=[...]`, stored on the `OperationLet` and
  `JobLet`; they are not lets of their own because they belong to one function. A function's pin must agree with its
  module's pin for the same capability, and one function can't act for two requesters.
- **Injection by type, per requester.** An operation asks for `documents: Documents` like for a service
  ([ADR 0005](0005-dependency-injection-by-type.md)); heads hide the parameter. The kernel maps every operation, job,
  service factory, adapter factory, info and health provider to the requester it acts for, and hands out a handle that
  exposes only the protocol's methods, in the modes granted, as wrapper functions: it has no attribute holding the
  adapter and no bound methods. Asking for an undeclared capability, or from a function no module declared, fails
  with a `HeinzelError` that says what to declare. `Kernel.invoke` validates the caller's arguments before injecting
  anything, so invalid input never builds an adapter.
- **Kernel-internal resolution.** `Capabilities` (which hands out handles) and its matrix rows stay in the kernel's
  private modules; modules get a `CapabilityView` (bindings, adapters, matrix, fixes) and no way to ask for a handle
  besides declaring a requirement. `Capabilities.handle_for(requester, capability)` is the seam for code that acts on
  a requester's behalf without being its function, such as the compiler of workflow contracts
  ([ADR 0021](0021-workflow-contracts.md)). The workflow module reaches it through `Kernel.handle_for(requester,
  capability)` (2026-10-24), which serves only requesters of kind `job` of the enabled module providing
  `CompiledJobs`, refuses publishing requesters and adapter pins, and so hands out read handles only; compiled jobs
  appear in the matrix like declared ones. The module check guards against mistakes (a module asking on another's
  behalf by accident), not against plugins: plugins are trusted code that runs with the user's rights
  ([ADR 0020](0020-daemon-head.md)), and like the rest of this design the check is no isolation boundary (below). `hz workflow run` is `read_only=False` (it spends money and writes a local
  draft) without declaring any `write` capability: the two are separate.
- **Write only through publish operations, declared there.** Until approvals exist, "approved publish operation" is
  enforced structurally: `write` is granted only to operations with `read_only=False` that declared it themselves.
  `Module(requires=...)` refuses `write`, and so do read-only operations and jobs, when the module is imported;
  services, info and health providers only ever get read handles, so a write can't hide behind a shared service.
  `write` does not imply `read`: a publish operation that also reads declares `documents:read,write` (or reads through
  its module's `documents:read`). Approval gating of those operations comes with `hz inbox` ([ADR 0020](0020-daemon-head.md)).
- **Binding is configuration, never discovery, and never the project's.** `[capabilities] tracker = "github"` in the
  site, user or `HZ_CONFIG` file, or `HZ_CAPABILITIES__TRACKER`; keys take `_` or `-`. A project `hz.toml` can't
  bind (settings classes list such fields in `ignored_in_project`): a repository someone else controls must not decide
  where data goes; doctor names any key it sets, never the value. A sole installed adapter is not bound automatically
  (principle 1: declared beats discovered); doctor names the binding to write and the installed candidates. The only
  default is `agent_runner = "claude-code"`, because the other core runner, `command`, needs a command line first;
  doctor names the alternative when Claude Code is missing. A pin (`acme:documents:read`) uses that adapter whatever
  is bound, and the matrix shows it. Two adapters with one name for one capability make that binding ambiguous: it is
  refused, naming both modules and distributions.
- **Reserved names.** Modules can't be named `hz` or `capabilities`, the kernel's configuration tables, so the
  `[capabilities]` table stays flat.
- **Doctor and info.** `hz system doctor` reports each unmet requirement as degraded (`tracker:read: no adapter
  bound`, an adapter that is not installed or is ambiguous, or one that lacks a mode), each binding to an adapter
  nobody provides and each `[capabilities]` key that names no capability. `hz system info` shows requester →
  capability:modes → adapter → hosts; hosts are what the adapter declares, `unknown (configured)` when its
  configuration decides and it doesn't say how (next bullet).
- **Hosts that settings decide** (director's decision, 2026-10-24). An adapter whose settings decide where data goes,
  such as a code host adapter whose `api_url` may name a self-hosted server, passes a function instead of a list:
  `@module.provides("code-host", modes={"read"}, hosts=github_hosts)` with
  `def github_hosts(settings: GithubSettings) -> list[str]: return [settings.api_url]`. A static list stays valid,
  so the change is additive ([ADR 0026](0026-plugin-api-versioned-with-semver.md)); the function is stored on
  `ProviderLet.configured_hosts`, and `ProviderLet.hosts` stays a tuple.
  - *Why a function, not `hosts_from="<setting name>"`*: settings are already injected by type
    ([ADR 0005](0005-dependency-injection-by-type.md)), so the function is type-checked in the plugin, says which
    settings class it reads when a module has several, and can return more than one host (an API and an upload
    host). A setting name is a string checked only at run time, against a class the kernel would have to guess, and
    covers exactly one URL field.
  - *Safe by construction, as far as declarations go*: the function may take only `ModuleSettings` subclasses
    (checked when the module is imported) of its own module (checked when the kernel registers the adapter; a plugin
    adapter that reads another module's settings is skipped and reported). The kernel resolves those settings itself
    instead of calling it through the injector, so it never gets `Credentials`, services, capability handles or the
    kernel, and has nothing to reach the network with. It must be cheap and do no I/O; like the rest of this design
    that is review's job, not an isolation boundary.
  - *Effective settings*: the function gets the same settings instance the adapter's factory gets, so the hosts
    shown are where data actually goes. Whether a project `hz.toml` can steer them depends on the adapter listing
    the field in `ignored_in_project`, which it should; then doctor names the project key, as for bindings.
  - *Host names only*: each value may be a host name or a URL; the kernel keeps only the lowercase host name (no
    scheme, user information, port, path or query), so a token in a URL never reaches `hz system info`. ASCII host
    names (punycode for international ones) and IP addresses are valid; a static list is held to the same rule when
    the module is imported.
  - *When the matrix is built*, once per kernel (the settings it reads are resolved once per kernel too). It never
    raises into `hz system capability list`, info or doctor: if the settings can't be read, the function fails or
    returns something that isn't a host, or returns nothing, the hosts show as `unknown (configured)` and doctor adds
    a degraded `hosts of <adapter>:<capability>` check. The check names the exception type and the position of a bad
    value, never the value or the exception's message, which may carry a URL with a token; the log has the traceback.
- **Not an isolation boundary.** Every module and plugin runs in the same process with the user's rights. A plugin can
  import an adapter's module and build it, reach the adapter through a handle's closures with introspection, or call
  the kernel's private modules. Handles and declarations make intent explicit and misuse visible in review, as
  `Credentials` does ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)); they don't stop a malicious plugin.
  Only install plugins you trust ([SECURITY.md](../../SECURITY.md)).
- **Not yet:** per-person and per-job binding overrides beyond the layered files, OAuth scopes and credentials per
  mode (adapters use `Credentials` as any module does), doctor checks that call adapters, plugin-defined
  capabilities, the generic MCP adapter, and contract tests for adapters. No vendor adapter ships in core; the tests
  use test-only adapters.

- [ADR 0022](0022-embedded-workflows.md) describes `DocumentConnector` plugins (`heinzel-google`,
  `heinzel-atlassian`); under this ADR they are adapters of the documents and spreadsheets capabilities, and
  `DocumentConnector` becomes part of the documents capability protocol. This answers ADR 0022's open question
  "connector protocol or let kind" in favour of a protocol.
- [ADR 0023](0023-inventory.md) lets plugins contribute discovered inventory entries; they would do so through the
  same adapters.
- [ADR 0021](0021-workflow-contracts.md) contract `inputs`, `outputs` and `publishes` would name capabilities rather
  than vendors.
- Agent runners follow the same pattern: [ADR 0029](0029-agent-runners-are-capability-adapters.md).
