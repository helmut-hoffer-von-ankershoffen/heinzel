---
status: proposed
date: 2026-11-14
implementation: partial
decision-makers: maintainer (decision), architect (shape)
related: 0026, 0028, 0029, 0045, 0050, 0055, 0057, 0062, 0141, 0146
---

# 0159: Plugins bring their own AI models through Pydantic AI

## Context and Problem Statement

A company wants Heinzels to use the models it reaches its own way, for example through a company proxy with its own
sign-in and its own list of models. It ships that as a private plugin whose code Core never sees. So Core needs a
well-defined interface a plugin implements to bring its models, and every AI feature has to run on them.

Before this record, a plugin could add an AI access option ([ADR 0057](0057-ai-access-is-a-module-one-choice-like-the-theme.md))
that reported status and ran a test, but chat, drafts and schedules ran only on Claude Code, and several paths
named `claude-code` or `pydantic-ai` outright.

## Decision Drivers

- Pydantic AI already defines what a model is (`pydantic_ai.models.Model`, a `Provider` with an HTTP client), so
  Core invents no new abstraction: a plugin is a model provider that plugs into Pydantic AI.
- How a plugin reaches its models and signs in is proprietary and its own business; Core never sees it.
- The plugin never runs an agent: Core keeps its tools, limits, approvals, records and checks in one engine.
- A plugin's model list is incomplete and changes every few weeks; Core works with an id alone.
- A connector can't always do every method of its capability; saying so is an answer, not a crash.

## Considered Options

1. **An interface to get the models** (chosen): the plugin lists models and returns a plain Pydantic AI model for
   one; Core runs it with Pydantic AI's own `Agent`.
2. **A capability that runs whole agent sessions** (an `agent-runner` per plugin): each plugin would re-implement
   tools, limits, usage and continuity checks.
3. **Configuration only** (a base address and a key in `[ai.access]`): covers a plain endpoint, not a model list
   from a database, availability, or another protocol. Kept for the built-in option (Head C).
4. **"Pydantic AI", then a provider under it**: the owner decided against it: "plugin should be its own AI
   provider".
5. **Core helpers for tokens** (a command-based `httpx.Auth`): the owner decided against it, "don't build a token
   from command helper" and "thats proprietary".

## Decision Outcome

Chosen option 1. **The principle: a plugin is a model provider that plugs into Pydantic AI. The plugin-facing
contract is discovery only: `models()` and `model(id)`. Core wraps no models, streams, messages or sessions in
types of its own; it uses Pydantic AI's `Agent`, capabilities, streaming, message history and usage directly.**
Checkable statements:

**The interface (version `AI_MODELS_INTERFACE = "1.0"`)**

1. **`ai-models` is a kernel capability, imported from `heinzel`** like every other capability protocol:
   `AiModels`, `ModelOffer`, `ModelUnavailableError`, `AI_MODELS_INTERFACE`. A plugin provides it with
   `module.provides("ai-models", modes={"read"}, hosts=[...], name="acme")`.
   - `models() -> list[ModelOffer]` lists the models on offer, the default first, from the plugin's own cache.
   - `model(model_id) -> pydantic_ai.models.Model` returns a ready model built however the plugin likes, without
     calling it; for an id it doesn't know or can't serve it raises `ModelUnavailableError`.
   - Optional: `test(model_id) -> AccessTest`, its own live check. Optional plain `title` and `summary` name its
     option. The docstrings in `heinzel/kernel/_ai_models.py` are the reference; the author guide quotes them.
2. **`ModelOffer` needs only an id.** It is a frozen Pydantic model that ignores fields it doesn't know. The id
   matches `^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$`. Optional: `title`, `description`, `aliases`,
   `context_window`, `efforts`, `tools` (default true), `thinking` (`adaptive`, `budget` or none), `available`
   (default true) and `level` (`low`, `medium`, `high` or none). The picker and a reply's header show `title` or
   else `id`. A reply's details also show the `model_name` the Pydantic AI response reported. Core picks a model by
   level with `model_for_level(source, level)`: the usable offer (calls tools, not marked unavailable) marked with
   that level; with several marked, the first, and a warning is logged; with none marked, or no level asked, the
   first usable offer; with no usable offer, `ModelUnavailableError`. The conformance kit fails a plugin that marks
   more than one offer with the same level.
