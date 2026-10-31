"""Service of the documentation module: the model of what each plugin brings, read from the kernel (ADR 0082).

Nothing here parses source. Modules, lets and the exposure rule come from the kernel; what was installed from where
comes from the plugin service; the scenes come from `docs/scenes.toml` when the plugin declares them.
"""

import inspect
import json
import re
import shlex
import tomllib
from collections import defaultdict
from collections.abc import Iterable
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any, cast

from packaging.utils import canonicalize_name
from pydantic import ValidationError
from pydantic_core import PydanticUndefined, to_jsonable_python

from heinzel import HeinzelError, Homebrew, Kernel, __version__
from heinzel.kernel import (
    CORE_DISTRIBUTION,
    CredentialLet,
    Exposed,
    HealthLet,
    InfoLet,
    JobLet,
    LayerLet,
    ModuleRecord,
    OperationLet,
    PlaybookLet,
    PrerequisiteLet,
    ProviderLet,
    RequirementLet,
    ServiceLet,
    SettingsLet,
    SetupLet,
    ThemeLet,
    env_prefix,
    exposed,
    format_duration,
    input_schema,
)
from heinzel.modules.system.plugin._models import PluginInfo
from heinzel.modules.system.plugin._service import REPOSITORY, PluginService, short_name
from heinzel.modules.system.plugin.docs._files import read_recording
from heinzel.modules.system.plugin.docs._models import (
    AdapterDoc,
    AssetTypeDoc,
    ColourDoc,
    CredentialDoc,
    Documentation,
    JobDoc,
    Kind,
    LayerDoc,
    LetDoc,
    ModuleDoc,
    OperationDoc,
    ParameterDoc,
    PlaybookDoc,
    PluginPage,
    PrerequisiteDoc,
    Recording,
    Scene,
    SettingDoc,
    SetupDoc,
    ThemeDoc,
)

SCENES_FILE = Path("docs") / "scenes.toml"
"""Where a plugin declares what to record, relative to its repository."""
ASSETS = "assets"
"""The directory beside a page that holds its recordings and swatches."""
HEADS = ("cli", "mcp", "mcp-http", "api", "api-private", "web")
DERIVED_COMMANDS = 4
"""How many operations the derived CLI scene runs after `--help`."""
SECTIONED_LETS = (
    OperationLet,
    SettingsLet,
    CredentialLet,
    PrerequisiteLet,
    ProviderLet,
    RequirementLet,
    ThemeLet,
    PlaybookLet,
    JobLet,
    LayerLet,
    SetupLet,
    HealthLet,
    InfoLet,
    ServiceLet,
)
"""Let kinds the page has a section for, or that are internal (services): every other kind is listed under
"Also declares", so a kind added later shows up before the renderer learns about it."""
TYPE_WORDS = {"string": "TEXT", "integer": "INTEGER", "number": "FLOAT", "boolean": "BOOLEAN", "array": "TEXT"}
CLI_WORDS = {"system.runner.chat": ("chat",)}
"""Modules the CLI mounts elsewhere than their path says: the chat is `hz chat` (ADR 0045)."""


