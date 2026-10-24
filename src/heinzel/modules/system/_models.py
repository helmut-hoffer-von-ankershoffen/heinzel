"""Models of the system module."""

from typing import Any

from pydantic import BaseModel, Field


class ModuleInfo(BaseModel):
    """A registered module."""

    qualname: str = Field(description="Qualified name, e.g. `system.changelog`.")
    summary: str = Field(description="What the module is for, in one sentence.")
    distribution: str = Field(description="Distribution that ships the module; `hz` for built-ins.")
    version: str = Field(description="Version of that distribution.")
    builtin: bool = Field(description="Whether the module ships with Heinzelmännchen.")
    lets: dict[str, list[str]] = Field(description="Names of the advertised lets, by kind.")


class SystemInfo(BaseModel):
    """The state of this Heinzelmännchen installation."""

    version: str = Field(description="Installed Heinzelmännchen version.")
    python_version: str = Field(description="Version of the Python running hz.")
    python_executable: str = Field(description="Path of the Python interpreter running hz.")
    platform: str = Field(description="Operating system and architecture.")
    settings: dict[str, Any] = Field(description="Kernel settings in effect, by name.")
    modules: list[ModuleInfo] = Field(description="Every registered module.")
    plugin_errors: dict[str, str] = Field(description="Plugins that failed to load, by entry point name.")
    sections: dict[str, Any] = Field(description="Sections contributed by modules, by qualified module name.")
