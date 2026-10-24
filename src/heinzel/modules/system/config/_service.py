"""Service of the config submodule."""

import os
import shlex
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal, cast

import tomlkit
from pydantic import ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python
from tomlkit.exceptions import ParseError

from heinzel import Config, ConfigEditor, HeinzelError, Kernel, ModuleSettings
from heinzel.kernel import FILENAME, PROJECT_FILENAME, ConfigFile, env_prefix, toml_value, user_config_dir
from heinzel.modules.system.config._models import (
    ConfigFileInfo,
    ConfigReport,
    ConfigRevealed,
    RevealScope,
    SettingInfo,
    SettingRevealed,
)
from heinzel.modules.system.config._reveal import Revealer
from heinzel.modules.system.config._schema import reference_hints, setting_schemas

HEADER = """\
# Heinzelmännchen configuration.
# Precedence, lowest first: defaults < site < user (this file) < project hz.toml < $HZ_CONFIG < environment.
# Uncomment a line to override its default. `hz system config show` prints what is in effect.
"""

Scope = Literal["user", "project"]
PLACEHOLDER_LIST: list[str] = ["first", "second"]  # the example for a list setting that is empty


def _default(field: FieldInfo) -> Any:  # noqa: ANN401 - a setting's default is any JSON value
    """A field's default as JSON (a factory's, computed now), or None when it has none."""
    if field.is_required() or field.default_factory is not None:
        return (
            None if field.default_factory is None else to_jsonable_python(field.get_default(call_default_factory=True))
        )
    return to_jsonable_python(field.default)