class DocsService:
    """Builds the documentation model from the kernel and the plugin service."""

    def __init__(self, kernel: Kernel, plugins: PluginService) -> None:
        """Create the service; the kernel and the plugin service are injected."""
        self._kernel = kernel
        self._plugins = plugins

    def documentation(
        self,
        *,
        plugin: str | None = None,
        scenes: Path | None = None,
        builtin: bool = False,
        assets: Path | None = None,
    ) -> Documentation:
        """The pages of every installed plugin (one with `plugin`), the core's own when `builtin`.

        `scenes` is a `docs/scenes.toml`; its scenes apply to the one plugin asked for, or to the core when `builtin`
        is the only thing asked for, or to the plugin whose name the file declares in `[scenes] plugin`. `assets` is
        the directory the pages are built in, where recordings are looked for.

        Raises:
            HeinzelError: If `plugin` names no installed plugin, or the scenes file is invalid.
        """
        declared = read_scenes(scenes) if scenes is not None else {}
        installed = {canonicalize_name(info.distribution): info for info in self._plugins.installed()}
        wanted = canonicalize_name(plugin.strip()) if plugin else None
        if wanted and wanted.removeprefix("heinzel-") in {short_name(d) for d in installed}:
            wanted = next(d for d in installed if short_name(d) == wanted.removeprefix("heinzel-"))
        if wanted and wanted not in installed and wanted != CORE_DISTRIBUTION:
            names = ", ".join(sorted(short_name(d) for d in installed)) or "none"
            msg = f"No plugin {plugin!r} is installed (installed: {names}); try `hz system plugin list`"
            raise HeinzelError(msg)
        records: dict[str, list[ModuleRecord]] = defaultdict(list)
        for record in self._kernel.modules:
            records[
                CORE_DISTRIBUTION if record.origin.builtin else canonicalize_name(record.origin.distribution)
            ].append(record)
        pages: list[PluginPage] = []
        warnings: list[str] = []
        known = {CORE_DISTRIBUTION, "", *installed, *(short_name(d) for d in installed)}
        for owner in declared:
            if owner not in known:
                names = ", ".join(sorted(short_name(d) for d in installed)) or "none"
                warnings.append(
                    f"{scenes} names the plugin {owner!r}, which isn't installed (installed: {names}); its scenes "
                    "weren't used"
                )
        if builtin and wanted in {None, CORE_DISTRIBUTION}:
            page_scenes = declared.get(CORE_DISTRIBUTION) or declared.get("") or []
            pages.append(self._core_page(records[CORE_DISTRIBUTION], page_scenes, assets))
        for name, info in sorted(installed.items(), key=lambda item: (not item[1].first_party, item[0])):
            if wanted and name != wanted:
                continue
            if info.error:
                warnings.append(f"{info.distribution}: not documented, it failed to load: {info.error}")
            own = (
                declared.get(name) or declared.get(short_name(name)) or ([] if len(installed) > 1 else declared.get(""))
            )
            pages.append(self._plugin_page(info, records.get(name, []), own or [], assets))
        return Documentation(pages=pages, warnings=warnings)

    # Pages.

    def _core_page(self, records: list[ModuleRecord], scenes: list[Scene], assets: Path | None) -> PluginPage:
        modules = sorted(records, key=lambda r: r.path)
        return self._page(
            distribution=CORE_DISTRIBUTION,
            name="hz",
            summary="Heinzelmännchen itself: the kernel's own modules, installed with `hz`.",
            version=__version__,
            kinds=["capability"],
            first_party=True,
            builtin=True,
            repository=REPOSITORY,
            license="MIT",
            install=f"uv tool install git+{REPOSITORY}",
            enabled=True,
            records=modules,
            scenes=scenes,
            assets=assets,
        )

    def _plugin_page(
        self, info: PluginInfo, records: list[ModuleRecord], scenes: list[Scene], assets: Path | None
    ) -> PluginPage:
        modules = sorted(records, key=lambda r: r.path)
        kinds: list[Kind] = [*info.kinds]
        if info.layers:
            kinds.append("layer")
        install = f"hz system plugin add {install_spec(info)} --confirm"
        return self._page(
            distribution=info.distribution,
            name=info.name,
            summary=info.summary or (modules[0].module.summary if modules else ""),
            version=info.version,
            kinds=kinds,
            first_party=info.first_party,
            builtin=False,
            repository=info.homepage or info.source_url,
            license=license_of(info.distribution),
            install=install,
            enabled=info.enabled,
            records=modules,
            scenes=scenes,
            assets=assets,
        )

    def _page(  # noqa: PLR0913 - every field of the page, by keyword
        self,
        *,
        distribution: str,
        name: str,
        summary: str,
        version: str,
        kinds: list[Kind],
        first_party: bool,
        builtin: bool,
        repository: str | None,
        license: str | None,  # noqa: A002 - the page's field
        install: str,
        enabled: bool,
        records: list[ModuleRecord],
        scenes: list[Scene],
        assets: Path | None,
    ) -> PluginPage:
        surfaces = {head: exposed(self._kernel, head) for head in HEADS}
        modules = [self._module(record, surfaces) for record in records]
        lets = [(record, let) for record in records for let in record.module.lets]
        resolved = scenes or ([] if builtin else self._derived_scenes(name, modules))
        return PluginPage(
            distribution=distribution,
            name=name,
            summary=summary,
            version=version,
            kinds=kinds,
            first_party=first_party,
            builtin=builtin,
            repository=repository,
            license=license,
            install=install,
            enabled=enabled,
            modules=modules,
            adapters=[_adapter(let) for _, let in lets if isinstance(let, ProviderLet)],
            themes=[_theme(record, let) for record, let in lets if isinstance(let, ThemeLet)],
            playbooks=[_playbook(record, let) for record, let in lets if isinstance(let, PlaybookLet)],
            jobs=[_job(record, let) for record, let in lets if isinstance(let, JobLet)],
            layers=[
                LayerDoc(module=let.name, parent=let.parent, site=let.site, roles=list(let.roles))
                for _, let in lets
                if isinstance(let, LayerLet)
            ],
            other_lets=[
                LetDoc(
                    module=record.qualname,
                    kind=type(let).__name__,
                    name=let.name,
                    summary=str(getattr(let, "summary", "") or ""),
                )
                for record, let in lets
                if not isinstance(let, SECTIONED_LETS)
            ],
            scenes=resolved,
            recordings=[recording(scene, assets / distribution if assets else None) for scene in resolved],
        )

    def _module(self, record: ModuleRecord, surfaces: dict[str, list[Exposed]]) -> ModuleDoc:
        lets = record.module.lets
        seen: list[OperationLet] = []
        items: list[Exposed] = []
        for head in HEADS:  # the CLI first, so its path names the operation; any head's is enough to document it
            for item in surfaces[head]:
                if item.record is record and item.operation not in seen:
                    seen.append(item.operation)
                    items.append(item)
        operations = [self._operation(item, surfaces) for item in items]
        settings = [let for let in lets if isinstance(let, SettingsLet)]
        setup = next((let for let in lets if isinstance(let, SetupLet)), None)
        return ModuleDoc(
            qualname=record.qualname,
            summary=record.module.summary,
            requires=[str(let.requirement) for let in lets if isinstance(let, RequirementLet)],
            operations=operations,
            settings=[s for let in settings for s in _settings(record.qualname, let)],
            credentials=[_credential(record, let) for let in lets if isinstance(let, CredentialLet)],
            prerequisites=[_prerequisite(record, let) for let in lets if isinstance(let, PrerequisiteLet)],
            health_checks=any(isinstance(let, HealthLet) for let in lets),
            info=any(isinstance(let, InfoLet) for let in lets),
            setup=SetupDoc(
                module=record.qualname,
                summary=setup.summary,
                required=setup.required,
                phase=setup.phase,
                command=f"hz setup {record.qualname}",
            )
            if setup
            else None,
        )

    def _operation(self, item: Exposed, surfaces: dict[str, list[Exposed]]) -> OperationDoc:
        operation = item.operation
        words = cli_words(item.path)
        schema = input_schema(item)
        properties: dict[str, dict[str, Any]] = schema.get("properties", {})
        required = set(schema.get("required", []))
        parameters = [
            _parameter(name, properties.get(name, {}), name in required, parameter)
            for name, parameter in ((p.name, p) for p in item.parameters)
        ]
        verb = "GET" if operation.read_only else "POST"
        path = "/".join(item.path)
        description = _description(inspect.getdoc(operation.func) or "")
        in_head = {head: any(o.operation is operation for o in surfaces[head]) for head in HEADS}
        command = " ".join(("hz", *words)) if in_head["cli"] else ""
        return OperationDoc(
            name=operation.name,
            module=item.record.qualname,
            summary=operation.summary,
            description=description,
            command=command,
            usage=" ".join((command, *(p.option for p in parameters))) if command else "",
            tool=item.name if in_head["mcp"] else None,
            http_tool=item.name if in_head["mcp-http"] else None,
            endpoint=f"{verb} /{path}" if in_head["api"] else None,
            private_endpoint=f"{verb} /{path}" if in_head["api-private"] else None,
            web_endpoint=f"{verb} /api/{path}" if in_head["web"] else None,
            private=operation.private,
            read_only=operation.read_only,
            audience=operation.audience,
            heads=sorted(operation.heads) if operation.heads is not None else None,
            requires=[str(r) for r in operation.requires],
            input_schema=schema,
            parameters=parameters,
        )

    def _derived_scenes(self, name: str, modules: list[ModuleDoc]) -> list[Scene]:
        """The CLI scene of a plugin without a scenes file: `--help`, then read-only commands that need no argument."""
        cli_modules = [module for module in modules if any(op.command for op in module.operations)]
        if not cli_modules:
            return []
        path = cli_words(tuple(cli_modules[0].qualname.split(".")))
        commands = [" ".join(("hz", *path, "--help"))]
        candidates = [
            op
            for module in modules
            for op in module.operations
            if op.command and op.read_only and not any(p.required for p in op.parameters) and op.name != "info"
        ]
        candidates.sort(key=lambda op: (op.audience != "everyday", op.command))
        for index, op in enumerate(candidates[:DERIVED_COMMANDS]):
            commands.append(op.command)
            if index == 0:
                commands.append(f"{op.command} --json")
        return [Scene(name="cli", title=f"{name} in the terminal", kind="cli", commands=commands, derived=True)]


