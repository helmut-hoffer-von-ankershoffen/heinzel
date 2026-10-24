---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0006, 0039
related: 0017, 0019, 0025, 0027, 0033, 0035, 0040
---

# 0047: Plugins are managed from the web app, and adding one shows what it installs before it is confirmed

## Context and Problem Statement

The maintainer wants to install, list, enable, disable and remove plugins in the web app, as a desktop chat app's
"Plugins" settings do: a "Yours" tab, a "Discover" tab with search, "Add", and a menu per plugin. Today `hz system
plugin add` takes "anything uv installs" (a name, a git URL, a local path, a package) and installs it at once, and its
`add` and `remove` operations are endpoints of every head, the unauthenticated REST head included
([ADR 0019](0019-mcp-and-rest-heads.md)). Adding a plugin runs code with the person's rights, so a click in a browser
must not be enough to install something nobody looked at, and no page that can reach the REST port may install
anything. A running `hz ui` also loaded its plugins at start, so it can't see one added under it. How are plugin
changes exposed, what does `add` accept, and how does the running web app pick a change up?

## Decision Drivers

- Installing a plugin is a code-execution decision: the person sees the source, version and publisher first, and
  confirms; what they saw is what gets installed.
- The token-protected web head and the person's own CLI and MCP may change plugins; the unauthenticated REST head never
  may ([ADR 0039](0039-a-web-head-a-react-app-served-by-the-api-head.md), security model).
- Heads are views ([ADR 0003](0003-heads-are-views-on-operations.md)): the confirmation is a parameter of the
  operation, the same in every head, not a dialog only the UI has.
- Plugins stay `--with` requirements of the uv tool environment ([ADR 0006](0006-plugins-installed-through-the-uv-tool-receipt.md)),
  and private GitHub repositories work with the person's gh login, without a token passing through Heinzelmännchen
  ([ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md)).

## Considered Options

- **One `add` that shows without `confirm` and installs the pinned requirement with it; PyPI and https git only;
  local paths through a CLI-only `develop`; changes on CLI, MCP and the web head only; a history file; enable and
  disable through `[hz] disabled_modules`; a restart that keeps the port and token** (chosen)
- **A separate preview operation and `add` unchanged**: two ways to learn the same thing, and `add` would still install
  unseen code from every head.
- **`confirm` only on the web head**: an operation can't know its head (ADR 0003); an agent over MCP would install
  without a look.
- **Reload the kernel's module discovery in the running process**: `uv tool install --force` replaces the very
  environment the process runs from, and modules already imported stay loaded; a half-old process is worse than a
  short restart.
- **Keep local paths in `add`**: a web page or agent could name any directory; developing a plugin is a person at a
  terminal, so it is a CLI-only operation.

## Decision Outcome

Chosen option: **one `add` that shows first and installs on `confirm`, a narrow set of sources, changes only behind a
person or the session token, and an in-place restart of `hz ui`**, because it keeps one operation per action in every
head and makes the dangerous step explicit and checkable. Proposed for the maintainer, 2026-10-24.

1. **Which heads.** `hz system plugin add`, `remove`, `enable`, `disable` and `history` declare
   `heads={"cli", "mcp", "web"}`; `list` and `search` stay on every head; `develop` and `new` are CLI only. This
   removes `add` and `remove` from the REST head, which had them since ADR 0006.
2. **Show, then confirm.** `add SPEC` without `confirm` installs nothing: it reads PyPI's JSON API or the GitHub API
   (through `gh api` when gh is installed, so private repositories work; otherwise over https as
   [ADR 0033](0033-github-rest-api-over-urllib.md) does for public ones) and answers a `PluginCandidate`: the
   distribution, version, summary, publisher, source, whether the repository is private, and `requirement`, pinned to
   the release (`heinzel-x==1.2.0`) or the git commit (`heinzel-x @ git+https://…@<sha>`) it read. `add SPEC
   --confirm` installs that requirement; the web app sends back `requirement` as `spec`, so the dialog's version is
   the one installed. Unknown packages, repositories and refs are refused before anything runs. A host other than
   GitHub is installed as given, with a warning that nothing could be read.
