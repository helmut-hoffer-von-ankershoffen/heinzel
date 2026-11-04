"""Models of the documentation module: what a plugin brings, as the kernel holds it, ready to render (ADR 0082)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from heinzel import Theme

Kind = Literal["connector", "theme", "capability", "playbook", "layer"]
"""What a plugin is; `PluginInfo.kinds` plus `layer` for a plugin whose module sits in a company's tree (ADR 0040)."""


class OperationDoc(BaseModel):
    """One operation as every head sees it."""

    name: str = Field(description="The operation's own name, e.g. `list`.")
    module: str = Field(description="The qualified name of the module that declares it, e.g. `system.plugin`.")
    summary: str = Field(
        description="Its first docstring line: the CLI help, the MCP description, the OpenAPI summary."
    )
    description: str = Field(default="", description="The rest of its docstring, in Markdown.")
    command: str = Field(description="The CLI command, e.g. `hz system plugin list`.")
    usage: str = Field(description="The CLI command with its arguments and options, e.g. `hz clock [--zone TEXT]`.")
    tool: str | None = Field(description="The MCP tool's name, e.g. `system_plugin_list`; none if MCP doesn't have it.")
    http_tool: str | None = Field(default=None, description="The tool in unauthenticated HTTP MCP; none if absent.")
    endpoint: str | None = Field(
        description="The REST endpoint, e.g. `GET /system/plugin/list`; none if REST lacks it."
    )
    web_endpoint: str | None = Field(description="The web app's API endpoint, e.g. `GET /api/clock`; none if absent.")
    private_endpoint: str | None = Field(default=None, description="The scoped, authenticated private API endpoint.")
    private: bool = Field(default=False, description="Whether this operation requires a trusted authenticated head.")
    read_only: bool = Field(description="Whether it only reads; otherwise it changes state.")
    audience: str = Field(description="`everyday` or `expert` (ADR 0041).")
    heads: list[str] | None = Field(description="The heads it is restricted to; none means every head.")
    requires: list[str] = Field(description="Capabilities it asks for besides its module's, e.g. `tracker:read`.")
    input_schema: dict[str, Any] = Field(description="The JSON Schema of what a caller supplies: the MCP input schema.")
    parameters: list["ParameterDoc"] = Field(description="Its public parameters, in order.")


class ParameterDoc(BaseModel):
    """One public parameter of an operation."""

    name: str = Field(description="The parameter's name, e.g. `zone`.")
    type: str = Field(description="Its type as the schema says it, e.g. `string`, `integer`, `array`.")
    required: bool = Field(description="Whether a caller must give it; on the CLI, required ones are arguments.")
    default: Any = Field(default=None, description="Its default, when it has one.")
    description: str = Field(description="What it is, from `Field(description=...)`.")
    option: str = Field(description="How the CLI takes it: `ZONE` for an argument, `--zone TEXT` for an option.")


class SettingDoc(BaseModel):
    """One setting of a module's settings model."""

    key: str = Field(description="The key in the module's TOML table, e.g. `zones`.")
    type: str = Field(description="Its type as the JSON Schema says it, e.g. `array of string`; `secret` for secrets.")
    default: str = Field(description="Its default as written in Python, or `platform-specific`.")
    description: str = Field(description="What it does.")
    environment: str = Field(description="The environment variable that sets it, e.g. `HZ_CLOCK__ZONES`.")
    ignored_in_project: bool = Field(default=False, description="Whether a project `hz.toml` can't set it (ADR 0028).")


class CredentialDoc(BaseModel):
    """A credential a module needs: its name and purpose, never a value (ADR 0030)."""

    name: str = Field(description="The credential's name, e.g. `token`.")
    purpose: str = Field(description="What it is for and where to get one.")
    service: str = Field(description="The keychain service it is kept under.")
    command: str = Field(description="How to store it, e.g. `hz system credential set github token`.")


class PrerequisiteDoc(BaseModel):
    """An external program a module needs: what it is for, when, and how it gets onto the machine (ADR 0112)."""

    executable: str = Field(description="The command name, e.g. `gh`.")
    title: str = Field(description="The tool's name for people, e.g. `GitHub CLI`.")
    purpose: str = Field(description="What the module uses it for.")
    when: str = Field(description="`install`, `setup` or `feature`: how early the module asks for it.")
    condition: str = Field(description="When a selection makes it necessary; empty when it always is.")
    minimum_version: str | None = Field(description="The oldest compatible version, when the module names one.")
    macos: str = Field(description="What hz does on macOS: the Homebrew command, or the owner's instructions.")
    linux: str = Field(description="What to do by hand on Linux; hz installs nothing there.")
    check: str = Field(
        description="How to see whether it is usable, e.g. `hz system prerequisite check --owner clock`."
    )