DOCSTRING_SECTIONS = ("Args:", "Arguments:", "Raises:", "Returns:", "Yields:", "Example:", "Examples:", "Note:")


def _description(doc: str) -> str:
    """A docstring's body after its summary line, up to its first Google-style section: what a reader needs."""
    body = doc.split("\n", 1)[1] if "\n" in doc else ""
    kept: list[str] = []
    for line in body.splitlines():
        if line.strip() in DOCSTRING_SECTIONS:
            break
        kept.append(line)
    return "\n".join(kept).strip()


def cli_words(path: tuple[str, ...]) -> tuple[str, ...]:
    """The CLI words of an operation's path: the path, except where the CLI mounts a module elsewhere."""
    for qualname, words in CLI_WORDS.items():
        prefix = tuple(qualname.split("."))
        if path[: len(prefix)] == prefix:
            return (*words, *path[len(prefix) :])
    return path


def license_of(name: str) -> str | None:
    """A distribution's licence from its metadata (the SPDX expression, else the License field or classifier)."""
    try:
        metadata = distribution(name).metadata
    except PackageNotFoundError:
        return None
    expression = metadata.get("License-Expression") or metadata.get("License")
    if expression and "\n" not in str(expression):
        return str(expression)
    for classifier in metadata.get_all("Classifier") or []:
        if str(classifier).startswith("License ::"):
            return str(classifier).rsplit(" :: ", 1)[-1]
    return None


