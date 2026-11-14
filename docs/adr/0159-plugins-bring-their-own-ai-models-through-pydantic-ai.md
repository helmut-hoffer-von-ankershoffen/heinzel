---
status: proposed
date: 2026-11-14
implementation: partial
decision-makers: maintainer (decision), architect (shape)
related: 0026, 0028, 0029, 0030, 0045, 0050, 0055, 0057, 0062, 0141, 0146
---

# 0159: Plugins bring their own AI models through Pydantic AI

## Context and Problem Statement

A company wants Heinzels to use the models it reaches through its own infrastructure: an internal endpoint or a
company proxy, with its own authentication (a short-lived token a helper prints), and a list of models from its own
database. Such a company ships a private plugin. Core can't see that plugin's code, so Core has to offer an interface
the plugin can implement, a test kit the plugin can run in its own CI, and a public example that does all of it with
made-up parts.

Before this record, a plugin could add an AI access option ([ADR 0057](0057-ai-access-is-a-module-one-choice-like-the-theme.md)) that
reports status and runs a test, but the chat, drafts and schedules ran only on Claude Code: `UserChatService`
refused every other option. Built-in options assumed OpenAI wherever a base address was set, and several paths named
`claude-code` or `pydantic-ai` outright. A real private plugin's team listed what blocked them; this record answers
each item.

## Decision Drivers

- The plugin owns its auth: hz never sees, stores or logs its token.
- The plugin never re-implements the agent loop: hz's own engine keeps its tools, limits, records and checks.
- Pydantic AI already has the extension point (`Model`, `Provider` with an HTTP client), so hz should use it rather
  than invent another.
- Every AI feature runs on whatever access is chosen. An option that can't serve a feature says so clearly instead of
  failing somewhere deep.
- A plugin's model list is incomplete and changes every few weeks. Core works with an id alone.
- A connector can't always do every method of its capability. Saying so is an answer, not a crash.
- Public helpers work on Python 3.12, which plugins target.

## Considered Options

1. **A capability whose adapter returns a ready Pydantic AI model** (chosen). The plugin builds
   `pydantic_ai.models.Model` with its own provider and HTTP client; hz runs it.
2. **A capability that runs whole agent sessions** (an `agent-runner` per plugin). Each plugin would re-implement
   tools, MCP, limits, usage and the continuity checks, and get them subtly wrong.
3. **Configuration only**: a base address, an API kind and a token command in `[ai.access]`. That covers a proxy that
   only needs a token, but not a model list from a database, availability probes, or another protocol. It is kept as
   a complement for the built-in option (statement 12).
4. **Pick "Pydantic AI", then a provider under it.** The owner first asked for this two-step choice, then decided
   against it: "plugin should be its own AI provider". Each plugin's models are one AI access option.

## Decision Outcome

Chosen option 1, with the built-in option extended as in option 3. Checkable statements:

### The capability

1. **`ai-models` is a kernel capability** with two read methods. `models() -> list[ModelOffer]` returns the
   offered models, the default first, fast, from the plugin's own cache. `model(model_id) -> pydantic_ai.models.Model`
   builds a ready model and never calls it. The plugin provides it with
   `module.provides("ai-models", modes={"read"}, hosts=[...], name="acme")`. Optional plain `title` and `summary`
   attributes on the adapter name its option. The protocol, `ModelOffer`, `ModelUnavailableError` and
   `NotSupportedError` are public API (`heinzel`, `heinzel.kernel`).
2. **`ModelOffer` needs only an id.** The id matches `^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$`, so it is safe in a
   command line, a log and a configuration value. Every other field is optional: `title`, `context_window`,
   `efforts`, `tools` (default true), `thinking` and `available`.
3. **Each `ai-models` adapter is an AI access option of the same name.** The kernel derives an `ai-access` adapter
   for it with the same hosts. `hz ai access list|use|test` and Settings show it next to Claude Code, the gateway and
   the Model API. A plugin that also provides `ai-access` under that name keeps its own. Choosing the option binds
   the `engine` agent runner when it is installed, for coding handoffs (statement 13); chat needs no runner.
   `ai-models` is never automatic, can't be bound in `[capabilities]`, and isn't a connector on the connector page,
   nor an input a contract can read.
4. **Status asks the cache, never a model.** The option's `status` calls `models()` on a thread with a 5 s timeout.
   When that fails or times out it shows the last good list with a note, and with none, an empty list and why. It
   reports `core_engine: true`, `billing: api`, the model in use, the models, and the ones marked unavailable.
   `test` builds the chosen model and sends one request capped at one request. A failure is a result, cleaned and
   cut to 300 characters.

### Choosing a model

