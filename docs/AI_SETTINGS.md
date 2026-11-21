# AI settings and their scope

Settings > AI separates shared usage, runner chats and provider-specific configuration. A setting remains editable
even when another provider is selected; changing it does not select that provider. Expert mode reveals the provider
and command details. Search still finds each visible setting by its original configuration key.

## Access controls

| Control | What it does | Boundary |
| --- | --- | --- |
| AI access choice | `ai access use` writes the access binding and, when supplied, its runner binding. | Bindings are reread before calls. An option without a runner keeps the existing runner and reports that fact. |
| Test | Calls only the named option's test. | May make a paid request. A successful access probe is not proof that a personal Heinzel has the required grant. Transport errors now appear on the page. |
| Set up Model API | Saves the model and stores a supplied key in the credential store. | Model configuration is loaded at startup; see the limitation below. Keys are never configuration settings or query parameters. |
| Set up gateway | Saves status and test command arrays. | Checks use the running gateway configuration; the runner must be configured separately. |
| Sign in | Uses the supported local ChatGPT browser flow or displays provider instructions. | Signing in alone does not grant personal Heinzel execution. |
| Install client | Adds the named optional client, then uses the existing restart-and-verify flow. | Installation is not a sign-in or a successful model probe. |
| Pick plugin model | Saves `ai.access.plugin_model`, then refreshes the listing. | Failed saves retain the previous selection. One shared default applies to the selected model provider, including an explicitly selected local test model. |
| Test all plugin models | Probes at most 24 models after the existing cost confirmation. | Unavailable models remain visible and pickable. |

These controls are implemented in `web/src/layout/AiAccessSetting.tsx` and
`src/heinzel/modules/ai/access/_service.py`. Empty setup forms no longer report success; editing after a save clears
the saved/test state until another save.

## Setting audit

The table traces the consumers in this source revision. “Works” means the value has a concrete consumer; it does
not mean every provider was contacted. Ordinary schema rows retain their existing restart-and-verify control;
plugin model and effort retain immediate save because their consumers reread configuration.

