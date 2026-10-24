"""Models of the plugin module."""

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import SettingsConfigDict

from heinzel import ModuleSettings

Source = Literal["pypi", "git", "path", "unknown"]
"""Where a plugin's code comes from: a package index (PyPI), a git repository, a directory on this machine, or
nothing recorded."""
Action = Literal["add", "develop", "remove", "enable", "disable"]
PluginKind = Literal["connector", "theme", "capability", "playbook"]
"""What a plugin is (`PluginInfo.kinds`); a plugin can be several."""


class PluginSettings(ModuleSettings):
    """Settings of the plugin module: the `[system.plugin]` table or `HZ_SYSTEM_PLUGIN__*` variables."""

    # The aliases are environment variable names; the field name still works in the TOML table and in code.
    model_config = SettingsConfigDict(populate_by_name=True)

    github_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HZ_SYSTEM_PLUGIN__GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        description="GitHub token for `hz system plugin search` and for reading a public repository's metadata "
        "before `add` when the gh CLI isn't installed, which work without one at a lower rate limit; also read from "
        "GH_TOKEN or GITHUB_TOKEN. Sent to api.github.com only.",
    )


class PluginLayer(BaseModel):
    """A layer a plugin's module declares (ADR 0040)."""

    module: str = Field(description="The layer's module name, e.g. `acme-payments`.")
    parent: str | None = Field(description="The layer above it; none for the root (the company).")
    site: str | None = Field(description="The site layer it names, if any.")


class PluginInfo(BaseModel):
    """An installed plugin, where it came from, and what it brings."""

    distribution: str = Field(description="Distribution name, e.g. heinzel-clock.")
    version: str = Field(
        description="Installed version, from the distribution's metadata as installed now; `0.0.0` only when the "
        "plugin says so itself (one not released yet)."
    )
    modules: list[str] = Field(description="Qualified names of the modules the plugin provides, enabled or not.")
    name: str = Field(description="The name without `heinzel-`: its command group (`hz <name>`).")
    summary: str = Field(default="", description="What the plugin does, in its authors' words (its metadata).")
    source_kind: Literal["pypi", "git", "path"] | None = Field(
        default=None, description="Where it was installed from: PyPI, a git repository, a directory; none if unknown."
    )
    source_url: str | None = Field(
        default=None,
        description="Where a browser shows its source, from its `direct_url.json` (PEP 610): the git repository, on "
        "GitHub at `/tree/<commit>` (and `/<subdirectory>` for a plugin in one; an ssh remote becomes its https "
        "page), or for PyPI its repository or PyPI page; https only, never with credentials; none for a directory "
        "or when unknown.",
    )
    revision: str | None = Field(default=None, description="The git commit installed, for a git source.")
    homepage: str | None = Field(default=None, description="Its source repository, from its metadata.")
    first_party: bool = Field(
        default=False, description="Whether it is maintained and released with Heinzelmännchen (ADR 0017)."
    )
    enabled: bool = Field(
        default=True, description="Whether its modules are loaded; `hz system plugin disable` switches it off."
    )
    error: str | None = Field(default=None, description="Why it failed to load, if it did.")
    layers: list[PluginLayer] = Field(
        default_factory=list[PluginLayer], description="Layers its modules declare (ADR 0040)."
    )
    capabilities: list[str] = Field(
        default_factory=list, description="Adapters it brings, as `<adapter>:<capability>`, e.g. `github:tracker`."
    )
    playbooks: list[str] = Field(
        default_factory=list, description="Modules that bring a playbook (ADR 0037); empty while it is disabled."
    )
    themes: list[str] = Field(
        default_factory=list,
        description="Names of the themes its modules offer for the web app (`module.theme`, ADR 0044), sorted; "
        "`hz system theme use <name>` picks one.",
    )
    kinds: list[PluginKind] = Field(
        default_factory=list[PluginKind],
        description="What the plugin is, any of four, always in this order: `connector` when it brings adapters "
        "(`capabilities` isn't empty); `theme` when it offers themes; `capability` when it has an operation of its "
        "own besides the `info` every plugin from the template gets (e.g. `hz clock`); `playbook` when it ships one. "
        "In the web app `Capability` means such a feature of its own, while `capabilities` above, like ADR 0028, names "
        "the integration interfaces (tracker, calendar, ...) a connector implements.",
    )