5. **One model per option, in `[ai.access] plugin_model`**, with an optional `plugin_effort`. Unset, the first
   offer that calls tools and isn't marked unavailable is used. `hz ai models` and the chat's picker list every
   offer, with `available: false` on the unavailable ones.
6. **Unavailable is a clear refusal, never a crash.** A chosen model that isn't offered, didn't answer when last
   probed, or calls no tools raises `ModelUnavailableError` before the turn starts and before the model is built.
   The error names the models to pick instead and the command that picks one. An `available: false` model stays
   visible and pickable in Settings, marked unavailable. The plugin decides what `available` means and may probe it
   in its own `test` or background job, caching the result.
7. **Effort maps to the model's own style.** For `thinking: "adaptive"` hz passes Anthropic's
   `thinking: {type: adaptive}` with `output_config.effort` (Pydantic AI's `anthropic_effort`). For
   `thinking: "budget"` it passes `thinking: {type: enabled, budget_tokens}`, with 2k, 8k, 16k, 32k or 64k tokens
   from low to max. With `thinking` unset, hz passes nothing and the plugin's model keeps its own settings, so a
   plugin whose models speak another API maps effort itself.

### Running it

8. **Chat, drafts and schedules run on the plugin's model through hz's engine.** All three go through
   `UserChatService.dispatch`. When the AI access in use is a plugin's models, dispatch builds the model through
   the plugin and runs the same `InteractiveTurn` as for Claude Code, with the same tools, proposals, limits and
   journal. That path never runs Claude Code's checks: no executable digest, no `claude.ai` login check, no
   stripped subprocess environment.
9. **Continuity before every request.** A wrapper (`PluginTurnModel`) checks before each request, streamed or not,
   that the owner, access, plugin version, model, effort and budget mode are what the turn started with; otherwise
   the turn stops ("changed during this turn"). Approved schedules and drafts bind to a fingerprint of the same
   terms, so a plugin update or another model makes them ask again. `ScheduleBinding.access` is
   `plugin-models` for such schedules. The fingerprint terms for Claude Code are unchanged, so existing approvals
   stay valid.
10. **Billing and usage.** A plugin's models report tokens, not dollars. A turn is recorded with `billing: api`,
    `cost_usd: null`, and the tokens from Pydantic AI's per-response usage, under cause `chat` or `scheduled`. It
    counts as a turn of unknown cost. As for a subscription, a dollar-capped budget mode (`usd-cap`) refuses such a
    turn before the model is built, because no cap can be enforced. The usage report shows tokens where cost is
    unknown. `UsageCause` also takes a plugin's own namespaced causes, `<plugin>.<cause>` (e.g. `acme.probe`).

### The trust boundary

11. **What the plugin owns and what Core guarantees.** The plugin owns the endpoint, the authentication, fetching
    and refreshing tokens, its HTTP client and its proxy settings, the model list and its cache, and what
    `available` means. Core never asks for, stores, logs or shows a token or header. It holds the plugin's adapter
    only in trusted core code (`Kernel.ai_models`), never in a requester's handle. It records counts and ids only
    (never prompts, answers or headers) and keeps a plugin's failure text cleaned and short. It guarantees the
    engine, tools, approvals, limits, continuity checks and usage records, and that a slow or failing plugin can't
    stall a listing. A plugin must not put secrets into exception messages: Core can't know what to redact.

### Helpers for plugins and the built-in option

12. **`heinzel.token_auth.CommandTokenAuth`** is an `httpx2.Auth`. `httpx2` is the HTTP client Pydantic AI 2.61 and
    Anthropic's client use, and Core gets it through `pydantic-ai-slim`. It runs a helper command (an argument list,
    no shell, with a timeout) that prints a bearer token, and keeps the token in memory. It fetches a new one 5
    minutes before the JWT `exp` claim, and once more on a 401. Empty output means the helper's own sign-in expired:
    it raises `TokenCommandError` with the helper's stderr hint, and sends nothing. The token never appears in a
    log, an error or `repr`. `drop=["x-api-key"]` removes the placeholder key Anthropic's client insists on. It works
    on Python 3.12 and lives in its own module, so `import heinzel` doesn't load the HTTP client. The built-in
    `pydantic-ai` option gains `[ai.access] api` (`openai` or `anthropic`) and `token_command` to use it with a base
    address (built in a later head; see Implementation).
