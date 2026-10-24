---
status: proposed
date: 2026-10-24
decision-makers: maintainer (direction), architect (evaluation and shape)
amends: 0029, 0050
related: 0018, 0028, 0030, 0037, 0039, 0041, 0044, 0045, 0049
---

# 0057: AI access is a module: one choice like the theme, options are adapters of `ai-access`

## Context and Problem Statement

hz reaches AI in more than one way. Today every run goes through the `agent-runner` capability: Claude Code headless
or a configured command ([ADR 0029](0029-agent-runners-are-capability-adapters.md)), and the chat runs on it
([ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md)). [ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md)
adds Pydantic AI for a Heinzelmännchen's turns, configured by `[heinzel] model`, `model_fast` and `base_url` with an API
key. Companies add a third way: a company CLI that grants employees access to AI, either by handing out an endpoint
and a short-lived credential for an OpenAI- or Anthropic-compatible API, or by wrapping a coding agent (made-up
example: `acme ai claude …`). Nothing in hz says which of these exist on a machine, whether each is signed in, which
one is used for what, or what it costs; the person has no single place to choose. The maintainer asked for an `ai`
module that owns this, selected the way a theme is ([ADR 0044](0044-themes-are-data-a-plugin-ships.md)): one visible
choice, a list of the installed options with their status, plugins contributing options.

## Decision Drivers

- One choice a newcomer can make in Settings → General, next to Theme; routing per use is an expert refinement.
- Tenet 8, model-vendor and agent agnostic: an option is how hz *reaches* AI, never a vendor requirement.
- Companies roll out their gateway by data or a plugin, without a core release; the core ships the generic shape.
- The kernel stays small: protocols and one injection seam, no access logic.
- Status is cheap and spends nothing; a live check is explicit and says what it cost.
- No behaviour change for the chat in the first slice.

## Considered Options

- **Options are adapters of a new `ai-access` capability; the choice is its binding; the `ai` module lists, tests and
  selects them and composes `agent-runner`** (chosen)
- **A new let kind, like `ThemeLet`**: a theme is inert data, an access option has behaviour (status, a live test, an
  endpoint), credentials and hosts, which is what `ProviderLet` already models with hosts, the capability matrix,
  doctor and ambiguity checks. A second mechanism for the same shape would duplicate all of it.
- **Fold `agent-runner` into `ai-access`** (the coding agent as a facet of the access option): it forces agent-only
  plugins (a Codex adapter) to become access options and bends `AgentRunner.run`, which is about one bounded session,
  into a protocol about access. Both stay one-purpose and the access option names its runner instead.