class AdapterDoc(BaseModel):
    """An adapter a plugin brings: a capability it serves (ADR 0028)."""

    adapter: str = Field(description="The name bindings use, e.g. `github`.")
    capability: str = Field(description="The capability it implements, e.g. `tracker`.")
    modes: list[str] = Field(description="The modes it serves, e.g. `read`.")
    hosts: list[str] = Field(description="Hosts it sends data to; empty when its settings decide them.")
    hosts_from_settings: bool = Field(description="Whether its settings decide the hosts.")
    binding: str = Field(description="How to bind it, e.g. `hz system config set capabilities.tracker github`.")


class ColourDoc(BaseModel):
    """One colour token of a theme, in both modes."""

    token: str = Field(description="The token, e.g. `primary` (`--color-primary`).")
    light: str | None = Field(description="The colour in light mode, `#rrggbb`; none when the theme leaves it out.")
    dark: str | None = Field(description="The colour in dark mode.")


class ThemeDoc(BaseModel):
    """A theme a plugin offers (ADR 0044)."""

    name: str = Field(description="What `hz system theme use` takes, e.g. `acme`.")
    title: str = Field(description="What people pick it by.")
    module: str = Field(description="The module offering it.")
    colours: list[ColourDoc] = Field(description="Every colour token, light and dark.")
    font_sans: str = Field(description="The font stack of text.")
    font_mono: str = Field(description="The font stack of code.")
    radius: str = Field(description="The corner radius of cards and dialogs.")
    border_width: str = Field(description="The width of borders.")
    fonts: list[str] = Field(description="Font families it ships files for.")
    logo: bool = Field(description="Whether it ships a logo.")
    command: str = Field(description="How to pick it, e.g. `hz system theme use acme`.")
    definition: Theme | None = Field(
        default=None, description="The complete checked theme, including variants and backdrops."
    )


class AssetTypeDoc(BaseModel):
    """An asset type of a playbook (ADR 0037)."""

    name: str = Field(description="The asset type, e.g. `weekly-report`.")
    description: str = Field(description="What it is.")
    hints: list[str] = Field(description="Where candidates may be, one line per discovery hint.")
    kept_by: str | None = Field(description="The template whose output keeps it true; none if nothing does.")


class PlaybookDoc(BaseModel):
    """A playbook a module brings (ADR 0037)."""

    module: str = Field(description="The module bringing it.")
    summary: str = Field(description="What the playbook is for.")
    assets: list[AssetTypeDoc] = Field(description="Its asset types.")
    templates: list[str] = Field(description="The names of its contract templates.")
    defaults: dict[str, Any] = Field(description="The settings it recommends, by `<section>.<key>`.")


class JobDoc(BaseModel):
    """A job the daemon runs for the module (ADR 0020)."""

    name: str = Field(description="The job's name.")
    module: str = Field(description="The module declaring it.")
    summary: str = Field(description="What it does.")
    schedule: str = Field(description="Its cron schedule, e.g. `0 9 * * MON`.")
    catch_up: str = Field(description="What it does about missed slots: `latest`, `all` or `skip`.")
    timeout: str = Field(description="How long a run may take, e.g. `15m`.")
    requires: list[str] = Field(description="Capabilities it reads.")


class LayerDoc(BaseModel):
    """A layer a module declares (ADR 0040)."""

    module: str = Field(description="The layer's module name, e.g. `acme-payments`.")
    parent: str | None = Field(description="The layer above it; none for the company.")
    site: str | None = Field(description="The site layer it names, if any.")
    roles: list[str] = Field(description="Roles it adds to the vocabulary.")


class LetDoc(BaseModel):
    """A let the renderer has no section for: listed by kind and name, so a new kind shows up at once."""

    module: str = Field(description="The module declaring it.")
    kind: str = Field(description="The let's class, e.g. `InfoLet`.")
    name: str = Field(description="The let's name.")
    summary: str = Field(default="", description="Its summary, when the let has one.")


class SetupDoc(BaseModel):
    """A module's setup step (ADR 0032)."""

    module: str = Field(description="The module whose step it is.")
    summary: str = Field(description="What it asks.")
    required: bool = Field(description="Whether `hz setup` runs it without offering to skip it.")
    phase: str = Field(description="Where in `hz setup` it runs.")
    command: str = Field(description="How to run it alone, e.g. `hz setup clock`.")


class ModuleDoc(BaseModel):
    """One module of a plugin with everything it advertises."""

    qualname: str = Field(description="The qualified name, e.g. `system.plugin`.")
    summary: str = Field(description="What it does.")
    requires: list[str] = Field(description="Capabilities the whole module reads (ADR 0028).")
    operations: list[OperationDoc] = Field(description="Its operations, in declaration order.")
    settings: list[SettingDoc] = Field(description="Its settings; the table is `[<qualname>]`.")
    credentials: list[CredentialDoc] = Field(description="The credentials it needs.")
    prerequisites: list[PrerequisiteDoc] = Field(
        default_factory=list[PrerequisiteDoc], description="The external programs it needs."
    )
    health_checks: bool = Field(description="Whether `hz system doctor` runs checks of its.")
    info: bool = Field(description="Whether it contributes a section to `hz system info`.")
    setup: SetupDoc | None = Field(description="Its setup step, if any.")


