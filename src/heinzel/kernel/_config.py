"""Layered configuration: TOML files from several scopes, overridden by environment variables.

Precedence, lowest first:

1. defaults declared on the settings models;
2. `site`: the machine-wide file, e.g. `/Library/Application Support/hz/config.toml` or `/etc/xdg/hz/config.toml`;
3. `user`: the user's file: `$HZ_CONFIG_HOME/config.toml`, else `$XDG_CONFIG_HOME/hz/config.toml`, else the
   platform's default (`~/Library/Application Support/hz/config.toml` on macOS, `~/.config/hz/config.toml` on Linux);
4. `project`: the nearest `hz.toml` in the working directory or above, shared by a team in its repository;
5. `explicit`: the file named by `HZ_CONFIG`;
6. environment variables: `HZ_<FIELD>` for the kernel, `HZ_<MODULE>__<FIELD>` for modules.

Kernel settings live in the `[hz]` table, module settings in a table named after the module's qualified name
(`[example]`, `[system.changelog]`).
"""

import json
import os
import tomllib
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Literal, cast

import platformdirs
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from heinzel.kernel._errors import HeinzelError

APP_NAME = "hz"
FILENAME = "config.toml"
PROJECT_FILENAME = "hz.toml"
KERNEL_SECTION = "hz"

Scope = Literal["site", "user", "project", "explicit"]
WritableScope = Literal["user", "project"]


@dataclass(frozen=True, slots=True)
class ConfigFile:
    """A configuration file Heinzelmännchen looks at, whether or not it exists."""

    scope: Scope
    path: Path

    @property
    def exists(self) -> bool:
        """Whether the file is there."""
        return self.path.is_file()


def env_prefix(section: str) -> str:
    """Environment variable prefix of a section: `HZ_` for the kernel, `HZ_<MODULE>__` for modules."""
    if section == KERNEL_SECTION:
        return "HZ_"
    return f"HZ_{section.upper().replace('.', '_').replace('-', '_')}__"


class ModuleSettings(BaseSettings):
    """Base class of settings a module declares with `module.settings(...)`.

    Values come from the module's TOML table and its `HZ_<MODULE>__<FIELD>` environment variables. Instances are
    built by the kernel and injected by type, like services.
    """

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    ignored_in_project: ClassVar[frozenset[str]] = frozenset()
    """Fields a project `hz.toml` may not set, because a repository someone else controls must not decide what runs
    or where data goes: they come from the site, user and `HZ_CONFIG` files and the environment only, and doctor
    names the keys a project file sets anyway."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Explicit arguments, then environment, then the TOML table, then secrets files."""
        del dotenv_settings  # configuration lives in TOML files and the environment, not in .env files
        return (init_settings, env_settings, _SectionSource(settings_cls), file_secret_settings)


_section: ContextVar[Mapping[str, Any]] = ContextVar("heinzel_config_section", default=MappingProxyType({}))


class _SectionSource(PydanticBaseSettingsSource):
    """Hands the TOML table of the section being loaded to pydantic-settings."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:  # pragma: no cover
        """Unused; `__call__` returns the whole table at once."""
        del field
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        """The section's values; unknown keys are ignored by the model."""
        return dict(_section.get())


@dataclass(frozen=True, slots=True)
class Config:
    """The configuration files in precedence order and their merged content."""

    files: tuple[ConfigFile, ...]
    data: Mapping[str, Any]
    layers: tuple[tuple[ConfigFile, Mapping[str, Any]], ...] = ()
    """The content of each existing file, lowest precedence first; `data` is their merge."""

    @classmethod
    def load(cls, cwd: Path | None = None) -> "Config":
        """Find and merge the configuration files for the working directory.

        Raises:
            HeinzelError: If a configuration file is not valid TOML.
        """
        files = config_files(cwd or Path.cwd())
        data: dict[str, Any] = {}
        layers: list[tuple[ConfigFile, Mapping[str, Any]]] = []
        for file in files:
            if file.exists:
                try:
                    content = tomllib.loads(file.path.read_bytes().decode())  # bytes: no newline translation
                except tomllib.TOMLDecodeError as exc:
                    msg = f"{file.path} is not valid TOML: {exc}"
                    raise HeinzelError(msg) from exc
                layers.append((file, content))
                _merge(data, content)
        return cls(files=files, data=data, layers=tuple(layers))

    @classmethod
    def empty(cls) -> "Config":
        """No files, no values: only defaults and environment variables apply."""
        return cls(files=(), data={})

    def section(self, name: str) -> dict[str, Any]:
        """The table for a dotted section name, e.g. `system.changelog`; empty if absent."""
        table: dict[str, Any] = dict(self.data)
        for part in name.split("."):
            table = _table(table.get(part)) or {}
        return table

    def project_keys(self, section: str, fields: frozenset[str]) -> list[str]:
        """Keys of `section` among `fields` that the project file sets (`-` and `_` alike); never their values."""
        project = [content for file, content in self.layers if file.scope == "project"]
        table = Config(files=(), data=project[0]).section(section) if project else {}
        return sorted(key for key in table if _field(key) in fields)

    def settings[S: ModuleSettings](self, model: type[S], section: str) -> S:
        """Build `model` from this configuration's `section` and the section's environment variables.

        Fields in `model.ignored_in_project` ignore what a project file sets and keep the other files' values.
        """
        table = self.section(section)
        if model.ignored_in_project and self.project_keys(section, model.ignored_in_project):
            trusted: dict[str, Any] = {}
            for file, content in self.layers:
                if file.scope != "project":
                    _merge(trusted, content)
            others = Config(files=(), data=trusted).section(section)
            table = {k: v for k, v in table.items() if _field(k) not in model.ignored_in_project}
            table |= {k: v for k, v in others.items() if _field(k) in model.ignored_in_project}
        token = _section.set(table)
        try:
            return model(_env_prefix=env_prefix(section))  # pyright: ignore[reportCallIssue] - pydantic-settings init option
        finally:
            _section.reset(token)