3. **Which sources.** First-party names (the plugins in `plugins/`, [ADR 0017](0017-first-party-plugins-in-the-core-repository.md),
   and the reference company `acme`, [ADR 0040](0040-layers-and-playbooks-are-plugins.md)), PyPI packages named
   `heinzel-<name>` ([ADR 0025](0025-plugin-distributions-are-named-heinzel-name.md)), and git repositories over
   https. Refused: local paths and `file:` URLs (`hz system plugin develop PATH` instead), ssh, plain http, archives
   and wheels, URLs with a user name or token, queries, refs or subdirectories with shell or path syntax, and anything
   that starts like an option.
4. **Private repositories.** uv's git gets `credential.https://github.com.helper = !gh auth git-credential` through
   `GIT_CONFIG_*` variables for the reinstall, appended to any the person set: git asks gh, and the token never passes
   through Heinzelmännchen or lands in a URL, the receipt or the history.
5. **What was installed is recorded.** Every carried-out change appends a line to `<data_dir>/plugins.jsonl` (time,
   action, distribution, exact requirement, source, version, publisher); `hz system plugin history` lists it. One
   change runs at a time.
6. **Enable and disable** add or remove the plugin's top-level modules in `[hz] disabled_modules` of the user file,
   the kernel's existing switch; the kernel keeps the blocked records (`Kernel.blocked`), so `list` shows a disabled
   plugin with `enabled: false`. A plugin disabled by another file or `HZ_DISABLED_MODULES` is not silently "enabled".
7. **`list` says what a plugin brings and where it came from**: summary, `source_kind` and a browser `source_url`
   (from PEP 610's `direct_url.json` or its Project-URL metadata, https only, credentials stripped), the git commit,
   whether it is first-party, enabled or failing to load, its layers, adapters and playbook modules.
8. **Restart instead of reload.** Every result of a change says `restart_required: true`: a new `hz` command sees it at
   once, a running `hz ui` or MCP server doesn't. The web head adds `POST /api/restart` (behind the token, like every
   `/api` call): `hz ui` stops serving and executes `hz` again with its arguments, handing the new process a duplicate of
   its listening socket and its token (`HEINZEL_UI_RESUME`, read and removed at start), so the open tab keeps its URL
   and token, and requests made meanwhile wait in the socket's backlog. A server that isn't `hz ui` (`make web-dev`)
   answers 400. This amends ADR 0039's "the API is the operations": the restart is the head's own endpoint, because
   only the head knows how it runs.

### Consequences

- Good: the web app gets "Yours" (`list`), "Discover" (`search`), "Add" (`add` then `add --confirm`), and the per-plugin
  menu (`disable`, `enable`, `remove`) from operations the CLI and MCP share; the dialog shows exactly what installs.
- Good: an unauthenticated local page can no longer install or remove plugins through the REST head.
- Good: the history answers "what did I install, from where, and when".
- Bad: `hz system plugin add github` now shows and asks for `--confirm`; documentation and scripts that install say
  `--confirm`, and `hz system plugin add .` became `hz system plugin develop .`.
- Bad: git sources are pinned to a commit in the uv receipt; adding the plugin again moves it, an upgrade of the core
  doesn't.
- Bad: a long install holds the HTTP request open for minutes (uv's own timeout is 15); a job with progress is later
  work.
- Neutral: themes that plugins ship are listed once their record lands; `list` is the place.

### Confirmation

`tests/unit/modules/plugin_sources_test.py` (accepted and refused sources, gh and https readers, credential variables,
browser URLs), `tests/unit/modules/plugin_service_test.py` (pinning to the PyPI release and the git commit, no install
without `confirm`, the history, one change at a time, `develop`, what `list` tells),
`tests/unit/kernel/kernel_test.py` (`Kernel.blocked`), and `tests/integration/plugin_web_test.py`: the web head exposes
the changes and the REST head doesn't, the token is required, `confirm` is required, bad sources get 400, disabling and
enabling write the user file, and a restart hands the port and token to the process it becomes.

## More Information

- Asked for by the maintainer, 2026-10-24 ("install, list, enable/disable and remove plugins from within the web UI").
  The React screens are built separately on these endpoints.
- Open: whether `hz system serve mcp` should restart itself the same way (stdio clients restart servers themselves);
  whether a source allowlist per company (only its private repository and first-party plugins) should be a setting.