3. **What Core guarantees the plugin.** It calls `models()` off the event loop, one call at a time per plugin, on a
   daemon thread, with a 5 s timeout, and reuses an answer for 3 s. When a call fails or times out it shows the
   last good list and says so. It keeps the last good list's ids, with `level`, `available` and `tools`, in
   `<state_dir>/ai/plugin-models/<adapter>.json` (mode 0600, replaced atomically, written only when it changed), so a
   restart with a broken plugin still shows that list; a missing or corrupt file is ignored. A row that isn't a
   valid offer is skipped and logged, not the list. It calls
   `model()` only with a listed, available id, off the event loop, with a 30 s timeout, once per turn.

**Compatibility**

4. **Within interface 1.x changes are additive only.** `ModelOffer` gains optional fields with defaults, and an
   unknown enum value in a newer plugin's row skips that row. The protocol gains optional methods only, which Core
   detects before calling (like `test`). Required parameters are never added. `ModelOffer.level` came this way:
   an optional field, so `AI_MODELS_INTERFACE` stays `1.0`. A breaking change is interface 2.0,
   follows [ADR 0026](0026-plugin-api-versioned-with-semver.md)'s deprecation rule, and Core keeps 1.x working for
   one minor release.
5. **Pydantic AI is part of the contract.** Core pins `pydantic-ai-slim==2.61.0`. A Core release that moves to a
   Pydantic AI version changing `Model`, `Provider` or their HTTP client (`httpx2`) is a plugin-API change: it is
   named in the changelog. Plugins should depend on the Pydantic AI range their Core supports, and pin `heinzel`
   to the release that ships this record and later ones below 1.0.

**Choosing and running a model**

6. **Each `ai-models` adapter is an AI access option of the same name.** The kernel derives its `ai-access` adapter
   with the same hosts. An adapter named like another distribution's AI access option (such as `claude-code`) is
   skipped and reported, so it can never take over chat. `ai-models` is never automatic, can't be bound in
   `[capabilities]`, isn't on the connector page, and isn't an input a contract can read.
7. **The model is pinned, and picked per chat.** `hz ai access use <plugin>` writes the model in use to
   `[ai.access] plugin_model`, so a new first row in the plugin's list never changes it or the schedules bound to
   it. People pick a model per chat in the chat's model picker, which lists the plugin's models with unavailable
   ones marked. Settings shows the same list and sets the default. `plugin_effort` sets the default effort.
