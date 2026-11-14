# Create and ship a custom plugin

A plugin can live in its own public or private repository. It needs no change in the Heinzelmännchen core or
`heinzel-plugins`. Start with one operation and one test; add settings, services and views when the feature needs
them. The same operation supplies CLI, MCP and REST access.

This guide uses the current public SDK and versioned developer toolkit. You need uv, Git and make; uv supplies Python.
GitHub CLI (`gh`) supplies repository creation, private access and release dispatch. The examples use a fictional
Harbor plugin; replace `your-account` with your GitHub user or organization.

If `hz` is not installed, get the SDK CLI with `uv tool install heinzel`. This installs core without
real plugins or a source checkout. Run `gh auth login` before creating a GitHub repository if you are not signed in.
For the app with default plugins, or automatic prerequisite setup, use the [standard app installation guide](guides/install.md).

## Scaffold a standalone repository

From a directory outside an existing Git repository:

```sh
hz system plugin new heinzel-harbor --defaults --data plugin_name=harbor --data github_owner=your-account
cd heinzel-harbor
make setup
make check-fast
```

The default `minimal` preset creates a typed operation, a behavior test, installed-wheel acceptance, a small
Makefile, owning CI/release workflows, README, AGENTS.md and CLAUDE.md. `setup` initializes Git and creates this
plugin's isolated environment and dependency lock. It downloads a pinned developer toolkit and a compatible
released core wheel; it never clones or tests core.

Use `--preset contributions` when you want a working example of settings, a service, a submodule, status/actions,
navigation, a Today widget, notifications and a local view. Both presets use the same commands.

Read the generated AGENTS.md before asking a coding agent to change the plugin. CLAUDE.md points to the same
instructions. The [extension guide](PLUGINS.md) describes the available declarations and their limits.

## Make the first useful change

Edit `src/heinzel_harbor/__init__.py` and `tests/hello_test.py`. The minimal declaration is:

```python
from heinzel import Module

module = Module("harbor", "Useful Harbor operations.")


@module.operation
def hello(name: str = "Ada") -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"
```

Return values rather than printing. Keep imports within your package and the public `heinzel` / `heinzel.kernel`
API. If plugins share an interface, put that interface in the SDK or a separate implementation-free contract
package. Consumers depend on that contract; they must not import a provider plugin.

```sh
make fix          # format and apply lint fixes
make check-fast   # own lint and unit behavior
make check        # own strict types, tests, coverage and frontend checks if declared
make preview      # isolated app state; stop with Ctrl-C
make build        # this plugin's wheel and source distribution
make acceptance   # install the wheel outside the source tree and test it
```

No unrelated plugin or core tests run. Write a test for the new behavior and mark every pytest file with one
level: `unit`, `integration` or `e2e`. Installed acceptance belongs in `tests/acceptance/`. A richer frontend declares
its own build and tests; a small static view needs no JavaScript build system.

## Show the useful feature

Use the [screenshot playbook](SCREENSHOT_PLAYBOOK.md) to plan real UI and CLI scenes with fictional data, review
privacy and legibility, and verify the rendered gallery before publication.

## Add dependencies

Add ordinary libraries and shared contract packages to `project.dependencies` in this plugin's `pyproject.toml`.
Put test tools in its test dependency group, then run `make lock` and `make check`. Commit the owning lock file.
The lock records resolved versions and hashes; it does not change core's dependencies or another plugin's lock.

Before a new SDK is released, use an explicit candidate artifact:

```sh
make lock CORE=/absolute/path/to/heinzel-0.9.1-py3-none-any.whl
make check CORE=/absolute/path/to/heinzel-0.9.1-py3-none-any.whl
make acceptance CORE=/absolute/path/to/heinzel-0.9.1-py3-none-any.whl
```

The toolkit checks the artifact's identity and compatibility range. Local wheel paths are not stored in the lock.
After the compatible core is published, the same commands work without `CORE`. Set `TOOLING=<toolkit-wheel>` only
when testing a candidate developer toolkit; normal projects use their immutable version and digest pin.