def install_spec(info: PluginInfo) -> str:
    """What `hz system plugin add` takes for a plugin that isn't first-party: its name on PyPI, else its repository."""
    if info.source_kind == "pypi" or not info.homepage:
        return info.distribution
    return f"git+{info.homepage}"


def _parameter(name: str, schema: dict[str, Any], required: bool, parameter: inspect.Parameter) -> ParameterDoc:
    kind = _type_of(schema)
    flag = name.replace("_", "-")
    if required:
        option = name.upper()
    elif kind == "boolean":
        option = f"[--{flag}/--no-{flag}]"
    else:
        option = f"[--{flag} {TYPE_WORDS.get(kind, 'TEXT')}]"
    default = None if parameter.default is inspect.Parameter.empty else to_jsonable_python(parameter.default)
    return ParameterDoc(
        name=name,
        type=kind,
        required=required,
        default=default,
        description=str(schema.get("description", "")),
        option=option,
    )


def _table(value: object) -> dict[str, Any] | None:
    """`value` as a JSON object, or None when it is anything else."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _type_of(schema: dict[str, Any]) -> str:
    """A JSON Schema's type in words: `string`, `array of string`, `string or null`, `secret` for passwords.

    Never a `|`: the pages put types in Markdown table cells.
    """
    if schema.get("format") == "password":
        return "secret"
    if "enum" in schema:
        return " or ".join(f"`{value}`" for value in schema["enum"])
    if "type" in schema:
        kind = str(schema["type"])
        items = _table(schema.get("items"))
        return f"array of {_type_of(items)}" if kind == "array" and items is not None else kind
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    options = schema.get("anyOf") or schema.get("oneOf")
    return " or ".join(_type_of(option) for option in options) if options else "object"


def _settings(section: str, let: SettingsLet) -> list[SettingDoc]:
    schema = let.model.model_json_schema()
    properties: dict[str, dict[str, Any]] = schema.get("properties", {})
    found: list[SettingDoc] = []
    for key, field in let.model.model_fields.items():
        if field.default_factory is not None:
            default = "computed"
        elif field.default is PydanticUndefined:
            default = "platform-specific"
        else:
            default = json.dumps(to_jsonable_python(field.default), ensure_ascii=False)
        found.append(
            SettingDoc(
                key=key,
                type=_type_of(_resolve(properties.get(key, {}), schema)),
                default=default,
                description=field.description or "",
                environment=f"{env_prefix(section)}{key.upper()}",
                ignored_in_project=key in let.model.ignored_in_project,
            )
        )
    return found


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """A property's schema with a top-level `$ref` followed, so a model-typed setting shows its shape's name."""
    if "$ref" in schema and len(schema) <= 2:  # noqa: PLR2004 - a bare reference, perhaps with a description
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        target = root.get("$defs", {}).get(name)
        if isinstance(target, dict):
            return {**target, "title": name}
    return schema


def _credential(record: ModuleRecord, let: CredentialLet) -> CredentialDoc:
    service = let.service or record.qualname
    command = f"hz system credential set {let.name}" + (f" --service {service}" if let.service else "")
    return CredentialDoc(name=let.name, purpose=let.purpose, service=service, command=command)


def _prerequisite(record: ModuleRecord, let: PrerequisiteLet) -> PrerequisiteDoc:
    """The page's row, from the one declaration doctor, setup and the installer read; no predicate runs."""
    if isinstance(let.macos, Homebrew):
        macos = f"`brew install {'--cask ' if let.macos.cask else ''}{let.macos.package}`"
    elif let.macos is not None:
        macos = let.macos.text
    else:
        macos = "Not needed or no installer declared."
    return PrerequisiteDoc(
        executable=let.name,
        title=let.title or let.name,
        purpose=let.purpose,
        when=let.when,
        condition=let.condition,
        minimum_version=let.minimum_version,
        macos=macos,
        linux=let.linux.text,
        check=f"hz system prerequisite check --owner {record.qualname}"
        + (" --context feature" if let.when == "feature" else ""),
    )


