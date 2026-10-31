---
status: accepted
amended-by: 0084
date: 2026-10-24
decision-makers: maintainer
supersedes: 0033, 0034
related: 0015, 0017, 0027, 0028, 0029, 0030, 0032, 0033, 0034
---

# 0035: Integrations go through the vendor's CLI as an ordinary employee

## Context and Problem Statement

Adapters implement capability protocols over any transport ([ADR 0028](0028-integrations-are-capability-adapters.md)).
The first two, GitHub and Atlassian, speak HTTP with a token the person creates by hand and stores through
`Credentials` ([ADR 0030](0030-credentials-live-in-the-os-keychain.md); [ADR 0033](0033-github-rest-api-over-urllib.md)
and [ADR 0034](0034-atlassian-cloud-rest-apis-over-urllib.md) record how); the Google Workspace adapter would need an
OAuth client, which in a company only an administrator may register, and a Zoom adapter an app registration with its
own review. Every such step is a form, a ticket or an approval before hz drafts anything, and every token is a secret
hz must store, redact and rotate. Meanwhile the vendors ship command-line tools an ordinary employee may install and
log in to with their own account: `gh` for GitHub, `acli` for Atlassian, `gws` for Google Workspace. The decision:
"Integrations use only what an ordinary employee may do. No admin-registered apps; vendor CLIs
(gh, acli, gws) are the transport and own authentication. Meetings come in as Zoom summary emails." How do adapters
reach a service, and who holds the credential?

## Decision Drivers

- Perfect onboarding (INTENT.md, tenet 11): joining needs nothing an employee cannot do alone, and nothing an
  administrator must register or approve.
- Least privilege by construction: a CLI logged in as the person can do exactly what the person may do, no more, and
  the vendor's own consent screen said so.
- Fewer secrets in hz: a token hz never holds cannot leak through output, logs, receipts or a bug report
  ([ADR 0027](0027-nothing-is-sent-home.md), [ADR 0030](0030-credentials-live-in-the-os-keychain.md)).
- The vendors maintain login flows, token refresh, enterprise hosts and rate-limit handling in their CLIs; hz should
  not reimplement them per adapter.
- Tenets 8 and 9: no vendor lock-in and specifics as plugins; a company that reaches vendors differently binds its own
  providers.

## Considered Options

- **Vendor CLIs as the transport; the CLI owns authentication; no admin-registered apps** (chosen)
- **HTTP with a personal access token per service** (the GitHub and Atlassian adapters today,
  [ADR 0033](0033-github-rest-api-over-urllib.md) and [ADR 0034](0034-atlassian-cloud-rest-apis-over-urllib.md), which
  weighed `gh` as "an external program to install and authenticate"): works without an administrator for these two, but the person creates and pastes a token, hz stores and redacts it, Google and Zoom
  need registered apps anyway, and each adapter carries its own HTTP client, paging and error handling.
- **OAuth apps registered by the project** (one hz client per vendor): the convenience of a browser login, but a
  company administrator must allow the app, Google's verification review applies to a restricted scope, and hz would
  hold refresh tokens for every service.
- **MCP servers only**: the vendors' hosted MCP servers also need an app or a gateway an administrator sets up;
  [ADR 0028](0028-integrations-are-capability-adapters.md)'s generic MCP adapter stays for companies that run one.
- **Vendor SDKs**: the import-linter contract keeps vendor clients out of the core, and a plugin's SDK still needs a
  token or an app; nothing gained over the CLI.
- **A hz service account or bot user**: an administrator creates it, it sees more than any one person, and its token
  is the most valuable secret on the machine. Against least privilege and tenet 4 (humans own, with lineage).

## Decision Outcome

Chosen option (proposed): **where a vendor ships a CLI an ordinary employee may install and log in to, the adapter
uses it as its transport and the CLI owns authentication; hz stores no token for that service and needs no registered
app; `Credentials` stays for services without such a CLI.** Checkable statements:

1. **The CLI is the transport and owns authentication.** An adapter for GitHub runs `gh`, for Atlassian `acli`, for
   Google Workspace `gws`. It never reads the CLI's token store, never calls a subcommand that prints a token, and
   declares no `CredentialLet` for that service. Logging in is the vendor's flow (`gh auth login`, `acli ... auth
   login`, `gws auth login`), run by the person once; the adapter's setup step
   ([ADR 0032](0032-modules-declare-interactive-setup-steps.md)) says which command and checks the result, nothing
   more. Whatever the vendor requires of an ordinary account for that login is the vendor's business and the adapter
   README's; hz adds no requirement of its own.
2. **Only what an ordinary employee may do.** No adapter in the core or a first-party plugin requires an
   administrator to register an application, create an OAuth client, grant an organisation-wide scope or create a
   service account. An adapter that can only work that way is not a first-party adapter; a company's private plugin
   may ship it.
   One case is allowed: a private workspace app that a person creates and installs for themselves, in a workspace
   they may add apps to, so that Slack can carry two-way approvals (buttons need an app; a CLI cannot receive a
   click). It belongs to its owner, only notifies and asks in the owner's own direct message, never acts as the person,
   and is not a Heinzelmännchen-hosted or shared app. If the workspace requires an administrator to approve it, the
   plugin says so and does not work around the setting.
3. **Invocation is bounded and shell-free.** The core publishes the bounded subprocess helper the agent runners
   already use ([ADR 0029](0029-agent-runners-are-capability-adapters.md): argument lists without a shell, a process
   group, SIGTERM then SIGKILL at the timeout, capped and drained stdout and stderr) for adapters. An adapter passes
   an argument list, never a string; resolves the executable to an absolute path from its settings
   (`[github] gh = "gh"`, looked up on PATH once per kernel, never from a project `hz.toml`); gives every call a
   timeout (default 60 s, settable, at most the contract's); caps stdout (10 MiB by default) and keeps the last
   64 KiB of stderr; closes stdin or passes exactly the bytes it means to; and runs with an environment allowlist
   (PATH, HOME, LANG, LC_*, TMPDIR, the CLI's own configuration variables the adapter names), never `HZ_CREDENTIAL_*`
   and never the inherited environment whole.
4. **Output is JSON, parsed into models.** Every call asks the CLI for machine-readable output (`gh --json`,
   `acli --json`, `gws` JSON) and parses it into the pydantic models of the capability; unknown fields are
   ignored, every string passes `clean_text` ([ADR 0028](0028-integrations-are-capability-adapters.md)). Human-readable
   output is never parsed. Paging uses the CLI's own paging or limit flags until the `Window` is covered or the byte
   budget is passed, then raises the "narrow the window" error of ADR 0028.
5. **Errors are mapped, never relayed raw.** A non-zero exit becomes a `HeinzelError` that names the operation, the
   exit code and what to do: not logged in or expired → the login command, reported through `attention` as a
   `degraded` check like a missing credential ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)); not found or
   forbidden → the resource, never the account; rate limited → when to retry; a timeout → the call and its limit;
   missing executable → the install command. The cleaned stderr tail may be logged at debug level only; it is never
   put in a draft, a receipt or `hz system info`.
6. **Versions are checked, offline.** An adapter declares the minimum CLI version it was written against. `hz system
   doctor` runs `<cli> --version` and nothing that reaches the network: missing or too old is `degraded` with the
   install or upgrade command; the CLI's local account record (`gh auth status`'s hosts file, `gws`'s stored account)
   is reported where the CLI exposes it without a request, otherwise doctor says "installed, login checked on first
   use". Whether a login still works is learned by the first call and reported through `attention` (5). An adapter
   refuses to run a call against a CLI older than its minimum, naming the version.
7. **Hosts come from the CLI's configuration.** The adapter's `hosts` function
   ([ADR 0028](0028-integrations-are-capability-adapters.md)) returns the host the CLI will talk to: `gh`'s host
   setting (github.com or an Enterprise Server), the Atlassian site URL, Google's fixed hosts. `hz system info` shows
   them as for every adapter.
8. **`Credentials` stays for services without a CLI** ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)), and
   for a CLI that itself needs a token in a variable (named in the adapter's settings, filled from a declared
   `CredentialLet` for that one call, never inherited). A service that has neither a CLI nor a token an employee may
   create is out of scope for first-party adapters (2).
9. **Capabilities, providers and bindings are unchanged** ([ADR 0028](0028-integrations-are-capability-adapters.md)).
   The CLI is one transport behind a `ProviderLet`; contracts still name capabilities, configuration still binds
   them, `write` still reaches only publish operations. A company's private plugin may bind other providers for the
   same capabilities: its own wrapper CLI, an MCP gateway through the generic MCP adapter, or an HTTP adapter with a
   token it is allowed to hold. Such a plugin is typically the company layer of the record "layers and playbooks are
   plugins", installed with the team's stack; nothing here limits what a plugin may do with the user's rights, only
   what the core and first-party plugins require.
10. **Not an isolation boundary.** A CLI logged in as the person can do whatever the person may do; the adapter
    calls read subcommands for `read` modes and the kernel grants `write` only to publish operations, which is review's
    job to keep true, as for every adapter ([ADR 0028](0028-integrations-are-capability-adapters.md),
    [ADR 0030](0030-credentials-live-in-the-os-keychain.md)). Only install plugins you trust
    ([SECURITY.md](../../SECURITY.md)).

### Consequences

- This record supersedes [ADR 0033](0033-github-rest-api-over-urllib.md) and
  [ADR 0034](0034-atlassian-cloud-rest-apis-over-urllib.md) (urllib, tokens).
- The **GitHub adapter** moves from its HTTP transport and the `github-token` credential to `gh` (`gh api` with
  `--paginate` and `--jq`, or `gh pr list --json`, as each method needs); the **Atlassian adapter** from HTTP and an
  API token to `acli`. Their `CredentialLet`s go, their READMEs say "install and log in", `hz system doctor` checks
  the CLI, and their contract tests run against a fake CLI. The **Google Workspace adapter** starts on `gws`; no OAuth
  client of ours exists. **Zoom** gets no adapter and no app: meetings arrive as the meeting tool's summary e-mails
  through the mail capability, whose addition to ADR 0028 is in flight in a separate record; the `meetings` protocol
  stays for vendors with a CLI or a company plugin. **Chat** waits for a CLI an employee may use, or a company plugin.
- Good: joining a team needs no ticket; the first win runs with the logins the person already has; hz holds no
  service token for GitHub, Atlassian or Google, so nothing to redact, rotate or leak.
- Good: enterprise hosts, SSO, token refresh and rate limits are the CLIs' problems; one bounded-subprocess helper
  replaces per-adapter HTTP clients and their redirect and redaction rules.
- Bad: a CLI on every machine the daemon runs on, found through PATH from a launchd or systemd environment; doctor and
  the adapter's setup step say what to install, and the version a plugin was written against pins the output shape.
- Bad: output shapes are the CLIs', not the APIs'; a CLI release can change them, and the version check is the
  only guard. Recorded fake-CLI outputs in tests must be refreshed against the real CLI deliberately.
- Bad: CI cannot log in; unit tests use recorded outputs, integration tests a fake executable written in Python
  (as [ADR 0029](0029-agent-runners-are-capability-adapters.md)), and the real CLIs run only on a maintainer's machine.
- Bad: rework of two adapters built this week, before they shipped.

### Confirmation

Implemented for GitHub (`gh`) and Atlassian (`acli`): each CLI adapter's command
lines, JSON parsing, paging and error mapping against recorded outputs
(in heinzel-plugins: `plugins/heinzel-github/tests/unit/gh_test.py`, `plugins/heinzel-atlassian/tests/unit/acli_test.py`) and against a
fake CLI (in heinzel-plugins: `plugins/heinzel-github/tests/integration/gh_test.py`, `plugins/heinzel-atlassian/tests/integration/acli_test.py`).
Still to come with the shared helper and the Google Workspace adapter: the bounded subprocess helper's argument lists, timeouts, process group, output
caps and environment allowlist with real processes (`tests/integration/runner_process_test.py`, extended), and that an
adapter subprocess never sees `HZ_CREDENTIAL_*` or the inherited environment;
each CLI adapter's command lines, JSON parsing, paging, error mapping and version refusal against recorded outputs
(in heinzel-plugins: `plugins/heinzel-<name>/tests/unit/*_test.py`) and against a fake CLI (`plugins/heinzel-<name>/tests/integration`);
`hz system doctor` with the CLI missing, too old and present, without network (`tests/integration/capability_test.py`);
a repository test that no first-party adapter declares a `CredentialLet` for a service that has a CLI adapter, and
that no first-party plugin imports an HTTP client for such a service (`tests/unit/repository_test.py`); the
import-linter contract "The core imports no vendor client or agent SDK" (`pyproject.toml`), unchanged.

## More Information

- Integrations use only what an ordinary employee may do; vendor CLIs (gh, acli, gws) are the transport and own
  authentication; meetings come in as summary e-mails.
- Builds on [ADR 0028](0028-integrations-are-capability-adapters.md) (adapters over any transport, `hosts`,
  `clean_text`, the window rule), [ADR 0029](0029-agent-runners-are-capability-adapters.md) (the bounded subprocess the
  runners already have: process groups, timeouts, caps, environment allowlist), [ADR 0030](0030-credentials-live-in-the-os-keychain.md)
  (kept for services without a CLI; `attention` for an expired login), [ADR 0032](0032-modules-declare-interactive-setup-steps.md)
  (the adapter's setup step runs the vendor's login), [ADR 0015](0015-health-checks-as-lets.md) (doctor) and
  [ADR 0017](0017-first-party-plugins-in-the-core-repository.md) (the adapters are first-party plugins). It does not
  change ADR 0028's text; the mail capability is being added to ADR 0028 separately and is referenced, not decided,
  here.
- The onboarding record relies on this one: `hz join` discovers who the person is from the CLIs'
  own logins. The record "layers and playbooks are plugins" says where a company's own providers live: in the layers
  of its private plugin repository; the public reference company binds the first-party CLI adapters.
- The validation slice (INTENT.md, "Thin slice first"): the weekly report draft reads merged pull requests through
  `gh`, moved issues through `gh` or `acli`, and the week's meetings from summary e-mails through `gws`; its first
  three Fridays decide whether the CLIs' output shapes and login state are stable enough in practice.
- Open: whether the bounded subprocess helper is public API (`heinzel.run_bounded`) or stays in the kernel's private
  modules with a public wrapper; the exact minimum versions; whether `system.plugin`'s optional GitHub token
  ([ADR 0030](0030-credentials-live-in-the-os-keychain.md), known exception) also moves to `gh`. Revisit when a vendor
  retires its CLI's JSON output or a company needs a transport no employee may install.