class SceneStep(BaseModel):
    """One step of a UI scene: what the recorder does, and what it says about it."""

    model_config = ConfigDict(extra="forbid")  # a misspelt key is a grammar error, not a silent no-op

    action: Literal["goto", "click", "hover", "fill", "key", "wait", "screenshot"] = Field(
        description="`goto` a route, `click` or `hover` an element by role and name, `fill` a field, press a `key`, "
        "`wait` for text, or take a `screenshot` with a caption."
    )
    target: str = Field(default="", description="The route, the element's name, the field's label, the key or text.")
    role: str = Field(default="", description="The element's ARIA role for `click`, `hover` and `fill`, e.g. `button`.")
    value: str = Field(default="", description="What `fill` types.")
    caption: str = Field(default="", description="What a `screenshot` shows, one line; the frame's caption.")
    hold: float = Field(default=1.5, description="Seconds a `screenshot` frame stays on screen in the animation.")
    optional: bool = Field(default=False, description="Skip the step when its element or text isn't there.")


class Scene(BaseModel):
    """What one recording shows: a CLI scene is command lines, a UI scene is steps in the web app."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Lowercase words joined by dashes; the recording is `assets/<name>.gif`.")
    title: str = Field(description="One line, the recording's heading and alt text.")
    kind: Literal["cli", "ui"] = Field(description="`cli`: typed in a terminal; `ui`: driven in the web app.")
    commands: list[str] = Field(
        default_factory=list[str], description="CLI scene: the `hz` command lines typed, in order."
    )
    hold: float = Field(default=2.5, description="CLI scene: seconds each command's output stays on screen.")
    steps: list[SceneStep] = Field(default_factory=list[SceneStep], description="UI scene: the steps, in order.")
    derived: bool = Field(default=False, description="Whether it was derived from the operations, not declared.")


class Recording(BaseModel):
    """The files a rendered scene left behind, as the page embeds them."""

    scene: str = Field(description="The scene's name.")
    animation: str | None = Field(description="The animated image, relative to the page; none if not recorded yet.")
    animation_dark: str | None = Field(
        default=None, description="The same in dark mode, for UI scenes; none if absent."
    )
    still: str | None = Field(description="A still for reduced motion, relative to the page; none if not recorded.")
    still_dark: str | None = Field(default=None, description="The still in dark mode; none if absent.")
    video: str | None = Field(description="The MP4, relative to the page; none if not recorded.")
    transcript: str = Field(default="", description="What the recording shows, as text: commands and their output.")


class PluginPage(BaseModel):
    """Everything a plugin's page says, from the kernel; `heinzel` itself when built-in modules are included."""

    distribution: str = Field(description="Distribution name, e.g. `heinzel-clock`.")
    name: str = Field(description="The short name: its command group, e.g. `clock`.")
    summary: str = Field(description="What it does: its metadata's summary, or its first module's.")
    version: str = Field(description="The installed version.")
    kinds: list[Kind] = Field(description="What it is: connector, theme, capability, playbook, layer.")
    first_party: bool = Field(description="Whether it is released with Heinzelmännchen (ADR 0017).")
    builtin: bool = Field(description="Whether these are the core's own modules, not a plugin's.")
    repository: str | None = Field(description="Its source repository, from its metadata; none if it names none.")
    license: str | None = Field(description="Its licence, from its metadata; none if it names none.")
    install: str = Field(description="How to install it, e.g. `hz system plugin add clock --confirm`.")
    enabled: bool = Field(description="Whether its modules are loaded.")
    modules: list[ModuleDoc] = Field(description="Its modules, parents first.")
    adapters: list[AdapterDoc] = Field(description="The adapters it brings.")
    themes: list[ThemeDoc] = Field(description="The themes it offers.")
    playbooks: list[PlaybookDoc] = Field(description="The playbooks it brings.")
    jobs: list[JobDoc] = Field(description="The jobs its modules declare.")
    layers: list[LayerDoc] = Field(description="The layers its modules declare.")
    other_lets: list[LetDoc] = Field(description="Lets of kinds the renderer has no section for.")
    scenes: list[Scene] = Field(description="What to record: declared in `docs/scenes.toml`, else derived.")
    recordings: list[Recording] = Field(description="The recordings found beside the page, by scene.")


class Documentation(BaseModel):
    """The documentation of every plugin installed, and the core's modules when asked for."""

    pages: list[PluginPage] = Field(description="One per distribution, first-party first, then by name.")
    warnings: list[str] = Field(description="What couldn't be documented, e.g. a plugin that failed to load.")


class BuildResult(BaseModel):
    """What `hz system plugin docs build` wrote, or would write."""

    directory: str = Field(description="Where the pages are.")
    written: list[str] = Field(description="Files written, relative to the directory; empty with `check`.")
    stale_pages: list[str] = Field(description="With `check`: the files that differ from what would be written.")
    unchanged: int = Field(description="Files that were already current.")
    stale: bool = Field(description="With `check`: whether any page is out of date, or none exists yet.")
    next: list[str] = Field(description="What to run next.")
