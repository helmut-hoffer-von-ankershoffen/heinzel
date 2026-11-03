"""scripts/web_build.py and hatch_build.py: the web head's app is built into the package (ADR 0039)."""

import importlib.util
import json
import os
import subprocess
import sys
import tomllib
import types
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]


def load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load(REPO / "scripts" / "web_build.py")
PACKAGE = json.loads((REPO / "web" / "package.json").read_text(encoding="utf-8"))
LOCKED = {
    package["name"]: package.get("version")
    for package in tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))["package"]
}


def test_the_node_the_build_uses_is_the_one_uv_lock_has():
    name, version = build.NODE_REQUIREMENT.split("==")
    assert LOCKED[name] == version
    assert PACKAGE["engines"]["node"] == version
    assert PACKAGE["devDependencies"]["@types/node"].split(".")[0] == version.split(".")[0]
    assert f'"{build.NODE_REQUIREMENT}"' in (REPO / "scripts" / "web_build.py").read_text(encoding="utf-8")


def test_the_pnpm_package_json_names_is_the_one_tools_toml_pins():
    pinned = tomllib.loads((REPO / "scripts" / "tools.toml").read_text(encoding="utf-8"))["pnpm"]["version"]
    assert PACKAGE["packageManager"] == f"pnpm@{pinned}"