- **A separate `[ai] access` key next to the binding**: the maintainer's first sketch. Two keys for one choice drift,
  and the binding already has what the choice needs: user scope only (a project `hz.toml` can't set it), unknown
  bindings reported by doctor, and a company playbook may recommend it ([ADR 0037](0037-plugins-bring-playbooks.md)),
  so a company layer suggests its gateway for free. The configuration key is `[capabilities] ai_access`; the words
  people read are "AI access".

## Decision Outcome

Chosen option (proposed): **AI access options are adapters of the `ai-access` capability; `[capabilities] ai_access`
is the one choice, made with `hz ai access use`; the built-in `ai` module lists every installed option with its
status, tests one on request, and keeps `agent-runner` as the runner an option names.** Checkable statements:

1. **Terminology.** *AI access* is how hz reaches AI; its *options* are Claude Code, Pydantic AI with an API key, a
   company gateway. *Model* is the model; *vendor* (Anthropic, OpenAI) only where the vendor matters. "Provider" is not
   used for access options, because it means a model vendor. The capability is `ai-access`, the module `ai`, its
   submodule `ai.access`, the commands `hz ai access list|use|test`, the web label "AI access".
2. **Kernel: a protocol and one seam.** `AiAccess` (`status() -> AccessStatus`, `test() -> AccessTest`, both mode
   `read`) and its models are in `heinzel.kernel._ai`, public in `heinzel`. `status()` runs no model and spends
   nothing: a CLI on PATH, a login the CLI reports offline, a credential present. `test()` is the one live call, as
   small as the option can make it, and reports what it cost. `Adapters[P]`, injected by type, is a read-only mapping
   from adapter name to a handle on every installed adapter of `P`'s capability, for the module that owns choosing
   among them; it is granted only to a requester that declared the capability (`ai-access:read`), in read modes, built
   lazily like any handle. Nothing else in the kernel changes.
3. **The choice is the binding, like the theme.** `[capabilities] ai_access` defaults to `claude-code`. `hz ai access
   list` shows every installed option with its title, summary, status (`signed in`, `not signed in`, `not tested`,
   `tested`, `test failed`), the hosts it reaches, where it comes from and an *in use* marker, then what each use runs
   today. `hz ai access use NAME` writes `capabilities.ai_access` to the user file through `ConfigEditor`, as `hz system
   theme use` writes `system.theme.name`, and, when the option names an agent runner, `capabilities.agent_runner` too,
   so the chat follows the choice. `hz ai access test [--name NAME]` runs the live check and keeps its result (time, outcome,
   model, cost) in `<state_dir>/ai/tests.json`, which status reads; none is ever run implicitly.
4. **Options, from core and plugins.** `claude-code` is provided by `system.runner` beside its runner (one module, two
   capabilities, one name): signed in from `claude auth status`, tested by a one-line `claude -p` within a small cap;
   it names the runner `claude-code`. `gateway` is the core's generic shape in `ai.access`, configured by data in
   `[ai.access]`: a title, a status command (exit 0 and optional JSON `{"signed_in": …, "models": […]}`), a test
   command, the hosts it reaches, the variables it may see, a sign-in hint and the agent runner that serves it (e.g.
   `command`, with `[system.runner] command = ["acme", "ai", "claude", …]`). Commands are argument lists without a
   shell, with an environment allowlist and no `HZ_CREDENTIAL_*`, and come from the site, user or `HZ_CONFIG` file or
   the environment only, never from a project `hz.toml`, as the command runner's do. A company ships either a playbook
   that recommends the binding and these values, or a plugin with its own option (`@module.provides("ai-access",
   name="acme")`) and, if it wraps a CLI agent, its own `agent-runner` adapter. `pydantic-ai` is in `ai.access`: a model
   API through Pydantic AI, the model named in `[ai.access] model` as Pydantic AI names it (`anthropic:...`), signed in
   when a key is there, tested by one request capped at a few output tokens. `pydantic-ai-slim` becomes a core
   dependency now, as ADR 0050 decided; the vendors' clients are the extras `heinzel[anthropic]` and `heinzel[openai]`,
   and status names the missing one. The core imports only `pydantic_ai`, never a vendor SDK, and turns its first-run
   banner off, because stdout may be the MCP head's protocol.
5. **Uses and routing.** Three uses: *chat* ([ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md)),
   *turns* of Heinzelmännchen ([ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md)) and
   *coding handoffs* ([ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) §6). Every use follows
   the one choice. An expert may route a use elsewhere in `[ai.roles]` (`chat`, `turns`, `coding`, each an option
   name; empty follows the choice), and models by class per option in `[ai.models.<option>]` (`fast`, `standard`,
   `best`), resolved use → option → model. This **amends ADR 0050 §5**: `[heinzel] model`, `model_fast` and
   `base_url` retire before they are built; an option's endpoint (base URL, API flavour, credential) comes from the
   option, never from a global, and a contract's `agent.model_class` maps through `[ai.models]`. Routing is built
   with the turn engine; until then the listing says what each use runs today (chat and coding: the bound agent
   runner; turns: not built).
6. **Agent runners stay, composed.** This **amends ADR 0029**: `agent-runner` is unchanged and remains the seam for
   bounded agent sessions and handoffs; an access option names the runner that serves its chat and coding uses
   (`AccessStatus.runner`), `use` binds it, and the listing warns when the bound runner isn't the chosen option's.
7. **Credentials.** A model API key is the credential `ai.access/api-key`
   ([ADR 0030](0030-credentials-live-in-the-os-keychain.md); `hz system credential set ai.access api-key`), or the
   vendor's own variable (`ANTHROPIC_API_KEY`); a failed test's message never repeats it. A gateway's short-lived token is never stored in the keychain, configuration, receipts or
   messages: the option mints it from its command when a run needs it and holds it in memory until it expires.
8. **Cost.** Budgets stay per run (`Budget.max_usd`, the chat's and turns' caps). A runner reports its cost
   (`AgentResult`); a model API run is priced with `[ai.prices]` (per model id, shipped defaults overridable; a model
   without a price counts at the cap, as ADR 0050 says). A test reports its cost like any run.
9. **Heads.** `list` is on the CLI, MCP and web heads, never the unauthenticated REST head, since it names hosts and
   sign-in state ([ADR 0039](0039-a-web-head-a-react-app-served-by-the-api-head.md)). `use` and `test` change
   configuration or spend money and are the person's own acts: CLI and web only, so an agent on MCP can't move work
   off a company gateway. Bindings are read at boot: after `use`, a running `hz ui` or MCP server says to restart. The web app shows "AI access" in Settings → General next to
   Theme: the options with status and *in use*, a test button and a picker; routing under expert mode
   ([ADR 0041](0041-expert-mode-newcomers-see-the-everyday-commands.md)). `ai` is a top-level module by the maintainer's
   decision: it is a user-facing choice like `chat`, not administration ([ADR 0018](0018-one-administrative-namespace.md)).
10. **Doctor.** The `ai` health check: the chosen option is installed and signed in, as far as status can tell
    offline; the bound agent runner is the one it names. `system.runner` keeps its runner checks.
11. **Model, effort and honest cost per answer.** `AgentRunner.run` takes an optional `model` and `effort` (`low`,
    `medium`, `high`, `xhigh`, `max`) that override the configured ones for one run; a runner that can't pass one
    says so in its warnings. `hz ai models` lists the models and efforts of the AI access in use (`AccessStatus`
    gains `efforts` and `billing`) for the chat composer's picker; a chat keeps its choice for its later messages.
    Every answer keeps when it was made, how long it took, the AI access, model, effort, tokens and cost, shown
    behind an (i). `billing` is `plan` when the agent ran on a subscription (Claude Code's `apiKeySource` is `none`,
    its `authMethod` `claude.ai`): its cost is what the run would cost at API prices, which the plan covers, and is
    labelled "uses your plan · ≈ $X at API prices" wherever it shows; `api` is real spend. A run without file tools
    (a chat, a question, a test) is lean: its contract replaces Claude Code's own system prompt, and a chat's agent
    gets the everyday operations as tools (`[system.runner.chat] tools = "all"` for every one), since every tool's
    schema goes with every message.

### Consequences

- Good: one place, one word and one choice for how hz reaches AI, in the CLI, MCP and soon the web app; a company
  rolls out its gateway with a playbook or a small plugin.
- Good: the capability matrix, hosts, doctor and ambiguity checks apply to access options for free.
- Good: the chat follows the choice now, without a change to `system.runner`'s chat.
- Bad: a new kernel seam (`Adapters[P]`) that hands a module handles on adapters nobody bound; it is read-only and
  granted by declaration, and like every handle no isolation boundary (ADR 0028).
- Bad: two capabilities to keep aligned (`ai_access` and `agent_runner`); `use` writes both and the listing and
  doctor say when they differ, for instance after a playbook recommended only the access.
- Bad: routing and models by class are designed, not built, until the turn engine exists.

### Confirmation

- `tests/unit/kernel/adapters_test.py`: `Adapters[P]` holds every installed adapter, read modes only, built lazily,
  refused without a declaration.
- `tests/unit/modules/ai_access_test.py`: listing, status labels, `use` writing both bindings, the kept test result,
  the gateway's command handling (no shell, environment allowlist, bounded output and time), the `pydantic-ai`
  option's status and a test turn through `TestModel` and `FunctionModel`, the key kept out of a failure.
- `tests/integration/ai_access_test.py`: `hz ai access list|use|test` through the CLI head with fake `claude` and
  gateway CLIs on PATH; the operations are absent from the REST head.
- `tests/unit/public_api_test.py`: `AiAccess`, `AccessStatus`, `AccessTest` and `Adapters` are public.

## More Information

- Builds on [ADR 0028](0028-integrations-are-capability-adapters.md) (adapters, bindings, hosts) and
  [ADR 0044](0044-themes-are-data-a-plugin-ships.md) (the choice and its listing).
- First slice: the protocol, `Adapters[P]`, the `claude-code`, `gateway` and `pydantic-ai` options with the
  `pydantic-ai-slim` dependency, `list`, `use`, `test`, doctor. Deferred: the turn engine itself (ADR 0050),
  `[ai.roles]`, `[ai.models]`, `[ai.prices]`, gateway endpoints and tokens, and the web Settings section.
- Open: whether `ai.access` options should also offer the endpoint (`endpoint()`) as a third protocol method or a
  separate capability once Pydantic AI consumes it; the recommendation is a method returning `None` for options
  without one.