8. **Unavailable is a clear refusal.** A model that isn't listed, is marked unavailable or calls no tools is
   refused before the turn starts and before `model()` is called. `ModelUnavailableError` names the models to
   pick instead. A model the provider answers 404 for gives the same error. `hz ai access test --all` (and Check
   every model in Settings' model dialog), only when a person starts it, sends each listed model one tiny request, one
   after another, at most 24 per run and 15 s each, recorded as usage with cause `test`. A model that doesn't answer
   is marked unavailable for every head, kept as ids only in a private file in the profile's state, until a later
   test answers; a model the plugin marks unavailable is skipped, and a test never clears the plugin's own mark.
   Any response counts as an answer, also one without text; only a provider or transport error, or no response in
   time, marks. A failure shows its kind and HTTP status only, never the provider's text. One run at a time per
   profile; the web asks for confirmation first and shows which model is being asked.
   The `ai-access` capability gains the optional method `test_model(model_id, timeout)` for it.
9. **Effort is checked per model and mapped to its style.** An effort the offer doesn't list is refused before
   the turn. For `thinking: adaptive`, Core passes Anthropic's `thinking: {type: adaptive}` and
   `output_config.effort`. For `budget`, it passes `thinking: {type: enabled, budget_tokens}`: 2k, 8k, 16k, 32k or
   64k tokens from low to max, with `max_tokens` above the budget. With `thinking` unset, Core passes no effort.
   Every request also sets Pydantic AI's own `anthropic_cache_instructions` and `anthropic_cache_tool_definitions`,
   so the requests of a multi-tool turn read instructions and tool definitions from Anthropic's prompt cache;
   models of other APIs ignore `anthropic_*` settings. Core adds no cache layer of its own.
10. **Chat, drafts and schedules run on the plugin's model through Core's engine.** Durable chats, drafts and
    schedules all go through `UserChatService.dispatch`, which runs the same `InteractiveTurn` as for Claude Code,
    with a Pydantic AI capability (`PluginTurnGuard`) instead of a model wrapper. Before each request, off the event
    loop and without calling the plugin, it checks that the owner, access, plugin version, configured model,
    effort and budget mode still hold, so an availability flip never aborts a running turn. It adds the effort and
    the output cap (16k tokens beside thinking), and records each response's tokens. A turn may move 1M tokens at
    most, makes at most the turn's request count of requests, and stops at 120 s. The Claude Code path is
    unchanged: no executable, login or environment check runs on the plugin path.
11. **The chat with the picker runs on the `engine` runner.** The web chat and `hz chat send` run on the bound
    agent runner. With a plugin's option in use, that runner must be `engine`: Head C's runner, which runs the
    plugin's model with Pydantic AI's streaming (text deltas, tool calls and results as they happen, through the
    same events as Claude Code's live output), keeps each chat's message history with `ModelMessagesTypeAdapter`
    (private, surviving a restart, capped at the model's context window), and keeps a stopped reply's partial
    answer marked stopped. Until `engine` is bound, that chat refuses rather than handing the plugin's model id or
    the prompt to another runner. Coding handoffs do the same; `hz ai access use` says so when they would stay on
    another runner.
12. **Failures are clean and leave the chat usable.** A provider's error becomes a `HeinzelError` from its kind
    and status code only, never its body or text. A 401 or 403 gives "The provider refused the request (401):
    check the plugin's sign-in." A 429, 503 or 529 says the provider is busy, a 5xx that it failed, a connection
    error that it couldn't be reached. A 400 or 413 whose body matches a fixed "too long" pattern (the body is
    matched, never shown) says the conversation is longer than the model can read: start a new chat, or pick a model
    with a larger context window. A failure before the turn's first response (no tool ran) sets the person's
    turn aside, so they can send the message again. Pydantic AI's own outcomes (limits, invalid output) keep their
    meaning in the engine.
13. **Billing and usage.** A plugin's models report tokens, not dollars. A turn is recorded with `billing: api`,
    `cost_usd: null` and the tokens of each response, under cause `chat` or `scheduled`, and counts as a turn of
    unknown cost. A dollar-capped budget mode (`usd-cap`) refuses it before `model()` is called. `UsageCause` also
    takes a plugin's own causes, namespaced `<plugin>.<cause>`.

**The trust boundary**

14. **What the plugin owns and what Core guarantees.** The plugin owns how it reaches its models: endpoint,
    sign-in, tokens, HTTP client, proxy, its list and cache, and what `available` means. Core provides no auth
    helper, never asks for, stores or logs a credential or header, and records counts and ids only, never prompts
    or answers. It holds the adapter only in trusted core code (`Kernel.ai_models`), never in a requester's handle.
    A plugin must not put secrets into exception messages: Core shows a plugin's own failure text (a list that
    failed, a model it couldn't build) cleaned and cut to 300 characters.
15. **Untrusted content.** What a connector or an MCP server returns reaches any model, a plugin's or not, only as
    a tool result inside the engine's framing of untrusted data, never as instructions. A connector built on an MCP
    server whose content carries markers keeps them as data; Head B's MCP helper turns configured markers into
    Core's untrusted framing, with a per-call nonce so content can't close its own frame. The `engine` runner holds
    read-only file tools confined to its working directory and the run's MCP servers, and no write or network tool.

**Connectors that can't do everything**

16. **`NotSupportedError(capability, method, reason)`**, a `HeinzelError`, says a connector can't do one method.
    An adapter raises it, or lists the method in a class attribute `unsupported: frozenset[str]`. Then the kernel's
    handle refuses the call before reaching the adapter, and the method may be left out. The CLI prints the message
    and exits 1. REST and the web head answer `400` with `{"error", "code": "not_supported", "capability",
    "method"}`. In a chat on the engine, a connected read the connector can't do shows as unavailable to the model,
    like any failed read. Nothing retries it. The conformance kit accepts it for any method.

**The engine runner (Head C)**

17. **History.** The `engine` runner keeps each chat's Pydantic AI message history, serialized with
    `ModelMessagesTypeAdapter`, in `<state_dir>/ai/engine-sessions/<chat>.json`: written atomically, mode 0600 in a
    0700 folder, never through a symbolic link, and kept across a restart. It is capped at 60 % of the offer's
    `context_window`, at four characters a token, by dropping whole turns, oldest first; the newest turn is always
    kept. Without a known context window it keeps at most 200 messages within 60 % of a conservative 128k-token
    window; the built-in Model API option has none, so it uses that cap. Chats on the interactive turn send at most
    12 kB of earlier messages, far below any window, so they need no cap by window. A chat that sends a transcript continues the stored history only when the transcript's last
    answer matches it; otherwise the runner starts a fresh history from the whole transcript. A stopped reply keeps
    its partial answer, marked `[stopped: <why>]`.
18. **Coding tools.** On the `engine` runner a coding session gets `read`, `grep` and `glob` (the read-only set of
    Claude Code's Read, Grep and Glob) and the run's MCP servers as `mcp__<server>__<tool>`, nothing else. The tools
    are confined to the working directory:
    - no `..`, no symbolic link anywhere on the path (each component opened with `O_NOFOLLOW` from a directory
      handle held for the session), no hard-linked file, no `.git`, regular files only;
    - `/`, the home directory and every folder above it (`/Users`, `/home`), and on macOS the data volume and
      its folders above the home directory are refused as working directories, by the identity of the opened
      folder, so another spelling, a different case on macOS or a symlink swapped in after the check doesn't pass;
    - each call runs in an isolated child process (`python -I`, no environment, only the directory handle), stopped
      at 20 s or the run's remaining time, whichever is sooner;
    - each call reads at most 64 MiB and visits at most 20,000 entries; `read` returns at most 2,000 lines or
      256 KiB, `grep` at most 200 matches, `glob` at most 1,000 files.
19. **Gaps against Claude Code, exactly.**
    1. Symbolic and hard links inside the folder are refused; Claude Code follows in-folder links.
    2. `grep` uses Python regular expressions, not ripgrep: it doesn't honour `.gitignore`, has no multiline,
       context, count or output modes, and skips binaries and files over 2 MiB.
    3. Text only: no images, PDFs or notebooks.
    4. The dollar budget is not enforced, because the models report tokens only; it is a warning in the result.
    5. MCP goes through a minimal stdio JSON-RPC client (`initialize`, `tools/list`, `tools/call`). A server's own
       requests (sampling, roots, elicitation) are answered as not supported, and only text content is returned.
    6. `ANTHROPIC_CUSTOM_HEADERS` in hz's environment is not sent to an `api = "anthropic"` endpoint, although
       Anthropic's client reads it: hz's own HTTP client drops every header it names, with the value it gives,
       before a request leaves (a gateway setup meant for Claude Code would otherwise send its token there), and
       puts hz's own `Authorization: Bearer <key>` back should the variable have replaced it. The Claude Code
       runner is unchanged.
20. **The built-in Model API option at an Anthropic endpoint.** With `[ai.access] api = "anthropic"` it speaks the
    Messages API at `base_url`, with the stored key as a bearer token, and lists no models. There is no token
    command (owner decision). `run_granted` refuses any binding that no pricing policy covers (any plugin, any
    endpoint) before building a model; `run_connected`, which is bounded by tokens, serves them.

**Hard-wiring found, and where each is answered**

| Where | What was hard-wired | Answer |
| --- | --- | --- |
| `heinzel/_user_chat.py` `selected` | chat, drafts, schedules need `ai_access == agent_runner == claude-code` | plugin models served (10); built-in `pydantic-ai`, `chatgpt`, `gateway` still refused: follow-up |
| `heinzel/_user_chat.py` `dispatch` | `BackendBinding(backend="claude-code", mode="cli-model")` | the plugin's name, mode `api`, model and effort |
| `heinzel/_interactive_turn.py` `run` | any backend binding billed as `plan` | `api` for mode `api` (13) |
| `heinzel/_autonomous_schedule.py` | fingerprint and `access` literal for Claude Code only | plugin terms and `plugin-models` (7) |
| `heinzel/_claude_subscription_model.py` | `claude.ai` login check, stripped environment | Claude Code path only (10) |
| `system/runner/chat` | the picker's model id goes to whatever runner is bound | refused unless `engine` is bound (11) |
| `ai/access/_pydantic.py` | any `base_url` builds an OpenAI-compatible client | `[ai.access] api` (`anthropic`), Head C |
| `kernel/_api_access.py` `ConfiguredApiAccess` | built-in only, one OpenAI model | Head C |
| `heinzel/_model_admission.py`, `run_connected`, `run_granted` | require `pydantic-ai` | Head C |
| `system.claude` | MCP server discovery is private | made public, Head B |
| `kernel/_ai.py` `UsageCause` | a closed set | namespaced plugin causes (13) |
| `heinzel/_service.py` `RUNNER_TOOLS` | read-only tools for `claude-code` and `command` only | `engine` runner (11), Head C |
| `org/_models.py` `CodingAgent` | `claude-code`, `command` or `none` in onboarding | unchanged: an onboarding hint, not a gate |
| `heads/web/_agent_titles.py` | recognises `claude` executables for titles | unchanged: cosmetic |

### Consequences

- Good: a private plugin implements two methods and gets chat, drafts, schedules, the picker, usage and Settings,
  reaching its models its own way; Core never holds its credentials.
- Good: one engine runs every turn, so tools, approvals and limits don't fork per vendor.
- Bad: Core trusts the plugin's `Model` object. A plugin runs in-process with the person's rights anyway; this adds
  nothing a plugin couldn't already do, but a careless plugin could log its own headers.
- Bad: no dollar caps on plugin models; owners who need caps keep `usd-cap` and can't use them.
- Bad: across a restart only the last good list's ids survive, so a broken plugin's models show without titles
  until it answers once.
- Bad: a durable chat turn that fails after its first response stays uncertain, as on the Claude Code path.

### Confirmation

- `tests/unit/kernel/ai_models_test.py`: the capability, the derived option, a skipped name clash, offers,
  versions, the choice, effort per model, mapping, the last good list, one listing thread, skipped rows, building,
  and declared gaps.
- `tests/unit/modules/user_chat_plugin_test.py` and `user_chat_plugin_review_test.py`: chat turns on a
  `FunctionModel` and on a stock `AnthropicModel` streaming over a fake Messages API. They cover effort and output
  cap on the wire, refusals, clean failures that leave the chat usable, mid-turn changes, fingerprints, and a free
  event loop.
- `tests/integration/ai_plugin_models_test.py` and `ai_plugin_models_review_test.py`: a made-up plugin listed,
  used, pinned, tested and chatted with in an isolated profile; the runner chat refusing; `not_supported` on the
  CLI and REST.
- `tests/e2e/plugin_models_ui_test.py`: the real Settings with Playwright on an isolated `hz ui serve` with that
  plugin installed: it picks the option, then a model (the unavailable one marked), and sees it answer.

## More Information

Head A builds statements 1 to 10 and 12 to 16, and the refusal in 11. Head B adds the conformance kit, the MCP
connector helper with untrusted markers, and public discovery of Claude Code's MCP servers. Head C opens the
built-in `pydantic-ai` option to an Anthropic base address, opens `ConfiguredApiAccess` and the internal admission
paths, and builds the `engine` runner of statement 11. **Head A ships only together with Head C**, in one bundle
and one release, 0.9.43: without the `engine` runner the chat with the picker can't serve a plugin's models. The
public `heinzel-acme-connectors` plugin, in the `heinzel-acme` repository of the made-up reference company, implements
every connector capability and `ai-models` with made-up parts and runs the conformance kit.