def app(root: Path) -> Path:
    web = root / "web"
    (web / "src").mkdir(parents=True)
    (web / "package.json").write_text("{}", encoding="utf-8")
    (web / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")
    (web / "src" / "main.tsx").write_text("render()", encoding="utf-8")
    return web


def test_the_source_digest_changes_with_any_source_and_nothing_else(tmp_path):
    web = app(tmp_path)
    before = build.source_digest(web)
    (web / "node_modules").mkdir()
    (web / "node_modules" / "x.js").write_text("ignored", encoding="utf-8")
    (web / "src" / ".DS_Store").write_text("ignored", encoding="utf-8")
    assert build.source_digest(web) == before
    (web / "src" / "main.tsx").write_text("render(2)", encoding="utf-8")
    assert build.source_digest(web) != before


def test_a_built_app_is_current_only_while_its_stamp_matches_the_sources(tmp_path):
    web, static = app(tmp_path), tmp_path / "static"
    assert not build.current(static, web)
    static.mkdir()
    (static / "index.html").write_text("<!doctype html>", encoding="utf-8")
    assert not build.current(static, web)  # no stamp
    (static / build.STAMP).write_text(build.source_digest(web) + "\n", encoding="utf-8")
    assert build.current(static, web)
    (web / "src" / "main.tsx").write_text("changed", encoding="utf-8")
    assert not build.current(static, web)


def test_an_sdist_without_sources_takes_the_app_it_carries(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html>", encoding="utf-8")
    assert build.current(static, tmp_path / "web")


def test_dependencies_count_as_installed_while_the_lockfile_holds_what_they_were_installed_from(tmp_path, monkeypatch):
    web = app(tmp_path)
    assert not build.installed(web)
    (web / "node_modules").mkdir()
    monkeypatch.setattr(build, "pnpm", lambda *arguments, **options: None)
    build.install(web)
    assert build.installed(web)
    os.utime(web / "pnpm-lock.yaml", (2_000_000_000, 2_000_000_000))  # touched, as a checkout does: still installed
    assert build.installed(web)
    (web / "pnpm-lock.yaml").write_text("lockfileVersion: 9\nchanged: true\n", encoding="utf-8")
    assert not build.installed(web)


def test_licenses_are_matched_against_the_allowlist_like_the_python_check():
    allowed = build.allowed_licenses()
    assert "MIT" in allowed
    assert not any(line.startswith("#") for line in allowed)
    listing = {
        "MIT": [{"name": "fine"}],
        "(MIT OR Apache-2.0)": [{"name": "dual"}],
        "GPL-3.0": [{"name": "copyleft"}],
    }
    assert build.refused_licenses(listing, allowed) == ["copyleft (GPL-3.0)"]


def test_pnpm_runs_through_tools_py_so_it_is_the_pinned_checksummed_binary():
    command = build.pnpm_command("install")
    assert command[1:] == [str(REPO / "scripts" / "tools.py"), "run", "pnpm", "install"]


def test_prepare_installs_only_when_locked_dependencies_are_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    installed = False
    calls = []

    def install():
        nonlocal installed
        calls.append("install")
        installed = True

    monkeypatch.setattr(build, "installed", lambda: installed)
    monkeypatch.setattr(build, "install", install)
    assert build.main(["prepare"]) == 0
    assert build.main(["prepare"]) == 0
    assert calls == ["install"]


def test_prepare_refuses_when_install_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "installed", lambda: False)

    def fail():
        msg = "fixture installation failed"
        raise build.WebError(msg)

    monkeypatch.setattr(build, "install", fail)
    assert build.main(["prepare"]) == 1


@pytest.mark.parametrize("prepare_exit", [0, 1])
@pytest.mark.parametrize("target", ["check", "check-fast", "preflight", "lint"])
def test_make_prepares_before_starting_parallel_checks(tmp_path, prepare_exit, target):
    web, steps, log = tmp_path / "web", tmp_path / "steps", tmp_path / "order"
    web.write_text('#!/bin/sh\nprintf "prepare\\n" >> "$ORDER_LOG"\nexit "$PREPARE_EXIT"\n')
    steps.write_text('#!/bin/sh\nprintf "checks\\n" >> "$ORDER_LOG"\n')
    web.chmod(0o700)
    steps.chmod(0o700)
    env = {
        "PATH": os.defpath,
        "ORDER_LOG": str(log),
        "PREPARE_EXIT": str(prepare_exit),
        "PREFLIGHT": "",
        "MAKEFLAGS": "",
    }
    result = subprocess.run(
        ["make", "--no-print-directory", target, f"WEB={web}", f"STEPS={steps}"],
        cwd=REPO,
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert (result.returncode == 0) is (prepare_exit == 0)
    assert log.read_text().splitlines() == (["prepare", "checks"] if prepare_exit == 0 else ["prepare"])


def test_an_unknown_command_prints_the_usage(capsys):
    assert build.main(["nonsense"]) == 2
    assert "Usage" in capsys.readouterr().err


class FakeHookInterface:
    """Hatchling's BuildHookInterface, as far as the hook uses it; hatchling is a build requirement, not installed."""

    target_name = "wheel"

    def __class_getitem__(cls, item: object) -> type:
        return cls


@pytest.fixture
def hook_module(monkeypatch):
    interface = ModuleType("hatchling.builders.hooks.plugin.interface")
    interface.BuildHookInterface = FakeHookInterface  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hatchling.builders.hooks.plugin.interface", interface)
    module = load(REPO / "hatch_build.py")
    monkeypatch.setattr(module, "_web", lambda: build)
    return module


def test_the_build_hook_needs_node_only_when_it_has_to_build(hook_module, monkeypatch):
    hook = hook_module.WebAppHook()
    monkeypatch.setattr(build, "current", lambda: True)
    assert hook.dependencies() == []
    monkeypatch.setattr(build, "current", lambda: False)
    assert hook.dependencies() == [build.NODE_REQUIREMENT]


def without_notifier(hook_module, monkeypatch, app: Path | None = None, *, current: bool = True) -> None:
    """The build hook sees Heinzel Notifier (ADR 0043) at `app`, built from the current sources or not; none if None."""
    original = hook_module._script

    def script(name: str):
        if name != "notifier":
            return original(name)
        return types.SimpleNamespace(
            APP=app or Path("/nonexistent/HeinzelNotifier.app"),
            PACKAGED="heinzel/modules/system/notify/macos/HeinzelNotifier.app",
            current=lambda: current,
            build=lambda: (_ for _ in ()).throw(SystemExit("swiftc not found")),  # a Mac without the tools
        )

    monkeypatch.setattr(hook_module, "_script", script)


def test_the_build_hook_includes_the_app_in_the_wheel_and_the_sdist(hook_module, monkeypatch):
    hook = hook_module.WebAppHook()
    without_notifier(hook_module, monkeypatch)
    monkeypatch.setattr(build, "current", lambda: True)
    data: dict[str, dict[str, str]] = {"force_include": {}}
    hook.initialize("standard", data)
    assert data["force_include"] == {str(build.STATIC): "heinzel/heads/web/static"}
    hook.target_name = "sdist"
    hook.initialize("standard", data)
    assert data["force_include"] == {str(build.STATIC): "src/heinzel/heads/web/static"}


def test_the_build_hook_ships_heinzel_notifier_when_it_matches_its_sources(hook_module, monkeypatch, tmp_path):
    hook = hook_module.WebAppHook()
    monkeypatch.setattr(build, "current", lambda: True)
    app = tmp_path / "HeinzelNotifier.app"
    app.mkdir()
    without_notifier(hook_module, monkeypatch, app)
    data: dict[str, dict[str, str]] = {"force_include": {}}
    hook.initialize("standard", data)
    assert data["force_include"][str(app)] == "heinzel/modules/system/notify/macos/HeinzelNotifier.app"
    hook.target_name = "sdist"
    hook.initialize("standard", data)
    assert data["force_include"][str(app)] == "src/heinzel/modules/system/notify/macos/HeinzelNotifier.app"
    stale: dict[str, dict[str, str]] = {"force_include": {}}
    without_notifier(hook_module, monkeypatch, app, current=False)
    hook.initialize("standard", stale)
    assert str(app) not in stale["force_include"]


def test_the_build_hook_tries_to_build_heinzel_notifier_on_a_mac(hook_module, monkeypatch, tmp_path, capsys):
    app = tmp_path / "HeinzelNotifier.app"
    app.mkdir()
    built = []
    state = {"current": False}

    def build_it() -> None:
        built.append(True)
        state["current"] = True

    def script(name: str):
        if name != "notifier":
            return original(name)
        return types.SimpleNamespace(
            APP=app, PACKAGED="p/HeinzelNotifier.app", current=lambda: state["current"], build=build_it
        )

    original = hook_module._script
    monkeypatch.setattr(hook_module, "_script", script)
    monkeypatch.setattr(build, "current", lambda: True)
    monkeypatch.setattr(hook_module.sys, "platform", "darwin")
    data: dict[str, dict[str, str]] = {"force_include": {}}
    hook_module.WebAppHook().initialize("standard", data)
    assert built == [True]
    assert data["force_include"][str(app)] == "p/HeinzelNotifier.app"


def test_the_build_hook_builds_a_stale_app_and_skips_editable_installs(hook_module, monkeypatch):
    hook = hook_module.WebAppHook()
    built = []
    without_notifier(hook_module, monkeypatch)
    monkeypatch.setattr(build, "current", lambda: False)
    monkeypatch.setattr(build, "ensure_built", lambda: built.append(True))
    editable: dict[str, dict[str, str]] = {"force_include": {}}
    hook.initialize("editable", editable)
    assert editable["force_include"] == {}
    assert built == []
    hook.initialize("standard", {"force_include": {}})
    assert built == [True]


def test_the_build_hook_refuses_a_wheel_without_the_app(hook_module, monkeypatch):
    def broken() -> Path:
        message = "no network"
        raise build.WebError(message)

    monkeypatch.setattr(build, "current", lambda: False)
    monkeypatch.setattr(build, "ensure_built", broken)
    with pytest.raises(RuntimeError, match="no network"):
        hook_module.WebAppHook().initialize("standard", {"force_include": {}})


def test_the_template_brings_no_web_app():
    template = REPO / "template"
    for name in ("Makefile", ".gitignore", "pyproject.toml.jinja", ".github/workflows/ci.yml.jinja"):
        text = (template / name).read_text(encoding="utf-8")
        assert "web_build.py" not in text
        assert "scripts/web.py" not in text
        assert "heads/web/static" not in text
    assert not (template / "web").exists()


@pytest.mark.parametrize("ignore", [".gitignore", "template/.gitignore"])
def test_no_ignore_pattern_matches_the_uv_cache_a_wheel_is_built_in(ignore):
    """uv unpacks the sdist under ~/.cache; a bare `.cache/` made hatchling add the bundled app to the wheel twice."""
    assert ".cache/" not in (REPO / ignore).read_text(encoding="utf-8").splitlines()
