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

Not yet: proposed. When implemented: a shared contract-test suite per capability that every adapter runs, an
import-linter contract that workflows and core modules do not import adapter plugins, `doctor` checks for every
binding and every declared dependency, and a test that a plugin cannot use a capability or mode it didn't declare.

## More Information

- The dependency model with read and write modes was proposed by the maintainer on 2026-10-23.

**What 0.2 implements.**

The status stays proposed: the generic MCP adapter, the first vendor adapters, approvals and the shared adapter
contract-test suite are not built yet. Built, with the choices made while building it (director's decisions,
2026-10-24):

- **Protocols** (`heinzel.kernel._protocols`, public in `heinzel`): only what the first workflows need. `CodeHost`
  (`merged_pull_requests`, `issues` in a `Window`), `Tracker` (`issues_updated`), `Documents` (`read`;
  `replace_section` as the only write). Results are pydantic models with described fields. No separate protocol
  version number: the protocols are public API and follow the core's SemVer ([ADR 0026](0026-plugin-api-versioned-with-semver.md)),
  which rejected a second API level number for the same reason.
- **Each method has a mode** in the capability registry (`CAPABILITIES`); a unit test keeps the registry and the
  protocols in step. Capability names in requirements and bindings are `code-host`, `tracker` and `documents`; the
  set is closed in 0.2 (plugins cannot add capabilities yet).
- **Two new let kinds.** `ProviderLet` (`@module.provides("tracker", modes={"read"}, hosts=[...])` on an adapter
  class, named `<adapter>:<capability>`) can't be a `ServiceLet`: several adapters provide the same protocol and
  configuration picks one, while a service type has exactly one provider. `RequirementLet`
  (`Module(..., requires=[...])`, one per capability) makes a module's needs visible before any code runs, like
  `CredentialLet` ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)). Both are consumed by the kernel (binding,
  injection) and by `system.capability` (`hz system capability list`, the `hz system info` section and the doctor
  check). Operations and jobs declare their own needs with `requires=[...]`, stored on the `OperationLet` and
  `JobLet`; they are not lets of their own because they belong to one function.
- **Injection by type, per requester.** An operation asks for `documents: Documents` like for a service
  ([ADR 0005](0005-dependency-injection-by-type.md)); heads hide the parameter. The kernel maps every operation, job,
  service factory, adapter factory, info and health provider to the requester it acts for, and hands out a handle on
  the bound adapter that exposes only the protocol's methods, in the modes granted. Asking for an undeclared
  capability, or from a function no module declared, fails with a `HeinzelError` that says what to declare.
- **Write only through publish operations.** Until approvals exist, "approved publish operation" is enforced
  structurally: `write` is granted only to operations with `read_only=False` that declared it, themselves or through
  their module. Declaring `write` on a read-only operation or a job is refused when the module is imported; services,
  info and health providers only ever get read handles, so a write can't hide behind a shared service. Approval gating
  of those operations comes with `hz inbox` ([ADR 0020](0020-daemon-head.md)).
- **Binding is configuration, never discovery.** `[capabilities] tracker = "github"` in the user or team (`hz.toml`)
  file, or `HZ_CAPABILITIES__TRACKER`; keys take `_` or `-`. A sole installed adapter is not bound automatically
  (principle 1: declared beats discovered); doctor names the binding to write and the installed candidates. A pin
  (`acme:documents:read`) uses that adapter whatever is bound, and the matrix shows it.
- **Doctor and info.** `hz system doctor` reports each unmet requirement as degraded (`tracker:read: no adapter
  bound`, an adapter that is not installed, or one that lacks a mode) and each binding to an adapter nobody provides.
  `hz system info` shows requester → capability:modes → adapter → hosts; hosts are what the adapter declares.
- **Not yet:** per-person and per-job binding overrides beyond the layered files, OAuth scopes and credentials per
  mode (adapters use `Credentials` as any module does), doctor checks that call adapters, plugin-defined
  capabilities, the generic MCP adapter, and contract tests for adapters. No vendor adapter ships in core; the tests
  use test-only adapters.
- Import-linter: "The core imports no vendor client or agent SDK".

- [ADR 0022](0022-embedded-workflows.md) describes `DocumentConnector` plugins (`heinzel-google`,
  `heinzel-atlassian`); under this ADR they are adapters of the documents and spreadsheets capabilities, and
  `DocumentConnector` becomes part of the documents capability protocol. This answers ADR 0022's open question
  "connector protocol or let kind" in favour of a protocol.
- [ADR 0023](0023-inventory.md) lets plugins contribute discovered inventory entries; they would do so through the
  same adapters.
- [ADR 0021](0021-workflow-contracts.md) contract `inputs`, `outputs` and `publishes` would name capabilities rather
  than vendors.
- Agent runners follow the same pattern: [ADR 0029](0029-agent-runners-are-capability-adapters.md).