class AvailablePlugin(BaseModel):
    """A plugin one can install with `hz system plugin add`."""

    name: str = Field(description="Distribution name, `heinzel-<name>` by convention (ADR 0025).")
    short_name: str = Field(
        description="The name without `heinzel-`: its command group (`hz <short_name>`); for first-party plugins "
        "also what `hz system plugin add` takes."
    )
    description: str = Field(description="What the plugin does, in its authors' words.")
    source: Literal["first-party", "github"] = Field(
        description="Where it was found: `first-party` (maintained with Heinzelmännchen) or `github` (a repository "
        "with the topic heinzel-plugin)."
    )
    community: bool = Field(description="Whether it is a community plugin, not maintained with Heinzelmännchen.")
    install_spec: str = Field(description="What to pass to `hz system plugin add` to install it.")
    url: str = Field(description="Where to read about it.")
    repository_url: str | None = Field(
        description="The repository `install_spec` installs from, as a browser opens it (`/tree/<ref>/<dir>` for a "
        "subdirectory); none when it names no https repository."
    )
    stars: int | None = Field(description="GitHub stars; none for first-party plugins.")
    installed: bool = Field(description="Whether a plugin of this name is installed.")


class PluginSearch(BaseModel):
    """Plugins one can install, and what kept the search from being complete."""

    plugins: list[AvailablePlugin] = Field(description="First-party plugins first, then community plugins by stars.")
    warnings: list[str] = Field(description="Sources that could not be searched, e.g. GitHub while offline.")


class PluginCandidate(BaseModel):
    """What `hz system plugin add` would install: the code, where it comes from and who publishes it."""

    spec: str = Field(description="What was asked for, as given.")
    requirement: str = Field(
        description="What is installed: pinned to the version or git commit read here wherever it could be. Pass "
        "it back as `spec` with `confirm` to install exactly what was shown."
    )
    distribution: str | None = Field(description="Distribution name, when it could be read before installing.")
    version: str | None = Field(description="The version that would be installed, when known.")
    summary: str = Field(description="What it does, in its authors' words; empty when unknown.")
    source: Source = Field(description="`pypi` or `git`; `path` only for a first-party plugin in a checkout.")
    url: str = Field(description="Where to read about it: its repository or PyPI page.")
    publisher: str | None = Field(description="Who publishes it: the repository owner or the package's author.")
    first_party: bool = Field(description="Whether it is maintained and released with Heinzelmännchen.")
    private: bool | None = Field(
        description="Whether the repository is private (read with your gh login); none when unknown."
    )
    revision: str | None = Field(description="The git commit that would be installed, for a git source.")
    installed_version: str | None = Field(description="The version installed now, when this replaces it.")
    warnings: list[str] = Field(description="What could not be checked, e.g. metadata of a host other than GitHub.")


class PluginChange(BaseModel):
    """What a plugin change would do or did, and whether running servers must restart to see it."""

    action: Action = Field(description="`add`, `develop`, `remove`, `enable` or `disable`.")
    done: bool = Field(description="Whether it was carried out; false for `add` without `confirm`, which only shows.")
    distribution: str | None = Field(description="The plugin changed, when known.")
    candidate: PluginCandidate | None = Field(description="For `add`: what is installed, to show before confirming.")
    plugins: list[PluginInfo] = Field(description="The plugins installed afterwards (now, if not done).")
    restart_required: bool = Field(
        description="Whether a running `hz ui` or MCP server must restart to load the change; the web app restarts "
        "its server with `POST /api/restart`. Every new `hz` command sees it at once."
    )
    next: list[str] = Field(description="Commands to run next, e.g. the one that confirms.")
    previewed: bool = Field(
        default=False, description="Whether a terminal already showed the preview and asked; the CLI says no more."
    )


class PluginEvent(BaseModel):
    """A plugin change as recorded in the history, one line of `plugins.jsonl` in the data directory."""

    at: datetime = Field(description="When it was carried out.")
    action: Action = Field(description="`add`, `develop`, `remove`, `enable` or `disable`.")
    distribution: str | None = Field(description="The plugin changed, when known.")
    requirement: str | None = Field(description="What was installed, exactly, for `add` and `develop`.")
    source: Source | None = Field(description="Where it came from, for `add` and `develop`.")
    version: str | None = Field(description="The version installed or removed, when known.")
    publisher: str | None = Field(description="Who publishes it, as shown before confirming.")