def _adapter(let: ProviderLet) -> AdapterDoc:
    return AdapterDoc(
        adapter=let.adapter,
        capability=let.capability,
        modes=sorted(let.modes),
        hosts=list(let.hosts),
        hosts_from_settings=let.configured_hosts is not None,
        binding=f"hz system config set capabilities.{let.capability} {let.adapter}",
    )


def _theme(record: ModuleRecord, let: ThemeLet) -> ThemeDoc:
    theme = let.theme
    light, dark = theme.light.model_dump(), theme.dark.model_dump()
    return ThemeDoc(
        name=theme.name,
        title=theme.title,
        module=record.qualname,
        colours=[ColourDoc(token=key, light=light[key], dark=dark[key]) for key in light if key != "backdrop"],
        font_sans=theme.font_sans,
        font_mono=theme.font_mono,
        radius=theme.radius,
        border_width=theme.border_width,
        fonts=sorted({font.family for font in theme.fonts}),
        logo=theme.logo is not None,
        command=f"hz system theme use {theme.name}",
        definition=theme,
    )


def _playbook(record: ModuleRecord, let: PlaybookLet) -> PlaybookDoc:
    return PlaybookDoc(
        module=record.qualname,
        summary=let.summary,
        assets=[
            AssetTypeDoc(
                name=asset.name,
                description=asset.description,
                hints=[hint.summary or f"{hint.read} matching {hint.match or 'anything'}" for hint in asset.hints],
                kept_by=asset.kept_by,
            )
            for asset in let.assets
        ],
        templates=[template.name for template in let.templates],
        defaults=dict(to_jsonable_python(dict(let.defaults))),
    )