### Other plugins your plugin needs

A plugin that can't work without another plugin (a company plugin whose bindings read through `heinzel-github`)
names it under `requires-plugins` in `[tool.heinzel.plugin]`; one it only works better with goes under
`recommends-plugins`. The table's `requires` stays the capability contracts:

```toml
[tool.heinzel.plugin]
requires-plugins = ["heinzel-github", "heinzel-atlassian>=0.2"]
recommends-plugins = ["heinzel-google"]
```

Entries are plugin names with an optional version range, never a URL: `hz system plugin add` says before it starts
which required plugins it installs too, reads each from the plugin catalog (first-party, or your own GitHub owner),
installs them first, one step each, and installs the recommended ones too (`--no-with-recommended` only offers
them). `hz system plugin repair`, which the automatic updater runs once per plugin version, installs required plugins
an installation lacks. Neither signs in: your `hz system doctor` checks keep naming the sign-in step.

A company plugin's playbook `defaults` that recommend connectors (`{"capabilities.tracker": "atlassian"}`) are written
when it is installed (ADR 0145), if the plugin declares a layer (`module.layer(...)`): the add's last step, `bind`,
sets each recommended binding whose connector is installed and keeps every binding the person set, or undid; layers
that disagree bind nothing. Any other plugin's playbook binds only when the person asks (`hz system plugin bind`). `--no-bind` skips it,
`hz system plugin unbind <module>` undoes it, `hz system plugin setup <module>` sets up a plugin installed
earlier without a terminal, and `hz system plugin checklist --module <module>` lists what is left: one row per
connector with its one next action, mostly the person's own sign-ins.

To prove a company plugin on a clean profile, run `scripts/acme_setup_e2e.py --hz <hz>`: it adds `acme` from the
plugin catalog under a throwaway HOME (never your live profile) and asserts `heinzel-github` and `heinzel-atlassian`
came with it. With `--with-bindings` it puts fake `gh`, `acli` and `gws` first on PATH, asserts the six
bindings, a checklist of sign-in rows only and a done checklist after the fake sign-ins, and that a binding set before
stays yours. It signs in nowhere. Those steps are yours, once: `gh auth login` and `acli jira auth login --web`
(then `acli confluence auth login --web` if you use Confluence); `hz system doctor` names whichever is still open.

To prove a company plugin on a clean profile, run `scripts/acme_setup_e2e.py --hz <hz>`: it adds `acme` from the
plugin catalog under a throwaway HOME (never your live profile) and asserts `heinzel-github` and `heinzel-atlassian`
came with it. It signs in nowhere. Those steps are yours, once: `gh auth login` and `acli jira auth login --web`
(then `acli confluence auth login --web` if you use Confluence); `hz system doctor` names whichever is still open.

## Declare the programs your plugin needs

If a feature runs an external command line (a VPN client, a vendor CLI), declare it in the module that owns the
feature. `hz setup`, doctor and the generated plugin page then check it, install it where they may, and document it,
without Core knowing your provider:

```python
from heinzel import Homebrew, ManualInstall, Module, ModuleSettings


class SharingSettings(ModuleSettings):
    """Whether to share the workspace."""

    enabled: bool = False


module = Module("sharing", "Share the workspace.")
module.settings(SharingSettings)


def wanted(settings: SharingSettings) -> bool:
    return settings.enabled


module.prerequisite(
    "vpn-tool",
    title="VPN tool",
    purpose="Shares the workspace over your private network.",
    when="feature",
    needed=wanted,
    condition="Sharing is turned on.",
    macos=Homebrew("vpn-tool"),
    linux=ManualInstall("Install the VPN tool from your distribution.", "https://example.org/vpn-tool/install"),
)
```

