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
  (documents, spreadsheets, calendar, mail) and Zoom (meetings and transcripts).

### Consequences

- Good: workflows are portable between teams and vendors; switching a vendor is a configuration change.
- Good: companies reuse the MCP servers and gateways they already run.
- Bad: the protocols are the lowest common denominator; vendor-specific features need protocol extensions or stay in
  the vendor's plugin as ordinary operations.
- Bad: new work: the capability protocols, the binding configuration and its doctor checks, the generic MCP adapter,
  and contract tests every adapter must pass.

### Confirmation

Partly, for the part built in 0.2 (see the implementation status under More Information): a requester can't use a
capability or mode it didn't declare, through every head (`tests/unit/kernel/bindings_test.py`,
`tests/integration/capability_test.py`); requirement strings and the registry stay in step with the protocols
(`tests/unit/kernel/capabilities_test.py`); declarations are checked when a module is imported
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
  only write). Issues belong to `tracker` alone, also when they live on the code host: a GitHub adapter provides both
  capabilities. Results are pydantic models with described fields. No separate protocol version number: the protocols
  are public API and follow the core's SemVer ([ADR 0026](0026-plugin-api-versioned-with-semver.md)), which rejected
  a second API level number for the same reason.
- **Growing a protocol.** Adding a method in a mode a capability already has breaks every adapter serving that mode,
  so it goes through ADR 0026's deprecation cycle; adding a method in a new mode is additive, because adapters declare
  the modes they serve and are only required to implement those modes' methods.
- **Each method has a mode** in the capability registry (`CAPABILITIES`); a unit test keeps the registry and the
  protocols in step. Capability names in requirements and bindings are `agent-runner`, `code-host`, `tracker` and
  `documents`; the set is closed in 0.2 (plugins cannot add capabilities yet). An adapter must implement the methods
  of the modes it declares, checked when the kernel registers an adapter class and when it builds one from a factory.
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
  capability)` (2026-10-24), which refuses publishing requesters, so it hands out read handles only. That widens the
  sentence above: a module that asks for the `Kernel` can build a `Requester` and get read handles for what that
  requester declares. Like the rest of this design, it makes intent explicit in code and review; it is not an
  isolation boundary (below).
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
  configuration decides.
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