13. **Coding sessions.** The plugin team needs coding handoffs on their models too. An `engine` agent runner (hz's
    engine with read-only file tools confined to the working directory, plus the run's MCP servers) serves coding
    for options whose `status` names it. That runner is built in a separate head and recorded here when it lands.
14. **Connectors can say "not supported".** An adapter raises `NotSupportedError(capability, method, reason)` from
    a method it can't do, or lists the method in a class attribute `unsupported: frozenset[str]`. Then the kernel's
    handle refuses the call before reaching the adapter, and the method may be absent; the declared-modes check
    skips it. The CLI prints the message and exits 1. REST and the web head answer `400` with
    `{"error", "code": "not_supported", "capability", "method"}`. A chat tool gets the message as the tool's answer
    (it is a `HeinzelError`). Nothing retries it. The conformance kit accepts it for any method.

### Hard-wiring found, and where each is answered

| Where | What was hard-wired | Answer |
| --- | --- | --- |
| `heinzel/_user_chat.py` `selected` | chat, drafts, schedules need `ai_access == agent_runner == claude-code` | plugin models served here (8); built-in `pydantic-ai`, `chatgpt` and `gateway` still refused: follow-up |
| `heinzel/_user_chat.py` `dispatch` | `BackendBinding(backend="claude-code", mode="cli-model")` | the plugin's name, mode `api`, model and effort |
| `heinzel/_interactive_turn.py` `run` | any backend binding billed as `plan` | `api` for mode `api` (10) |
| `heinzel/_autonomous_schedule.py` | fingerprint and `access` literal for Claude Code only | plugin terms and `plugin-models` (9) |
| `heinzel/_claude_subscription_model.py` | `require_subscription_login` demands a `claude.ai` login; `subscription_environment` strips the environment | used only on the Claude Code path (8) |
| `ai/access/_pydantic.py` | any `base_url` builds an OpenAI-compatible client with a never-refreshed `x-api-key` | `api` and `token_command` (12), Head C |
| `kernel/_api_access.py` `ConfiguredApiAccess` | built-in only, pinned to one OpenAI model | Head C |
| `heinzel/_model_admission.py`, `run_connected`, `run_granted` | require `pydantic-ai` | Head C |
| `system.claude` | MCP server discovery is private | made public, Head B |
| `kernel/_ai.py` `UsageCause` | a closed set | namespaced plugin causes (10) |
| `heinzel/_service.py` `RUNNER_TOOLS` | read-only tools for `claude-code` and `command` only | `engine` runner (13), Head C |
| `org/_models.py` `CodingAgent` | `claude-code`, `command` or `none` in onboarding | unchanged: an onboarding hint, not a gate |
| `heads/web/_agent_titles.py` | recognises `claude` executables for titles | unchanged: cosmetic |

### Consequences

- Good: a private plugin implements two methods and gets chat, drafts, schedules, usage, Settings and the picker,
  with its own auth and proxy, and Core never holds its token.
- Good: the same engine runs every turn, so tools, approvals and limits don't fork per vendor.
- Bad: hz trusts the plugin's `Model` object completely. A plugin runs in-process with the person's rights anyway
  ([ADR 0026](0026-plugin-api-versioned-with-semver.md)); this adds nothing a plugin couldn't already
  do, but a careless plugin could log its own headers.
- Bad: no dollar caps on plugin models. Owners who need caps keep `usd-cap` and can't use them until a plugin can
  report prices.
- Bad: the last good model list lives in memory only, so a fresh process with a broken plugin shows no models until
  the plugin answers once.

### Confirmation

`tests/unit/kernel/ai_models_test.py` covers the capability, the derived option, offers, the choice, effort mapping,
the last good list, building, and declared gaps. `tests/unit/modules/user_chat_plugin_test.py` runs chat turns on a
`FunctionModel` and on a real `AnthropicModel` whose HTTP client takes its token from a command and talks to a fake
Messages API: one 401 and one refresh, no `x-api-key`. It also covers effort, unavailable models, dollar caps,
mid-turn changes and fingerprints. `tests/unit/token_auth_test.py` covers the token helper.
`tests/integration/ai_plugin_models_test.py` lists, uses, tests and chats with a made-up plugin in an isolated
profile, and checks `not_supported` on the CLI and REST. `tests/e2e/plugin_models_ui_test.py` drives the real
Settings with Playwright on an isolated `hz ui serve` with that plugin installed: it picks the option, then a model
(the unavailable one marked), and checks that it answers.

## Implementation

Head A (this record's first change) builds statements 1 to 12 and 14 except the built-in option's use of `api` and
`token_command`. Head B adds the conformance kit (`heinzel.conformance`), a helper for connector adapters on an MCP
server with configurable untrusted-content markers, and public discovery of Claude Code's MCP servers. Head C opens
the built-in `pydantic-ai` option, `ConfiguredApiAccess` and the internal admission paths beyond OpenAI and
`pydantic-ai`, and adds the `engine` runner for coding. The public `heinzel-example-bridge` plugin implements every
connector capability and `ai-models` with made-up parts, runs the conformance kit, and needs the Core release that
ships Heads A and B.
