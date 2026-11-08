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
when it is installed (ADR 0144): the add's last step, `bind`, sets each recommended binding whose connector is
installed and keeps every binding the person set; layers that disagree bind nothing. `--no-bind` skips it,
`hz system plugin unbind <module>` undoes it, `hz system plugin setup <module>` sets up a plugin installed
earlier without a terminal, and `hz system plugin checklist --module <module>` lists what is left: one row per
connector with its one next action, mostly the person's own sign-ins.

To prove a company plugin on a clean profile, run `scripts/acme_setup_e2e.py --hz <hz>`: it adds `acme` from the
plugin catalog under a throwaway HOME (never your live profile) and asserts `heinzel-github` and `heinzel-atlassian`
came with it. With `--with-bindings` it puts fake `gh`, `acli` and `gws` first on PATH, asserts the six
bindings, a checklist of sign-in rows only and a done checklist after the fake sign-ins, and that a binding set before
stays yours. It signs in nowhere. Those steps are yours, once: `gh auth login` and `acli jira auth login --web`
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
