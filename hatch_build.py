"""Hatch build hook: ship the web head's built app (web/) and Heinzel Notifier inside the package.

The app is built into src/heinzel/heads/web/static (not in git), stamped with the digest of its sources
(scripts/web_build.py, ADR 0039). A build whose stamp matches reuses it, so a wheel built from the sdist, which
carries the app, needs no Node.js. A missing or stale app is built first, with Node.js from the
`nodejs-wheel-binaries` wheel this hook adds to the build requirements and the pinned pnpm of scripts/tools.toml; if
that fails, so does the build: a wheel is complete or absent. Editable installs skip it: a checkout serves what
`make web-build` built.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

ROOT = Path(__file__).resolve().parent


def _script(name: str) -> ModuleType:
    """A build script from scripts/, loaded by path.

    Raises:
        RuntimeError: If the script isn't there.
    """
    spec = importlib.util.spec_from_file_location(f"heinzel_{name}", ROOT / "scripts" / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - the file is in the repository and the sdist
        msg = f"scripts/{name}.py is missing; build from a checkout or an sdist of heinzel"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _web() -> ModuleType:
    """scripts/web_build.py, which builds the app; standard library only until it runs Node.js."""
    return _script("web_build")


class WebAppHook(BuildHookInterface[Any]):
    """Builds the web app when it is missing or stale, and includes it in the sdist and the wheel."""

    PLUGIN_NAME = "custom"

    def dependencies(self) -> list[str]:
        """Node.js, only when the app has to be built."""
        web = _web()
        return [] if web.current() else [web.NODE_REQUIREMENT]

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Build the app if needed, then include it.

        Raises:
            RuntimeError: If the app couldn't be built.
        """
        if version == "editable":
            return
        web = _web()
        if not web.current():
            try:
                web.build()
            except web.WebError as error:
                msg = f"The web app (web/) couldn't be built into the package: {error}"
                raise RuntimeError(msg) from error
        target = "heinzel/heads/web/static" if self.target_name == "wheel" else "src/heinzel/heads/web/static"
        build_data["force_include"][str(web.STATIC)] = target
        # Heinzel Notifier (ADR 0043), built by `make notifier` on macOS: shipped when it matches its sources. A wheel
        # without it still works; macOS then falls back to osascript. CI's release build checks that it is there.
        notifier = _script("notifier")
        if notifier.APP.is_dir() and notifier.current():
            packaged = notifier.PACKAGED if self.target_name == "wheel" else f"src/{notifier.PACKAGED}"
            build_data["force_include"][str(notifier.APP)] = packaged