def config_files(cwd: Path) -> tuple[ConfigFile, ...]:
    """The files Heinzelmännchen reads, lowest precedence first."""
    files = [
        ConfigFile("site", platformdirs.site_config_path(APP_NAME) / FILENAME),
        ConfigFile("user", user_config_dir() / FILENAME),
    ]
    project = next((d / PROJECT_FILENAME for d in (cwd, *cwd.parents) if (d / PROJECT_FILENAME).is_file()), None)
    if project:
        files.append(ConfigFile("project", project))
    if explicit := os.environ.get("HZ_CONFIG"):
        files.append(ConfigFile("explicit", Path(explicit).expanduser()))
    return tuple(files)


def user_config_dir() -> Path:
    """The user's configuration directory; XDG_CONFIG_HOME is honoured on macOS too, as CLI users expect."""
    if explicit := os.environ.get("HZ_CONFIG_HOME"):
        return Path(explicit).expanduser()
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg).expanduser() / APP_NAME
    return platformdirs.user_config_path(APP_NAME)


def _field(key: str) -> str:
    """The settings field a TOML key sets: `code-host` and `code_host` both set `code_host`."""
    return key.replace("-", "_")


def _table(value: object) -> dict[str, Any] | None:
    """A TOML table as a plain dict, or None if `value` isn't one."""
    return dict(cast("Mapping[str, Any]", value)) if isinstance(value, Mapping) else None


def _merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    """Merge `source` into `target`, table by table, so later files override single keys, not whole tables."""
    for key, value in source.items():
        existing, incoming = _table(target.get(key)), _table(value)
        if existing is not None and incoming is not None:
            _merge(existing, incoming)
            target[key] = existing
        else:
            target[key] = incoming if incoming is not None else value


class ConfigEditor:
    """Changes configuration files the way `hz system config set` does; provided by `system.config` (ADR 0032).

    Setup steps ask for it by type. Values are validated against the setting's model before anything is written.
    The kernel declares the interface; `system.config` implements it, so plugins depend on the kernel only.
    """

    def write(self, key: str, value: object, scope: WritableScope = "user") -> Path:
        """Validate `value` for the setting `key` (`section.key`, e.g. `clock.zones`) and write it to the file.

        Raises:
            HeinzelError: If the key is unknown or the value is invalid for it.
        """
        raise NotImplementedError

    def unset(self, key: str, scope: WritableScope = "user") -> Path:
        """Remove the setting `key` from the file, so its default applies again.

        Raises:
            HeinzelError: If the key is unknown or not set in that file.
        """
        raise NotImplementedError


def toml_value(value: object) -> str:
    """A TOML literal for a JSON-compatible value: `true`, `3`, `"text"`, `["a", "b"]`, `{ name = "x" }`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping):
        items = cast("Mapping[str, object]", value).items()
        pairs = ", ".join(f"{_toml_key(str(key))} = {toml_value(item)}" for key, item in items)
        return f"{{ {pairs} }}" if pairs else "{}"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(toml_value(item) for item in cast("list[object]", value)) + "]"
    return json.dumps(value, ensure_ascii=False)


def _toml_key(key: str) -> str:
    bare = key.isascii() and key and all(c.isalnum() or c in "-_" for c in key)
    return key if bare else json.dumps(key, ensure_ascii=False)
