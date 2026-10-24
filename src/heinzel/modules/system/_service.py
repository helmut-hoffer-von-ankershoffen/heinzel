"""Service of the system module."""

import platform
import sys
from collections import defaultdict
from typing import Any

from pydantic import BaseModel

from heinzel import InfoLet, Kernel, __version__
from heinzel.kernel import update_hint
from heinzel.modules.system._models import ModuleInfo, SystemInfo


class SystemService:
    """Reports on the Heinzelmännchen installation."""

    def __init__(self, kernel: Kernel) -> None:
        """Create the service; the kernel is injected."""
        self._kernel = kernel

    def modules(self) -> list[ModuleInfo]:
        """Every registered module with the lets it advertises."""
        infos: list[ModuleInfo] = []
        for record in self._kernel.modules:
            lets: dict[str, list[str]] = defaultdict(list)
            for let in record.module.lets:
                lets[type(let).__name__].append(let.name)
            infos.append(
                ModuleInfo(
                    qualname=record.qualname,
                    summary=record.module.summary,
                    distribution=record.origin.distribution,
                    version=record.origin.version,
                    builtin=record.origin.builtin,
                    lets=dict(lets),
                )
            )
        return infos

    def info(self) -> SystemInfo:
        """Versions, interpreter, settings, modules and the sections modules contribute."""
        return SystemInfo(
            version=__version__,
            python_version=platform.python_version(),
            python_executable=sys.executable,
            platform=platform.platform(),
            settings=self._kernel.settings.model_dump(mode="json"),
            modules=self.modules(),
            plugin_errors=dict(self._kernel.errors),
            sections={record.qualname: self._section(let) for record, let in self._kernel.lets(InfoLet)},
            update_hint=update_hint(self._kernel.settings),
        )

    def _section(self, let: InfoLet) -> Any:  # noqa: ANN401 - sections are free-form JSON
        try:
            section = self._kernel.call(let.provider)
        except Exception as exc:  # noqa: BLE001 - a broken module must not break `hz system info`
            return {"error": f"{type(exc).__name__}: {exc}"}
        return section.model_dump(mode="json") if isinstance(section, BaseModel) else section
