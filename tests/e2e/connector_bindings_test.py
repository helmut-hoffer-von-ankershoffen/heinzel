"""Mounted Settings → Connectors: every connector capability can be bound in the app and switched between plugins,
and plugin pages wait for the plugin list, with a company's layers installed too."""

import functools
import json
import os
import sys
import tomllib
from pathlib import Path

import pytest

from heinzel import Config, Kernel, Module, ModuleRecord, ModuleSettings, Settings
from heinzel.heads import web
from heinzel.kernel import CAPABILITIES, Origin, discover
from tests.e2e._browser_result import check_browser, require_browser, wait_browser
from tests.e2e._mounted import MountedServer
from tests.e2e.docs_test import GuardedCommand
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
CONNECTOR_CAPABILITIES = ("calendar", "chat", "code-host", "directory", "mail", "meetings", "tracker")


def adapter(capability: str) -> type:
    """A class with every method of `capability`; never built, since binding in Settings only saves configuration."""
    return type(f"Fake{capability.title()}", (), dict.fromkeys(CAPABILITIES[capability].methods, lambda *_: None))


def plugins() -> dict[str, Module]:
    """`bridge` brings an adapter for every connector capability (two for documents); `other` four of them."""
    bridge = Module("bridge", "Test-only connectors for every capability.")
    for capability in CONNECTOR_CAPABILITIES:
        bridge.provides(capability, modes={"read"}, hosts=["bridge.invalid"])(adapter(capability))
    for kind in ("docs", "wiki"):
        bridge.provides("documents", modes={"read"}, hosts=["bridge.invalid"], name=f"bridge-{kind}")(
            adapter("documents")
        )
    other = Module("other", "Test-only connectors for mail, calendar, documents and tracker.")
    for capability in ("mail", "calendar", "documents", "tracker"):
        other.provides(capability, modes={"read"}, hosts=["other.invalid"])(adapter(capability))
    return {"bridge": bridge, "other": other}


def mounted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kernel: Kernel, script: str, engine: str) -> str:
    """Run the browser driver `script` against `kernel` served with restarts refused; its standard output."""
    # Restarts refused, as under `make web-dev`: "Restart and apply" saves and says to restart, and the test process
    # is never replaced.
    monkeypatch.setattr(web, "serve", functools.partial(web.serve, restartable=False))
    with MountedServer(kernel, monkeypatch) as server:
        command = [
            sys.executable,
            str(ROOT / "scripts/web_build.py"),
            "exec",
            "node",
            f"e2e/{script}",
            server.url,
            engine,
            os.environ.get("HZ_E2E_SHOTS", ""),
        ]
        guarded = GuardedCommand(command, cwd=ROOT, env=os.environ.copy())
        try:
            returncode = require_browser(wait_browser(guarded, 420))
        finally:
            guarded.stop()
        stdout, stderr = guarded.capture.text("stdout"), guarded.capture.text("stderr")
        check_browser(returncode, (stderr + "\n" + stdout).replace(server.token, "<redacted-session>"))
    return stdout


def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """This test's own home, XDG folders and browsers, and the kernel settings that use them."""
    web_build.ensure_built()
    for name, folder in {
        "HOME": "home",
        "XDG_CONFIG_HOME": "config",
        "XDG_DATA_HOME": "xdg-data",
        "XDG_STATE_HOME": "xdg-state",
        "XDG_CACHE_HOME": "xdg-cache",
    }.items():
        monkeypatch.setenv(name, str(tmp_path / folder))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    return Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_every_connector_capability_is_bound_and_switched_in_settings(make_kernel, tmp_path, monkeypatch, engine):
    settings = isolated(tmp_path, monkeypatch)
    kernel = make_kernel(builtins=True, settings_=settings, config=Config.load(tmp_path / "home"), **plugins())
    stdout = mounted(tmp_path, monkeypatch, kernel, "connector-bindings.mjs", engine)

    final = json.loads(stdout.strip().splitlines()[-1])
    saved = tomllib.loads((Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").read_text(encoding="utf-8"))
    assert {key.replace("_", "-"): value for key, value in saved["capabilities"].items()} == final
    assert final == {
        "mail": "bridge",
        "calendar": "other",
        "documents": ["bridge-docs"],
        "tracker": "other",
        "chat": "bridge",
        "meetings": "bridge",
        "code-host": "bridge",
        "directory": "bridge",
    }


class Note(ModuleSettings):
    """A plugin's own setting, so that the plugin has a settings page."""

    note: str = "fictional"


def plugin_stack(settings: Settings, home: Path) -> Kernel:
    """Two connector plugins and a company's two layers, each module from its own distribution and with settings."""
    modules = plugins()
    modules["acme"] = Module("acme", "Test-only company layer.")
    modules["acme-lisbon"] = Module("acme-lisbon", "Test-only site layer.")
    records = discover(plugins=False)[0]
    for name, module in modules.items():
        module.settings(type(f"{name.title().replace('-', '')}Settings", (Note,), {"__doc__": f"{name} settings."}))
        origin = Origin(distribution=f"heinzel-{name}", version="1.0.0", builtin=False)
        records.append(ModuleRecord(qualname=name, module=module, origin=origin))
    return Kernel(settings, records, config=Config.load(home))


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_plugin_pages_wait_for_the_plugin_list_with_company_layers_installed(tmp_path, monkeypatch, engine):
    settings = isolated(tmp_path, monkeypatch)
    stdout = mounted(tmp_path, monkeypatch, plugin_stack(settings, tmp_path / "home"), "connector-pages.mjs", engine)
    assert json.loads(stdout.strip().splitlines()[-1]) == {
        "loading": "Looking for installed connectors…",
        "connectors": ["Bridge", "Other"],
        "organisation": ["ACME", "ACME Lisbon"],
    }
