"""Service of the config submodule."""

import os
import shlex
import shutil
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
from heinzel.modules.system.config._models import ConfigFileInfo, ConfigReport, SettingInfo

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

    def report(self) -> ConfigReport:
        """The files Heinzelmännchen reads and every setting in effect."""
        return ConfigReport(
            files=[self._file(file) for file in self._kernel.config.files],
            settings=[
                SettingInfo(
                    section=section,
                    key=key,
                    value=to_jsonable_python(getattr(settings, key)),
                    env=f"{env_prefix(section)}{key.upper()}",
                    description=field.description or "",
                    default=_default(field),
                    source=self._source(section, key, type(settings).ignored_in_project),
                )
                for section, settings in self._kernel.settings_of()
                for key, field in type(settings).model_fields.items()
            ],
        )

    def _source(self, section: str, key: str, ignored_in_project: frozenset[str]) -> str:
        """Which layer the value in effect comes from: the environment, the last file that sets it, or the default."""
        # pydantic-settings matches variable names regardless of case.
        if f"{env_prefix(section)}{key}".upper() in {name.upper() for name in os.environ}:
            return "env"
        for file, content in reversed(self._kernel.config.layers):
            if file.scope == "project" and key in ignored_in_project:
                continue  # ignored there (ADR 0028), so another layer decides
            table = Config(files=(), data=content).section(section)
            if key in table or key.replace("_", "-") in table:
                return str(file.scope)
        return "default"

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
            lines.append(f"# {setting.description}")
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
        table = {**self._kernel.config.section(section), name: parsed}
        try:
            Config(files=(), data=_nest(section, table)).settings(model, section)
        except ValidationError as exc:
            msg = f"Invalid value for {key}: {exc.errors()[0]['msg']}\n{self._example(key, section, name, model)}"
            raise HeinzelError(msg) from exc
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