class ConfigService(ConfigEditor):
    """Explains and initializes configuration."""

    def __init__(self, kernel: Kernel) -> None:
        """Create the service; the kernel is injected."""
        self._kernel = kernel
        self.revealer = Revealer(sys.platform, shutil.which, os.environ)
        """This machine's file manager; tests replace it so nothing opens."""

    def report(self) -> ConfigReport:
        """The files Heinzelmännchen reads, every setting in effect, and whether a file can be revealed here."""
        # Read now, not as booted: `hz ui` keeps running while it and terminals change the files.
        config = self._kernel.config.reread()
        sections = [(section, _fresh(config, section, settings)) for section, settings in self._kernel.settings_of()]
        schemas = {type(settings): setting_schemas(type(settings)) for _, settings in sections}
        return ConfigReport(
            files=[self._file(file) for file in config.files],
            can_reveal=self.revealer.unsupported() is None,
            file_manager=self.revealer.file_manager,
            settings=[
                SettingInfo(
                    section=section,
                    key=key,
                    value=to_jsonable_python(getattr(settings, key)),
                    env=f"{env_prefix(section)}{key.upper()}",
                    description=field.description or "",
                    section_title=type(settings).section_title,
                    default=_default(field),
                    source=self._source(config, section, key, type(settings).ignored_in_project),
                    json_schema=schemas[type(settings)].get(key),
                )
                for section, settings in sections
                for key, field in type(settings).model_fields.items()
            ],
        )

    @staticmethod
    def _source(config: Config, section: str, key: str, ignored_in_project: frozenset[str]) -> str:
        """Which layer the value in effect comes from: the environment, the last file that sets it, or the default."""
        # pydantic-settings matches variable names regardless of case.
        if f"{env_prefix(section)}{key}".upper() in {name.upper() for name in os.environ}:
            return "env"
        for file, content in reversed(config.layers):
            if file.scope == "project" and key in ignored_in_project:
                continue  # ignored there (ADR 0028), so another layer decides
            table = Config(files=(), data=content).section(section)
            if key in table or key.replace("_", "-") in table:
                return str(file.scope)
        return "default"

    def reveal(self, scope: RevealScope) -> ConfigRevealed:
        """Show the site, user or project configuration file in this machine's file manager; nothing is created."""
        path = next((f.path for f in self._kernel.config.files if f.scope == scope), None)
        if path is None and scope == "user":
            path = user_config_dir() / FILENAME
        if path is None:
            supported = self.revealer.unsupported() is None
            message = (
                f"There is no project {PROJECT_FILENAME} in {Path.cwd()} or above; "
                "`hz system config set KEY VALUE --scope project` creates one here."
                if scope == "project"
                else f"Heinzelmännchen reads no {scope} configuration file here."
            )
            return ConfigRevealed(scope=scope, path=None, supported=supported, opened=False, message=message)
        done = self.revealer.reveal(path)
        return ConfigRevealed(
            scope=scope, path=path, supported=done.supported, opened=done.opened, message=done.message
        )

    def reveal_setting(self, key: str) -> SettingRevealed:
        """Show the file or folder a path setting names, such as `hz.log_dir`, in this machine's file manager.

        Raises:
            HeinzelError: If the setting isn't a path, or is not set.
        """
        setting = self.get(key)
        schema = setting.json_schema or {}
        # A path, or `Path | None`: the format is on the schema or on its one branch that isn't null.
        branches = cast("list[dict[str, Any]]", [schema, *schema.get("anyOf", [])])
        if not any(branch.get("format") == "path" for branch in branches):
            several = cast("dict[str, Any]", schema.get("items") or {}).get("format") == "path"
            msg = (
                f"{key} holds several paths; open them one by one in your file manager."
                if several
                else f"{key} isn't a file or folder setting; only those can be shown in a file manager."
            )
            raise HeinzelError(msg)
        if not isinstance(setting.value, str) or not setting.value:
            msg = f"{key} is not set, so there is nothing to show."
            raise HeinzelError(msg)
        path = Path(setting.value).expanduser()
        if not path.is_absolute():
            # Relative to whichever directory hz runs in, which is not what the file that set it meant.
            msg = f"{key} is the relative path {path}; set it to a full path to show it."
            raise HeinzelError(msg)
        done = self.revealer.reveal(path)
        return SettingRevealed(key=key, path=path, supported=done.supported, opened=done.opened, message=done.message)

    def init(self, path: Path | None = None, *, force: bool = False) -> Path:
        """Write a configuration file listing every setting, commented out, with its description and default.

        Raises:
            HeinzelError: If the file exists and `force` is not set.
        """
        target = path or user_config_dir() / FILENAME
        if target.exists() and not force:
            msg = f"{target} already exists; pass --force to overwrite it."
            raise HeinzelError(msg)
        lines = [HEADER]
        current = None
        for setting in self.report().settings:
            if setting.section != current:
                current = setting.section
                lines.append(f"\n[{current}]")
            lines.append(f"# {setting.description}{reference_hints(setting.json_schema)}")
            # TOML has no null: a setting without a default gets a hint instead of an assignment that wouldn't parse.
            if setting.value is None:
                lines.append(f"# {setting.key}: not set by default")
            else:
                lines.append(f"# {setting.key} = {toml_value(setting.value)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def get(self, key: str) -> SettingInfo:
        """The setting `section.key` in effect, e.g. `hz.log_level` or `example.greeting`."""
        section, name, _ = self._locate(key)
        return next(s for s in self.report().settings if s.section == section and s.key == name)

    def set(self, key: str, value: str, scope: Scope = "user") -> Path:
        """Validate `value` for the setting `key` and write it to the user or project configuration file.

        `value` is read as a TOML value when it parses as one (`true`, `3`, `["a", "b"]`), otherwise as a string.
        Comments and formatting of the file are kept.

        Raises:
            HeinzelError: If the key is unknown or the value is invalid for it.
        """
        return self.write(key, _parse_value(value), scope)

    def write(self, key: str, value: object, scope: Scope = "user") -> Path:
        """Validate a value (JSON-compatible, or a pydantic model) for the setting `key` and write it to the file.

        What setup steps call through `ConfigEditor` (ADR 0032); `set` parses text and calls this.

        Raises:
            HeinzelError: If the key is unknown or the value is invalid for it.
        """
        section, name, model = self._locate(key)
        parsed = _without_none(to_jsonable_python(value))
        if parsed is None:
            msg = f"TOML has no null; to let {key} take its default, run `hz system config unset {key}`."
            raise HeinzelError(msg)
        table = {**self._kernel.config.reread().section(section), name: parsed}
        try:
            Config(files=(), data=_nest(section, table)).settings(model, section)
        except ValidationError as exc:
            msg = f"Invalid value for {key}: {exc.errors()[0]['msg']}\n{self._example(key, section, name, model)}"
            raise HeinzelError(msg) from exc
        if problem := model.refuse_written(name, parsed):
            msg = f"Invalid value for {key}: {problem}"
            raise HeinzelError(msg)
        path = self._scope_file(scope)
        document = _load(path)
        _table(document, section)[name] = parsed
        _save(path, document)
        return path

    def unset(self, key: str, scope: Scope = "user") -> Path:
        """Remove the setting `key` from the user or project configuration file; its default applies again.

        Raises:
            HeinzelError: If the key is unknown or not set in that file.
        """
        section, name, _ = self._locate(key)
        path = self._scope_file(scope)
        document = _load(path)
        table = _table(document, section)
        if name not in table:
            msg = f"{key} is not set in {path}"
            raise HeinzelError(msg)
        del table[name]
        _save(path, document)
        return path

    def _locate(self, key: str) -> tuple[str, str, type[ModuleSettings]]:
        section, _, name = key.rpartition(".")
        for candidate, settings in self._kernel.settings_of():
            if candidate == section and name in type(settings).model_fields:
                return section, name, type(settings)
        known = ", ".join(f"{s.section}.{s.key}" for s in self.report().settings)
        msg = f"Unknown setting {key!r}. Known settings: {known}"
        raise HeinzelError(msg)

    def _example(self, key: str, section: str, name: str, model: type[ModuleSettings]) -> str:
        """How to write a value for `key`: what kind of value it takes and a command line that sets one."""
        schema = model.model_json_schema()["properties"].get(name, {})
        current = next((getattr(s, name) for c, s in self._kernel.settings_of() if c == section), None)
        value: Any = to_jsonable_python(current)
        if choices := schema.get("enum"):
            kind = ""  # the validation message already lists the choices
            value = choices[0]
        elif schema.get("type") == "array":
            kind = "A list, written as a TOML array in single quotes."
            value = value or PLACEHOLDER_LIST
        elif schema.get("type") == "object":
            kind = "A table, written as a TOML inline table in single quotes."
        elif isinstance(value, bool):
            kind = "true or false."
        elif isinstance(value, int | float):
            kind = "A number."
        else:
            kind = "Text."
            value = value or "text"
        text = value if isinstance(value, str) else toml_value(value)
        return f"{kind} For example:".lstrip() + f"\n  hz system config set {key} {shlex.quote(text)}"

    def _scope_file(self, scope: Scope) -> Path:
        if scope == "user":
            return user_config_dir() / FILENAME
        project = next((f.path for f in self._kernel.config.files if f.scope == "project"), None)
        return project or Path.cwd() / PROJECT_FILENAME

    @staticmethod
    def _file(file: ConfigFile) -> ConfigFileInfo:
        return ConfigFileInfo(scope=file.scope, path=file.path, exists=file.exists)


def _fresh(config: Config, section: str, booted: ModuleSettings) -> ModuleSettings:
    """`section`'s settings as the files say now; as booted when a file now holds a value they don't accept."""
    try:
        return config.settings(type(booted), section)
    except ValidationError:
        return booted


def _parse_value(value: str) -> Any:  # noqa: ANN401 - any TOML value
    # tomllib, not tomlkit: tomlkit hands back a plain bool for `true`, which has no `unwrap`.
    try:
        return tomllib.loads(f"value = {value}")["value"]
    except tomllib.TOMLDecodeError:
        return value


def _without_none(value: Any) -> Any:  # noqa: ANN401 - any JSON-compatible value
    """`value` without None in tables and arrays: TOML has no null, an absent key means its default."""
    if isinstance(value, dict):
        return {k: _without_none(v) for k, v in cast("dict[str, Any]", value).items() if v is not None}
    if isinstance(value, list):
        return [_without_none(v) for v in cast("list[object]", value) if v is not None]
    return value


def _save(path: Path, document: tomlkit.TOMLDocument) -> None:
    """Write the file whole or not at all: Ctrl-C or a full disk never leaves half a configuration behind.

    A symlinked file (dotfile managers) is written where the link points, keeping the link and the file's mode.
    """
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
        if target.exists():
            shutil.copymode(target, temporary)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _nest(section: str, table: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = table
    for part in reversed(section.split(".")):
        data = {part: data}
    return data


def _load(path: Path) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
    except ParseError as exc:
        msg = f"{path} is not valid TOML: {exc}"
        raise HeinzelError(msg) from exc


def _table(document: tomlkit.TOMLDocument, section: str) -> Any:  # noqa: ANN401 - a tomlkit table
    table: Any = document
    for part in section.split("."):
        if part not in table:
            table[part] = tomlkit.table()
        table = table[part]
    return table
