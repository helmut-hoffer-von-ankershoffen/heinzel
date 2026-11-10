# Which settings need a restart

Settings saves a live setting at once; every other setting waits under "Restart and apply". The marker is `x-live` on the
setting (docs/SETTINGS_UX.md). A setting is live only when everything that uses it reads it again by itself.
`tests/unit/settings_restart_test.py` keeps this table and the markers the same, and fails for a setting
missing here.

Most of the rest are not fundamental: their service is built once at start and keeps the settings it was given. They
are marked as a follow-up and become live as their service reads them again. Only a socket, a folder, a log or the
set of modules truly needs a restart.

| Setting | Restart | Why |
| --- | --- | --- |
| `hz.data_dir` | yes | Folders are opened and created once when the server starts. |
| `hz.cache_dir` | yes | Folders are opened and created once when the server starts. |
| `hz.state_dir` | yes | Folders are opened and created once when the server starts. |
| `hz.log_dir` | yes | Logs are opened once when the server starts. |
| `hz.log_level` | yes | Logging is set up once when the process starts. |
| `hz.output_format` | no | Only commands use it, and each reads it as it starts; the web server never does. |
| `hz.expert` | no | Read again by the web app and by every command as it starts. Own switch in General, so it is not listed. |
| `hz.playful` | no | Only commands in a terminal use it, and each reads it as it starts; the web server never does. |
| `hz.plugins_enabled` | yes | Which modules exist is decided once, when the server starts. |
| `hz.update_check` | no | The update status and the hourly check read it again; the command line reads it as each command starts. |
| `hz.auto_update` | no | The web server reads it again before every update decision. |
| `hz.auto_update_min_age_hours` | no | Read again when an update is announced. |
| `hz.auto_update_plugin_min_age_hours` | no | Read again when an update is announced. |
| `hz.update_channel` | no | The update status and release list read it again; the command line reads it as each command starts. |
| `hz.features` | yes | Which modules exist is decided once, when the server starts. |
| `hz.disabled_modules` | yes | Which modules exist is decided once, when the server starts. |
| `hz.ui_port` | yes | The server's socket is opened once. |
| `hz.ui_allowed_origins` | yes | The origin check is built once, when the server starts. |
| `hz.api_allowed_hosts` | yes | The host check is built once, when the server starts. |
| `capabilities.agent_runner` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.ai_access` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.calendar` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.chat` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.code_host` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.directory` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.documents` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.mail` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.meetings` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.tracker` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `capabilities.team_graph` | yes | The choice is bound to its adapter once, when the server starts. Rereading it is a follow-up. |
| `ai.daily_budget_usd` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.gateway_title` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.gateway_summary` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.gateway_status_command` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.gateway_test_command` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.gateway_sign_in` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.gateway_runner` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.gateway_hosts` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.gateway_models` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.gateway_env` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.chatgpt_model` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.model` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `ai.access.base_url` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `graph.time_zone` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `graph.offline_after` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `graph.weeks` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `heinzel.user_dir` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `heinzel.team_repository` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `heinzel.schedule_team` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `heinzel.max_usd` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `heinzel.session_max_usd` | no | The chat limits are read again with every turn. |
| `heinzel.message_max_usd` | no | The chat limits are read again with every turn. |
| `heinzel.subscription_budget_mode` | no | Read again with every chat turn. |
| `heinzel.retention_days` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `org.handle` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `org.role` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `org.timezone` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `org.working_hours` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `org.github` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `org.coding_agent` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `org.name` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `org.email` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `report.user` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.agent.skill_dirs` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.changelog.github_token` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.credential.backend` | yes | The credential store is opened once, when the server starts. |
| `system.daemon.unit_dir` | yes | Where the background jobs are installed is fixed once the daemon is. |
| `system.directory.lookback_days` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.directory.min_occurrences` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.enabled` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.draft` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.heinzel` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.failed` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.doctor` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.release` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.plugin` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.calendar` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.sound` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.quiet` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.working_hours` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.per_hour` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.repeat_hours` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.notify.native` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.plugin.catalog_sources` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.plugin.github_token` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.model` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.claude_executable` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.codex_executable` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.codex_model` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.codex_effort` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.read_only_tools` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.command` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.command_output` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.command_format` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.command_draft_key` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.command_enforces_read_only` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.command_read_only_tools` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.command_env` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.ask_max_usd` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.chat.session_max_usd` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.chat.keep_days` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.chat.keep_sessions` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.runner.chat.tools` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.skill.chat` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.skill.personal_dir` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.skill.export_dirs` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.sound.name` | no | Read again with every sound request. Own controls, so it is not listed. |
| `system.sound.volume` | no | Read again with every sound request. Own controls, so it is not listed. |
| `system.sound.follow_theme` | no | Read again with every sound request. Own controls, so it is not listed. |
| `system.sound.mute_in_meetings` | no | Read again with every sound request. Own controls, so it is not listed. |
| `system.sound.duck_while_typing` | no | Read again with every sound request. Own controls, so it is not listed. |
| `system.state.personal` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.theme.name` | no | Read again with every theme request. Own picker, so it is not listed. |
| `system.ui.contributions` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `system.ui.audio_visualizer` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `team.name` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `team.repository` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `team.checkout` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
| `team.layer` | yes | Read once into the service when the server starts. Rereading it is a follow-up. |