| Section and key | Scope and consumer | Result or limitation |
| --- | --- | --- |
| `ai.daily_budget_usd` | `_usage.report`: all recorded usage | Works as a meter target, never an enforced spending limit. Missing prices are excluded; subscription usage can be valued at API prices. |
| `system.runner.model` | `_claude.ClaudeCodeRunner.run`, `_command.CommandRunner.run` | Works for these runners. A call's model overrides the default. Claude subscription access tests and personal chat deliberately reject an override; leave empty there. Does not configure Codex, Model API or plugin models. |
| `system.runner.claude_executable` | Claude runner, Claude access probe and subscription snapshot | Works; path/name must identify an admitted Claude executable for subscription chat. |
| `system.runner.codex_executable` | `_codex.CodexRunner` and `CodexAccess` | Works for Codex CLI only. |
| `system.runner.codex_model` | `_codex.CodexRunner.run` | Works: passes `-m`; independent of ChatGPT and Model API models. |
| `system.runner.codex_effort` | `_codex.CodexRunner.run` | Works: passes `model_reasoning_effort`; Codex's default applies when unset. |
| `system.runner.read_only_tools` | `_claude.ClaudeCodeRunner.run` | Works for file-reading runner jobs; checks requested tools against the allowlist. Does not grant personal chat tools. |
| `system.runner.command` | `_command.CommandRunner.run` | Works: argument array, never a shell; fills documented placeholders. |
| `system.runner.command_output` | `_command.CommandRunner.run` | Works: stdout or the declared output file. |
| `system.runner.command_format` | `_command.CommandRunner._result` | Works: plain text or JSON. |
| `system.runner.command_draft_key` | `_command.CommandRunner._result` | Works only with JSON output. |
| `system.runner.command_enforces_read_only` | `_command.CommandRunner._check` | Required assertion before the custom command may run; not a sandbox added by Heinzel. |
| `system.runner.command_read_only_tools` | `_command.CommandRunner._check` | Works as the profile allowlist; the external command enforces actual tool behavior. |
| `system.runner.command_env` | `_command.CommandRunner.run` | Works: adds permitted environment names; credential variables remain excluded. |
| `system.runner.ask_max_usd` | `runner.ask`, `chat._store.ChatStore.costs` | Caps the requested runner budget. Claude receives its budget flag; Codex explicitly cannot enforce it. Custom commands must consume and enforce the budget placeholder. Personal Heinzels also have policy limits. |
| `system.runner.chat.session_max_usd` | `ChatStore.costs`, `ChatService.send` | Stops new runner-chat messages when recorded spend exhausts the cap. Missing costs conservatively consume the requested budget. Not a universal provider billing guarantee. |
| `system.runner.chat.keep_days` | `ChatStore.prune` | Works when cleanup runs; skips personal sessions and sessions holding the run lock. |
| `system.runner.chat.keep_sessions` | `ChatStore.prune` | Works as a count limit for generic runner chats; excludes personal sessions. |
| `system.runner.chat.tools` | `ChatService._run`, shared chat tool policy | Works for generic chat's operation set. Personal conversations use their own scoped tools and grants. |
| `ai.access.gateway_title` | `_gateway.GatewayAccess.status` | Works as the gateway card's name. |
| `ai.access.gateway_summary` | `_gateway.GatewayAccess.status` | Works as the gateway card's description. |
| `ai.access.gateway_status_command` | `_gateway.GatewayAccess.status` | Works as the offline sign-in/status probe. |
| `ai.access.gateway_test_command` | `_gateway.GatewayAccess.test` | Works as the explicit live probe; empty means no test is configured. |
| `ai.access.gateway_sign_in` | `_gateway.GatewayAccess.status` | Displayed as setup guidance, not automatically executed. |
| `ai.access.gateway_runner` | `_gateway.GatewayAccess.status`, `AccessService.use` | Selects the named installed runner when the gateway is chosen. Editing it alone does not reselect the gateway. |
| `ai.access.gateway_hosts` | `_gateway.GatewayAccess.status` | Declared destination metadata; not a network allowlist. |
| `ai.access.gateway_models` | `_gateway.GatewayAccess.status` | Fallback listing when the status command does not report models; not a model selection. |
| `ai.access.gateway_env` | `_gateway.GatewayAccess.status` and `test` | Works for gateway status/test subprocesses; not the separately configured runner. |
| `ai.access.chatgpt_model` | `_chatgpt.ChatGptAccess.test` | Used by ChatGPT access testing. Does not configure Codex or establish a personal-chat execution grant. |
| `ai.access.api` | `_pydantic.PydanticAiAccess` | Selects OpenAI or Anthropic protocol for a configured endpoint; irrelevant to a vendor-prefixed model without an endpoint. |
| `ai.access.model` | `_pydantic.PydanticAiAccess`, configured API bindings | Used for Model API probes and admitted direct-model turns. Provider/model support and grants still apply. |
| `ai.access.base_url` | `_pydantic.PydanticAiAccess` | Works for compatible endpoints; changes model interpretation and key-transport checks. |
| `ai.access.plugin_model` | `_plugin_models.PluginModelsAccess`, `_engine_runner`, `_user_chat` | Reread for plugin-model execution; per-chat selection can override the default. |
| `ai.access.plugin_effort` | `_plugin_models`, `_engine_runner`, `_user_chat` | Reread; only effective on models that support effort. Per-chat choices take precedence where supported. |

The provider-model defaults also cover the optional local Dummy (42) provider.
It remains explicitly selected through AI access; this page neither enables it nor adds a separate control.

## Remaining functional limitation

The Model API and gateway setup dialogs save configuration without restarting the process. Their adapters retain
startup `AccessSettings`. Model API probes refuse changed model, endpoint or API settings before reading credentials
and recheck after credential reads before network access. Gateway status and test probes refuse changed commands or
environment allowlists before starting a process. These checks also protect card and CLI probes.

The dialog explicitly explains the restart requirement under “When changes take effect”; restart Heinzel before
testing changed model/gateway settings.
The setup Test stays disabled after configuration saves; key-only saves remain testable without a restart.
The full schema controls under the provider's section already offer restart-and-verify. A future setup-flow change
should reuse that mechanism, including proof of loaded values, rather than merely refreshing the listing.

The scope audit deliberately does not relax Claude subscription admission, add Codex budget enforcement, alter
retention, or change execution grants. These are constraints exposed by the UI, not inactive settings to remove.

## Evidence boundary

Focused component tests cover provider grouping, preservation of every field, unknown-field fallback, search,
original save keys, expert visibility, error display and setup state. Existing focused runner tests exercise the
provider consumers without external requests. Screenshots use fictional data in an isolated browser fixture.
Live provider calls, deployed UI, and hosted full browser lanes remain separate acceptance checks.