`when` says how early the program is needed: `install` (every standard app), `setup` (the module's setup step) or
`feature` (only when `needed`, a read-only predicate over settings, says the person's selection requires it).
`condition` is the same condition in words; documentation shows it and never runs your predicate. The generic engine
runs `vpn-tool --version` (`version_args` must contain exactly one flag: `--version`, `-version` or `-V`,
without any suffix arguments or bare subcommand), accepts a copy that answers and meets `minimum_version`, and
verifies every result against the final process `PATH`. A directory choice that shadows another required tool leaves
that tool unmet.
On macOS it installs a missing `Homebrew` formula or cask after the person agreed. Linux, a missing Homebrew and a
`ManualInstall` only show your steps. Text and links are one plain line each. Do not sign anyone in from a
prerequisite: keep sign-in in your own setup step.

Set `requires_apple_developer_tools=True` when the program's `/usr/bin/<name>` on macOS is an Apple developer-tool
launcher, rather than the real program. The owning declaration supplies this metadata; ordinary system tools need
no such flag. Once any enabled owner marks a launcher, the safety guard also applies to other owners of the same
executable, including checks scoped to one owner. A probed check verifies the selected developer tools before
launching that system copy. The guard compares physical file identity, covering dot components, symlinks, hardlinks
and case aliases while keeping the selected command path for execution and reports. If identity cannot be read,
the tool stays unverified and that uncertainty permits neither a probe nor installation. A check with `probe=False`
starts no process, including `xcode-select`, and leaves that launcher unverified unless another executable copy is found.
The flag is exported in prerequisite metadata and its
guidance appears in the plugin page.
Relative Homebrew candidates are rejected so inspection and execution cannot refer to different working directories.

A step that has prerequisites runs after they are ready; `hz setup` checks them first. Removing your plugin never
removes the program. `hz system plugin docs build` writes a Prerequisites table from the same declaration, so the page
cannot drift from what the code does. Test a declaration with `hz system prerequisite check --owner <module>`
(`HZ_SHARING__ENABLED=1` selects the example above; `--context feature` asks for a `feature` program that has no
predicate) and `hz system prerequisite list --json`, against the released SDK; no Core checkout is needed.

## Bring your own AI models and connectors

A plugin can bring its own AI models and its own connectors. This needs Heinzelmännchen 0.9.43 or later, the first
release with [ADR 0159](adr/0159-plugins-bring-their-own-ai-models-through-pydantic-ai.md). See the public
`heinzel-acme-connectors` plugin, in the `heinzel-acme` repository of the made-up reference company, for a worked
example of all of it.

### AI models: the `ai-models` interface

A plugin is a model provider that plugs into [Pydantic AI](https://ai.pydantic.dev): it says which models it has,
and hands Heinzel a Pydantic AI model for one. How it reaches the models, and how it signs in, is up to the plugin.
Heinzel runs the model with Pydantic AI itself, with its own tools, streaming, history, limits and usage records.

```python
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from heinzel import ModelOffer, ModelUnavailableError, Module

module = Module("acme-ai", "Acme's models.")


@module.provides("ai-models", modes={"read"}, hosts=["ai.acme.example"], name="acme")
class AcmeModels:
    title = "Acme AI"

    def models(self) -> list[ModelOffer]:
        # From your own cache: Heinzel waits 5 seconds at most.
        return [ModelOffer(id="acme-large", thinking="adaptive", efforts=["low", "medium", "high"])]

    def model(self, model_id: str) -> AnthropicModel:
        if model_id not in {offer.id for offer in self.models()}:
            raise ModelUnavailableError(model_id)
        provider = AnthropicProvider(base_url="https://ai.acme.example", http_client=your_http_client())
        return AnthropicModel(model_id, provider=provider)
```

The option shows up as "Acme AI" next to Claude Code in Settings and in `hz ai access list`. People choose it with
`hz ai access use acme`, then pick a model per chat in the chat's model picker. Reference
(`from heinzel import ...`, interface `AI_MODELS_INTERFACE = "1.0"`):

| Name | What it is |
| --- | --- |
| `AiModels.models() -> list[ModelOffer]` | The models on offer, the default first. Heinzel calls it off the event loop, with a 5 s timeout, reuses an answer for 3 s, and shows the last good list if a call fails. A bad row is skipped, not the list. |
| `AiModels.model(model_id) -> Model` | A ready `pydantic_ai.models.Model`, built without calling it. Called only with a listed, available id, off the event loop, once per turn. Raise `ModelUnavailableError` for an id you can't serve. |
| `AiModels.test(model_id) -> AccessTest` | Optional: your own live check. Without it, Heinzel sends one short request. |
| `title`, `summary` | Optional attributes: what the option is called and one sentence about it. |
| `ModelOffer` | `id` (required), and optional `title` (shown instead of the id), `description`, `aliases`, `context_window`, `efforts`, `tools` (default true), `thinking` (`"adaptive"` or `"budget"`: Anthropic's two effort styles, which Heinzel maps; leave unset to handle effort yourself) and `available` (default true; false keeps it visible, marked unavailable, and refused). |
| `ModelUnavailableError(model_id, others=None, reason="")` | The model can't be used now; the message names others to pick. |

Within interface 1.x, changes are additive only: new optional fields and new optional methods. Never put a secret
into an exception message: Heinzel shows failure messages, cut short.

### Connectors

Provide `mail`, `calendar`, `documents`, `tracker`, `chat`, `meetings` or `code-host` as for any connector. A
method your system can't do raises `NotSupportedError(capability, method, reason)`. Or list it in a class
attribute, `unsupported = frozenset({"replace_section"})`: then Heinzel refuses the call before it reaches you, and
the method may be left out. People see a plain "can't do that here" in the CLI, the web app and the chat.

## Check your adapters with the conformance kit

Core can't test a private plugin, so your plugin's own CI runs Core's checks. The kit comes with Heinzelmännchen:
`heinzel.conformance`, and the `hz_conformance` pytest fixture, which needs no import or conftest. Point it at your
module:

```python
import pytest

from acme_plugin import module
from heinzel import Window

pytestmark = pytest.mark.integration


def test_conforms(hz_conformance, fake_acme):
    window = Window.model_validate({"since": "2026-11-01T00:00:00Z", "until": "2026-11-08T00:00:00Z"})
    hz_conformance(
        module,
        samples={"mail": {"messages": {"query": "from:ada", "window": window}}},
        writes=lambda: len(fake_acme.writes),
    )
```

It builds each adapter your module provides as Heinzel does, and checks it against its capability's contract:

- **Shape.** Every method of the modes you declare exists and takes the protocol's parameters and defaults, unless
  `unsupported` lists it. Every name in `unsupported` is a method of the capability.
- **Modes and hosts.** Each declared mode keeps a method you support. You name the hosts you send data to.
- **Read calls.** It calls each read method once: those without parameters, and those you pass `samples` for. It
  never calls a write method. A read call must return the protocol's type, or raise `NotSupportedError` with its
  own capability and method. It must not write: calling one of your write methods, a write tool of an
  `McpConnector`, or changing your `writes` counter fails the check. The counter is the guard that really counts:
  the other two only see writes made through those paths on the calling thread.
- **AI models.** `models()` answers within 5 seconds with valid `ModelOffer`s and unique ids. `model(id)` returns a
  `pydantic_ai.models.Model`. An unknown or unavailable id should raise `ModelUnavailableError`; the kit warns
  otherwise, since Heinzel refuses such an id before it calls `model()`. With `live_request=True`, one request
  through your first model must answer.
- **Sign-in and health.** A `@module.sign_in` provider's `status(live=False)` answers within 5 seconds without the
  network, with a `SignInStatus` that lists every declared grant, says `live=False`, and names the account, never a
  token. Offline `@module.health` checks run without the network and return a list of `Check`. Live checks and
  `sign_in` itself are never called.

A failure lists every problem at once, each with the method and what the contract says. A `ConformanceWarning`
names what Heinzel tolerates but you should fix, such as one invalid model offer Heinzel skips; with pytest's
`filterwarnings = ["error"]` it fails too. The report names
the Heinzelmännchen version, the `ai-models` interface version, Pydantic AI and httpx2. Check one adapter on its own
with `hz_conformance(adapter, "mail", modes={"read"})`.

## Build a connector on an MCP server

A company MCP gateway often gives each connector a local launcher that signs in by itself. `McpConnector` runs that
launcher and speaks MCP with it, so your plugin holds no secret:

```python
from heinzel import Message, Module, Window
from heinzel.connectors.mcp import ENVELOPE_UNTRUSTED, INLINE_UNTRUSTED, McpConnector, McpTool

module = Module("acme-chat", "Acme chat through the company MCP gateway.")


@module.provides("chat", modes={"read"}, hosts=["mcp.acme.example"], name="acme-chat")
class AcmeChat:
    unsupported = frozenset({"post"})

    def __init__(self) -> None:
        self._mcp = McpConnector(
            ["acme-mcp", "launch", "chat"],
            tools=[McpTool("channel_history", aliases=("history",)), McpTool("send", mode="write")],
            markers=[INLINE_UNTRUSTED, ENVELOPE_UNTRUSTED],
            modes={"read"},
        )

    def messages(self, channel: str, window: Window) -> list[Message]:
        result = self._mcp.call("channel_history", {"channel": channel, "since": window.since.isoformat()})
        return [Message.model_validate(row) for row in result.data["messages"]]
```

- **Start and stop.** The launcher starts on the first call, within `start_timeout`, and stops with `stop()` or at
  exit. Each call has a `timeout`. A launcher that fails to start reports the last line it wrote to stderr, such as
  its own "sign in again" hint.
- **Tools.** At start, every tool you declare must exist under its name or one of its `aliases`; extra tools are
  fine. A missing one raises `McpToolsMissingError`, naming it and the tools the server has.
- **Size.** An answer longer than `max_output` characters (a million by default) raises `McpConnectorError`. With
  `overflow="cut"` the connector keeps the first `max_output` characters of the text, adds a note, and sets
  `result.cut`.
- **Modes.** MCP servers don't reliably say which tools write, so you declare each tool's mode. A connector with
  `modes={"read"}` refuses its write tools.
- **Untrusted content.** Servers mark text from outside, such as a mail body, with markers like
  `«untrusted-data» … «/untrusted-data»` or `<untrusted-mail-data-UUID> … </untrusted-mail-data-UUID>`. Pass the
  markers your server uses; `UntrustedMarker.literal(start, end)` makes your own. The connector removes them, keeps
  the content, lists the marked parts in `result.untrusted`, and passes every string through `clean_text`. Content
  can contain markers too, to end its region early or nest one. So everything from a marker's first appearance to its
  last counts as untrusted, and an end marker with no start before it makes everything before it untrusted. Only an
  envelope with a random id pairs exactly. Heinzel then treats what your adapter returns as data, never as
  instructions.
- **Results.** `result.text` is the text, `result.data` the structured content or the text read as JSON, and
  `result.parse(Model)` validates it.

In your tests, `McpConnector.in_process(server, tools=...)` connects to an `mcp.server.MCPServer` in the same process.

To see whether the person already set up the launcher in Claude Code or Claude Desktop, call
`heinzel.connectors.claude_mcp_servers()`. It reads Claude's configuration files only and returns each server's
name, source, program and the names of its environment variables, never a value. Servers the person switched off
for Heinzels are left out. Use it to point people at the right setup step; your connector still starts its own
launcher.

## Choose public or private hosting

First commit the scaffold and your feature. Then create exactly one repository:

```sh
git add .
git commit -m "feat: add Harbor plugin"
gh repo create your-account/heinzel-harbor --public --source . --remote origin --push
```

For a private plugin, replace `--public` with `--private`. Keep the repository, examples, artifacts and credentials
private. Both layouts and presets work in a repository outside `heinzel-plugins`.

Repository visibility controls source access. Operations returning private data must declare
`@module.operation(private=True)` so only local or authenticated heads expose them. Hosting a repository privately
does not change an operation's declared exposure. See the [extension guide](PLUGINS.md#python-cli-mcp-and-rest).

To participate in the catalog, add the `heinzel-plugin` topic:

```sh
gh repo edit your-account/heinzel-harbor --add-topic heinzel-plugin
hz system plugin search --text harbor --json
```

Discovery reads the repository's `pyproject.toml` and `heinzel.modules` entry point without importing its code.
There is no central registration file to edit. Private repositories appear only to GitHub accounts allowed to
read them; run `gh auth login` on each installing machine. The same authenticated gateway reads catalog listings
and project metadata. A topic is optional for direct installation.

Catalog discovery currently uses GitHub. Direct Git installation supports HTTPS repositories; SSH URLs, archives
and local paths are not `plugin add` sources. Use `hz system plugin develop .` for a local checkout.

## Release one plugin

The plugin version belongs to this project, independently of core and other plugins. After the initial 0.0.0
scaffold, choose a release version and run the local release preview:

```sh
uv version --bump minor --frozen
make lock
make release
```

`release` runs owning checks, builds artifacts and previews the tag; it uploads nothing. Commit the version and
lock changes, merge a reviewed pull request, and return to clean, current `main`. The generated CI checks this
plugin against an installed core wheel. It does not run core's suite.

For a **public PyPI package**, configure a GitHub environment named `pypi`, restricted to `main`, and register a
[PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/) for this distribution, your
repository, `release.yml` and that environment. Each distribution needs its own trust grant; no long-lived API
token is needed.

When a collection has several brand-new PyPI projects, enroll and publish one pending project before enrolling
the next. PyPI currently rejects duplicate pending GitHub publisher identities; activation removes that bootstrap
restriction. Existing projects can publish independently in parallel. See the
[PyPI tracking issue](https://github.com/pypi/warehouse/issues/16920).

With the owning publisher configured:

```sh
make publish
```

For a **private plugin**, the same command selects GitHub releases automatically. Private sources are refused by
the PyPI path because [PyPI packages are public](https://docs.pypi.org/publishing/troubleshooting/#how-can-i-publish-my-private-packages-to-pypi).
A public plugin can also choose GitHub-only publication with `make publish REGISTRY=github`.

The protected workflow checks clean source identity, builds once, runs acceptance on that wheel and publishes
those exact bytes under `heinzel-harbor/v<version>`. Public artifacts carry GitHub provenance. Private repository
attestations require a supported GitHub plan; enable `PRIVATE_ATTESTATIONS=true` as a repository variable only
when available. See [GitHub's availability and setup](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).
Private checks keep package names out of public advisory queries. If your organization provides an internal
audit tool, declare its argument list as `tool.heinzel.plugin.audit_command`; it runs in the owning environment.
Without that command, private releases verify locks and installed compatibility but do not perform a vulnerability
advisory audit.

If a public GitHub release was published before its PyPI grant was ready, resume its original upload:

```sh
make publish REGISTRY=pypi TAG=heinzel-harbor/v0.1.0
```

This verifies the original tag, source commit, workflow attestation and artifact hashes, then uploads the same
bytes. It does not rebuild from newer `main`. Private releases do not use this public-registry resume path.
Never overwrite a released version; change the owning version for a new release. `uv publish --dry-run dist/*`
can rehearse artifact validation locally without uploading.

## Install from another repository

Public and private GitHub plugins use the same command; private access uses the installing person's `gh` login:

```sh
gh auth login
hz system plugin add 'git+https://github.com/your-account/heinzel-harbor@heinzel-harbor/v0.1.0'
hz system plugin add 'git+https://github.com/your-account/heinzel-harbor@heinzel-harbor/v0.1.0' --confirm
hz harbor hello --name Ada --json
```

The first call previews the source and resolved commit. The second installs the pinned candidate. Do not put
credentials in repository URLs or commit them. A published PyPI package can instead be installed with
`hz system plugin add heinzel-harbor --confirm`. Private GitHub releases provide distributable artifacts and
history; source installation from their immutable tag does not require a private package index.

## Add to a plugin collection

Inside `heinzel-plugins`, or a collection using the same developer toolkit:

```sh
make new NAME=harbor
make check-fast NAME=harbor
make check NAME=harbor
make build NAME=harbor
make acceptance NAME=harbor
make release NAME=harbor
make publish NAME=harbor
```

The collection scaffold creates only `plugins/heinzel-harbor/`, with its own metadata, source, tests, lock and the
same small owning Makefile. From inside that directory, the standalone commands above work unchanged. This is
also the owning loop for optional plugins in Core or contrib: the parent Makefile may build something else.
Root `NAME` shortcuts are conveniences of a configured collection.

The supported hosts route plugin-only hooks and CI to owning projects and public-contract consumers. Their one
generic plugin release workflow supplies publication without core tests. The scaffold adds no nested Git
repository, copied workflow or parent registration. To render collection files directly use
`hz system plugin new plugins/heinzel-harbor --defaults --layout collection --data plugin_name=harbor`.
Name the owner in root collection commands; shared tooling checks have the distinct `tooling-check` target.
Consumers of a changed public contract are selected from their declared requirements, without testing all core.
Core-root `make pr` and coding-agent completion checks also select the owning projects for plugin-only work.
Shared commit and credential policies still apply; a mixed core/plugin change retains the full core preflight.
Core's optional-plugin workflow uses the main-restricted `plugin-pypi` environment for public PyPI grants;
standalone repositories, contrib and the official collection use `pypi`. Register the owning distribution with
its host's `release.yml` and that environment. Core's separate tagged-core publisher remains in `pypi`.

## Local sharing observations

A plugin can declare historical sharing evidence for the interactive bare `hz` overview with
`module.add(CachedOverviewLet(name="sharing"))` (`from heinzel import CachedOverviewLet`). This is inert metadata: the
overview never resolves a plugin service or invokes its operations. The owning plugin may atomically publish a
private `state_dir/overview/<qualified-module>.sharing.json` record during an explicit mutation or existing scheduled
work. A read-only status operation must remain read-only.

The JSON object contains exactly `state` and `observed_at`. State is one of `not installed`, `stopped`, `running`,
`unhealthy` or `unknown`; the timestamp is an aware ISO timestamp. Use mode 0700 for the overview and state folders,
mode 0600 for records, and an atomic replacement of a freshly written regular file. Publish no URL, token, host,
identity, error message or other arbitrary detail. Core ignores unsafe, oversized, malformed, future or expired
records; even fresh evidence is labeled **last observed**, never current reachability. See
[ADR 0119](adr/0119-interactive-cli-reads-local-overview-records.md).

## Preview screenshots before installation

In Settings → Plugins, open **Screenshots** for a discovered or installed public GitHub plugin. Choose UI or CLI
captures, or open a capture at full size. `hz system plugin showcase harbor --json` returns the same declared media
without installing the plugin. Private and unknown installed sources stay in their owning repositories.

Declare captures in the owning project's `pyproject.toml`:

```toml
[[tool.heinzel.catalog.screenshots]]
path = "docs/assets/interface.png"
surface = "ui"
caption = "The plugin's interface"

[[tool.heinzel.catalog.screenshots]]
path = "docs/assets/terminal.webp"
surface = "cli"
caption = "The plugin's terminal output"
```

Use at most eight regular PNG, JPG or WebP files of at most 2 MiB each, inside the project. Captions are plain text,
at most 180 characters. The preview uses the owning public source at an immutable revision and never executes the
plugin. Stream soundscapes may provide explicit YouTube links; opening a gallery starts no playback.