def _job(record: ModuleRecord, let: JobLet) -> JobDoc:
    return JobDoc(
        name=let.name,
        module=record.qualname,
        summary=let.summary,
        schedule=let.schedule,
        catch_up=let.catch_up.value,
        timeout=format_duration(timedelta(seconds=let.timeout.total_seconds())),
        requires=[str(r) for r in let.requires],
    )


# Scenes and recordings.


def read_scenes(path: Path) -> dict[str, list[Scene]]:
    """The scenes a `docs/scenes.toml` declares, by the plugin they are for (`""` when the file names none).

    The file holds `[[cli]]` and `[[ui]]` tables; `[scenes] plugin = "<distribution>"` says whose they are, which a
    repository of several plugins needs. CLI commands must be `hz` commands, as the README demo's snippets must.

    Raises:
        HeinzelError: If the file can't be read or a scene is malformed.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"Can't read the scenes in {path}: {exc}"
        raise HeinzelError(msg) from exc
    return scenes_from(data, where=str(path))


def scenes_from(data: dict[str, Any], *, where: str = "docs/scenes.toml") -> dict[str, list[Scene]]:
    """The scenes in a parsed scenes file, by plugin.

    Raises:
        HeinzelError: If a scene is malformed, a name repeats, or a CLI line isn't an `hz` command.
    """
    header = _table(data.get("scenes")) or {}
    named = header.get("plugin")
    owner = canonicalize_name(str(named)) if named else ""
    found: list[Scene] = []
    for kind in ("cli", "ui"):
        for index, table in enumerate(data.get(kind, []), 1):
            try:
                scene = Scene.model_validate({**table, "kind": kind})
            except ValidationError as exc:
                problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
                msg = f"{where}: [[{kind}]] #{index} is malformed: {problems}"
                raise HeinzelError(msg) from exc
            for problem in scene_problems(scene):
                msg = f"{where}: [[{kind}]] {scene.name!r}: {problem}"
                raise HeinzelError(msg)
            found.append(scene)
    names = [scene.name for scene in found]
    if duplicates := sorted({n for n in names if names.count(n) > 1}):
        msg = f"{where}: two scenes are named {duplicates[0]!r}"
        raise HeinzelError(msg)
    return {owner: found}


def scene_problems(scene: Scene) -> list[str]:
    """Why a scene can't be recorded: a bad name, a command that isn't `hz`, a UI scene without a screenshot."""
    problems: list[str] = []
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", scene.name) is None:
        problems.append("name must be lowercase words joined by dashes")
    if scene.kind == "cli":
        if not scene.commands:
            problems.append("a CLI scene needs `commands`")
        problems += [
            f"type real `hz` commands, at most piped into {', '.join(sorted(PIPE_INTO))}, nothing else: {line!r}"
            for line in scene.commands
            if not _is_hz(line)
        ]
    elif not any(step.action == "screenshot" for step in scene.steps):
        problems.append("a UI scene needs at least one `screenshot` step")
    if not scene.title.strip():
        problems.append("a scene needs a title")
    return problems


PIPE_INTO = frozenset({"jq", "head", "tail", "grep", "sort", "wc", "less", "cat"})
"""What a scene's `hz` command may be piped into: nothing that runs or writes anything else."""
SHELL_SYNTAX = re.compile(r"[;&<>`$\\\x00-\x1f\x7f-\x9f]")
READER_FLAGS = {
    "jq": re.compile(
        r"-(?:[crseMSaC]+)|--(?:compact-output|raw-output|slurp|exit-status|monochrome-output|sort-keys|ascii-output|color-output)"
    ),
    "head": re.compile(r"-(?:[0-9]+|[nc][0-9]+|[qv])|--(?:lines|bytes)=[0-9]+|--(?:quiet|verbose)"),
    "tail": re.compile(r"-(?:[0-9]+|[nc][0-9]+|[qv])|--(?:lines|bytes)=[0-9]+|--(?:quiet|verbose)"),
    "grep": re.compile(r"-[ivnch]+|--(?:invert-match|ignore-case|line-number|count|no-filename)"),
    "sort": re.compile(r"-[nrufb]+|--(?:numeric-sort|reverse|unique|ignore-case|ignore-leading-blanks)"),
    "wc": re.compile(r"-[lwcLm]+|--(?:lines|words|bytes|chars|max-line-length)"),
    "less": re.compile(r"-[FRX]+"),
    "cat": re.compile(r"-[nbsETv]+|--(?:number|number-nonblank|squeeze-blank|show-ends|show-tabs|show-nonprinting)"),
}
"""Only stdin filters and flags with no filesystem, output-file or code-loading operands."""


def _is_hz(line: str) -> bool:
    """Whether a command line is `hz ...`, at most piped into a reader (`hz clock --json | jq`): nothing else runs."""
    if SHELL_SYNTAX.search(line) or not line.isprintable():
        return False
    try:
        first, *rest = (shlex.split(part) for part in line.split("|"))
    except ValueError:
        return False
    if not first or first[0] != "hz" or not first[1:]:
        return False
    return all(
        part and part[0] in READER_FLAGS and all(READER_FLAGS[part[0]].fullmatch(arg) for arg in part[1:])
        for part in rest
    )


def recording(scene: Scene, directory: Path | None) -> Recording:
    """The files a rendered scene left in a page's assets directory, as relative paths; none while not recorded.

    Raises:
        HeinzelError: If a recording is unsafe, oversized or its transcript is not UTF-8 text.
    """
    base = f"{ASSETS}/{scene.name}"
    if directory is None:
        return Recording(scene=scene.name, animation=None, still=None, video=None)
    assets = directory / ASSETS

    def found(suffix: str, extension: str) -> str | None:
        return (
            f"{base}{suffix}.{extension}"
            if read_recording(assets / f"{scene.name}{suffix}.{extension}") is not None
            else None
        )

    try:
        transcript = (read_recording(assets / f"{scene.name}.md") or b"").decode("utf-8")
    except UnicodeError:
        msg = "Recording transcripts must be UTF-8 text; record the scene again"
        raise HeinzelError(msg) from None
    return Recording(
        scene=scene.name,
        animation=found("", "gif") or found("", "webp"),
        animation_dark=found("-dark", "gif") or found("-dark", "webp"),
        still=found("", "png"),
        still_dark=found("-dark", "png"),
        video=found("", "mp4"),
        transcript=transcript.strip(),
    )


def kinds_of(pages: Iterable[PluginPage]) -> dict[str, list[PluginPage]]:
    """Pages by kind, for a gallery grouped by what plugins are."""
    grouped: dict[str, list[PluginPage]] = defaultdict(list)
    for page in pages:
        for kind in page.kinds or ["capability"]:
            grouped[kind].append(page)
    return grouped
