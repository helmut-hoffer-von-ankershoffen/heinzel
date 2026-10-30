"""The full lifecycle, as users run it: install Heinzelmännchen with uv, add a plugin, upgrade, use, remove, uninstall.

Two paths are covered: the commands run directly, and the scripts the `hz-init` / `hz-uninstall` agent skills run.
Needs network (PyPI). Every test gets its own uv tool directory, configuration and skill directory.
"""

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
HEAD = subprocess.run(
    ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=False, timeout=300
).stdout.strip()
DUMMY_PLUGIN = REPO / "tests" / "fixtures" / "heinzel-dummy"
SKILLS = REPO / "agent-skills" / "skills"
# --color never: with FORCE_COLOR (as in CI) uv would wrap the path in escape codes.
UV_PYTHON_DIR = subprocess.run(
    ["uv", "--color", "never", "python", "dir"], capture_output=True, text=True, check=False, timeout=60
).stdout.strip()
MODULES_DIR = REPO / "src" / "heinzel" / "modules"


def declared_name(package: Path) -> str | None:
    """The name a package's `module = Module("<name>", ...)` declares, or None if it declares none."""
    text = (package / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^module = Module\(\s*"([a-z][a-z0-9-]*)"', text, re.MULTILINE)
    return match[1] if match else None


def builtin_modules() -> set[str]:
    """The built-in modules in the checkout, by the names they declare (the package `telemetry` is `report`)."""
    found: set[str] = set()
    for init in MODULES_DIR.rglob("__init__.py"):
        if init.parent == MODULES_DIR or declared_name(init.parent) is None:
            continue
        chain = [init.parent, *init.parent.parents]
        names = [declared_name(p) for p in reversed(chain[: chain.index(MODULES_DIR)])]
        found.add(".".join(name for name in names if name))
    return found


BUILTIN_MODULES = builtin_modules()


@dataclass
class Sandbox:
    """An isolated place to install Heinzelmännchen: uv tool and bin directories, configuration, data and skills."""

    root: Path
    own_home: bool = False
    """Give the sandbox its own $HOME and let Heinzelmännchen use its default directories inside it."""

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def env(self) -> dict[str, str]:
        env = {
            **os.environ,
            "UV_TOOL_DIR": str(self.root / "tools"),
            "UV_TOOL_BIN_DIR": str(self.root / "bin"),
            "UV_CACHE_DIR": str(self.root / "uv-cache"),
            "UV_PYTHON_INSTALL_DIR": UV_PYTHON_DIR,
            "HZ_SYSTEM_AGENT__SKILL_DIRS": json.dumps([str(self.skills)]),
        }
        for name in ("VIRTUAL_ENV", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
            env.pop(name, None)
        if self.own_home:
            for name in ("HZ_CONFIG", "HZ_CONFIG_HOME", "HZ_DATA_DIR", "HZ_CACHE_DIR", "HZ_STATE_DIR", "HZ_LOG_DIR"):
                env.pop(name, None)
            # Reuse managed Pythons while keeping package cache in this sandbox.
            env["HOME"] = str(self.home)
        else:
            env |= {
                "HZ_CONFIG_HOME": str(self.root / "config"),
                "HZ_DATA_DIR": str(self.root / "data"),
                "HZ_CACHE_DIR": str(self.root / "cache"),
                "HZ_STATE_DIR": str(self.root / "state"),
                "HZ_LOG_DIR": str(self.root / "log"),
                "HZ_SYSTEM_DAEMON__UNIT_DIR": str(self.root / "units"),
            }
        return env

    @property
    def hz_bin(self) -> Path:
        return self.root / "bin" / "hz"

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    def run(self, *command: str | Path, **env: str) -> str:
        result = subprocess.run(
            [str(part) for part in command],
            capture_output=True,
            text=True,
            check=False,
            env={**self.env, **env},
            timeout=240,
        )
        assert result.returncode == 0, f"{command} failed:\n{result.stdout}\n{result.stderr}\n{self.diagnose()}"
        return result.stdout

    def diagnose(self) -> str:
        """What the sandbox's bin directory holds, and where the hz launcher's interpreter points."""
        lines = [f"bin: {sorted(p.name for p in (self.root / 'bin').glob('*'))}"]
        if self.hz_bin.is_symlink() or self.hz_bin.exists():
            lines.append(f"hz -> {self.hz_bin.resolve()} (exists: {self.hz_bin.resolve().exists()})")
            if self.hz_bin.resolve().exists():
                shebang = self.hz_bin.resolve().read_bytes().split(b"\n", 1)[0].decode(errors="replace")
                interpreter = Path(shebang.removeprefix("#!").split()[0]) if shebang.startswith("#!") else None
                lines.append(f"shebang: {shebang}")
                if interpreter:
                    lines.append(f"interpreter -> {interpreter.resolve()} (exists: {interpreter.resolve().exists()})")
        return "\n".join(lines)

    def hz(self, *args: str) -> str:
        return self.run(self.hz_bin, *args)


def test_sandboxes_keep_separate_uv_caches_and_normal_link_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "foreign-cache"))
    monkeypatch.delenv("UV_LINK_MODE", raising=False)
    first = Sandbox(tmp_path / "one").env
    second = Sandbox(tmp_path / "two", own_home=True).env
    assert first["UV_CACHE_DIR"] == str(tmp_path / "one/uv-cache")
    assert second["UV_CACHE_DIR"] == str(tmp_path / "two/uv-cache")
    assert first["UV_PYTHON_INSTALL_DIR"] == second["UV_PYTHON_INSTALL_DIR"] == UV_PYTHON_DIR
    assert "UV_LINK_MODE" not in first
    assert "UV_LINK_MODE" not in second


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    dist = tmp_path_factory.mktemp("dist")
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(dist), str(REPO)], check=True, timeout=300)
    return next(dist.glob("heinzel-*.whl"))


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


@pytest.mark.timeout(300)
@pytest.mark.long_running
def test_direct_lifecycle(sandbox, wheel):
    """A cold macOS run builds the wheel and exercises several isolated uv installs/upgrades."""
    sandbox.run("uv", "tool", "install", wheel)
    assert sandbox.hz("--version").startswith("hz ")
    info = json.loads(sandbox.hz("system", "info", "--json"))
    assert {m["qualname"] for m in info["modules"]} == BUILTIN_MODULES
    assert json.loads(sandbox.hz("system", "changelog", "--json")).startswith("# Changelog")
    assert Path(json.loads(sandbox.hz("system", "config", "init", "--json"))).is_file()

    installed = json.loads(sandbox.hz("system", "agent", "install", "--json"))
    assert {skill["name"] for skill in installed} == {"hz", "hz-init", "hz-uninstall"}
    assert (sandbox.skills / "hz-init" / "scripts" / "install.sh").is_file()

    sandbox.hz("system", "plugin", "develop", DUMMY_PLUGIN)
    assert sandbox.hz("dummy", "hello").strip() == "hello from dummy"
    # ADR 0006: plugins are recorded in the uv tool receipt, so upgrading Heinzelmännchen keeps them.
    sandbox.run("uv", "tool", "upgrade", "heinzel")
    assert sandbox.hz("dummy", "greet", "--name", "Ada").strip() == "Hello, Ada!"
    [dummy] = json.loads(sandbox.hz("system", "plugin", "list", "--json"))
    assert (dummy["distribution"], dummy["version"], dummy["modules"]) == (
        "heinzel-dummy",
        "1.2.3",
        ["dummy", "dummy.greet"],
    )
    assert (dummy["source_kind"], dummy["enabled"]) == ("path", True)
    assert [e["action"] for e in json.loads(sandbox.hz("system", "plugin", "history", "--json"))] == ["develop"]
    sections = json.loads(sandbox.hz("system", "info", "--json"))["sections"]
    assert sections["dummy"] == {"greeting": "hello from dummy"}  # the core's own uses are tested in integration

    # `hz system update --source` switches the source and keeps plugins too.
    assert sandbox.hz("system", "update", "--source", str(wheel)).startswith(("Reinstalled hz ", "Updated hz "))
    assert sandbox.hz("dummy", "hello").strip() == "hello from dummy"

    sandbox.hz("system", "plugin", "remove", "heinzel-dummy")
    assert json.loads(sandbox.hz("system", "plugin", "list", "--json")) == []
    assert json.loads(sandbox.hz("system", "agent", "uninstall", "--json"))
    assert not (sandbox.skills / "hz-init").exists()

    sandbox.run("uv", "tool", "uninstall", "heinzel")
    assert not sandbox.hz_bin.exists()


def test_skill_lifecycle(tmp_path, wheel):
    """What `/hz-init` and `/hz-uninstall --purge` do, through the scripts that ship with the skills."""
    sandbox = Sandbox(tmp_path, own_home=True)
    sandbox.home.mkdir()
    output = sandbox.run(
        "bash", SKILLS / "hz-init" / "scripts" / "install.sh", "--plugin", DUMMY_PLUGIN, HZ_SOURCE=str(wheel)
    )
    assert "==> Done" in output
    assert sandbox.hz("dummy", "hello").strip() == "hello from dummy"
    assert [p.name for p in sandbox.home.rglob("config.toml")] == ["config.toml"]
    assert {p.name for p in sandbox.skills.iterdir()} == {"hz", "hz-init", "hz-uninstall"}

    # Running hz-init again updates in place and keeps plugins and configuration.
    sandbox.run("bash", SKILLS / "hz-init" / "scripts" / "install.sh", HZ_SOURCE=str(wheel))
    assert sandbox.hz("dummy", "hello").strip() == "hello from dummy"
    data_dir = Path(json.loads(sandbox.hz("system", "config", "get", "hz.data_dir", "--json"))["value"])
    data_dir.mkdir(parents=True, exist_ok=True)
    assert data_dir.is_relative_to(sandbox.home)

    sandbox.run("bash", SKILLS / "hz-uninstall" / "scripts" / "uninstall.sh", "--purge")
    assert not sandbox.hz_bin.exists()
    assert not sandbox.skills.exists() or not any(sandbox.skills.iterdir())
    assert not list(sandbox.home.rglob("config.toml"))
    assert not data_dir.exists()

    # Uninstalling twice is harmless.
    assert "not installed" in sandbox.run("bash", SKILLS / "hz-uninstall" / "scripts" / "uninstall.sh")


def test_install_from_git_like_users_do(sandbox):
    """`uv tool install git+https://...` builds from a git checkout; this builds from the local repository's HEAD."""
    if not HEAD:
        pytest.skip("needs a git checkout with at least one commit")
    sandbox.run("uv", "tool", "install", f"git+file://{REPO}@{HEAD}")
    assert sandbox.hz("--version").startswith("hz ")
    assert json.loads(sandbox.hz("system", "changelog", "--json")).startswith("# Changelog")
    assert {s["name"] for s in json.loads(sandbox.hz("system", "agent", "skills", "--json"))} == {
        "hz",
        "hz-init",
        "hz-uninstall",
    }


def installed_from_git(sandbox) -> None:
    """Core installed from this checkout's HEAD; plugin artifacts and revisions are selected independently."""
    if not HEAD:
        pytest.skip("needs a git checkout with at least one commit")
    sandbox.run("uv", "tool", "install", f"git+file://{REPO}@{HEAD}")


def dummy_git_source(sandbox, tmp_path):
    """A synthetic HTTPS source backed by a local bare repository; no real plugin or network."""
    work = tmp_path / "work"
    subprocess.run(["cp", "-R", str(DUMMY_PLUGIN), str(work)], check=True, timeout=60)
    git = ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True, timeout=60)
    subprocess.run([*git, "add", "."], check=True, timeout=60)
    subprocess.run([*git, "commit", "-q", "-m", "plugin"], check=True, timeout=60)
    bare = tmp_path / "heinzel-dummy.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, timeout=60)
    rewrite = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{bare}.insteadOf",
        "GIT_CONFIG_VALUE_0": "https://git.example.test/acme/heinzel-dummy",
    }
    spec = "heinzel-dummy @ git+https://git.example.test/acme/heinzel-dummy@main"
    return spec, rewrite


def test_a_plugin_preview_hint_works_pasted_with_its_operation_receipt(sandbox, tmp_path):
    installed_from_git(sandbox)
    spec, rewrite = dummy_git_source(sandbox, tmp_path)
    preview = sandbox.run(sandbox.hz_bin, "system", "plugin", "add", spec, **rewrite)
    assert "Not installed yet" in preview
    [hint] = [line for line in preview.splitlines() if line.startswith("To install: ")]
    command = shlex.split(hint.removeprefix("To install: "))
    assert command[0] == "hz"
    operation_id = command[command.index("--operation-id") + 1]
    assert re.fullmatch(r"[A-Za-z0-9_-]{40,50}", operation_id)
    assert "Installed heinzel-dummy" in sandbox.run(sandbox.hz_bin, *command[1:], **rewrite)
    assert [p["distribution"] for p in json.loads(sandbox.hz("system", "plugin", "list", "--json"))] == [
        "heinzel-dummy"
    ]
    assert sandbox.hz("dummy", "--help")  # the plugin loads


def test_a_plugin_from_a_git_repository_is_added_with_confirm(sandbox, tmp_path):
    """A git spec over https, served from a local bare repository through git's insteadOf (no network)."""
    installed_from_git(sandbox)
    spec, rewrite = dummy_git_source(sandbox, tmp_path)
    preview = sandbox.run(sandbox.hz_bin, "system", "plugin", "add", spec, **rewrite)
    assert "Not installed yet" in preview
    assert "✓ Installed" in sandbox.run(sandbox.hz_bin, "system", "plugin", "add", spec, "--confirm", **rewrite)
    assert sandbox.hz("dummy", "hello").strip() == "hello from dummy"


def test_the_web_head_streams_an_independent_plugin_installation(sandbox, tmp_path):
    import httpx

    installed_from_git(sandbox)
    spec, rewrite = dummy_git_source(sandbox, tmp_path)
    ui = subprocess.Popen(
        [str(sandbox.hz_bin), "ui", "--port", "0"],
        env={**sandbox.env, **rewrite, "BROWSER": "true"},
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert ui.stderr is not None
        line = next(line for line in ui.stderr if "runs at" in line)
        url = line.strip().rsplit(" ", 1)[-1]
        base, token = url.split("#token=")
        auth = {"Authorization": f"Bearer {token}"}
        add = f"{base.rstrip('/')}/api/system/plugin/add"
        shown = httpx.post(add, params={"spec": spec}, headers=auth, timeout=120).json()
        assert shown["done"] is False
        assert shown["candidate"]["first_party"] is False
        # The UI streams installation so the task appears immediately and quiet builds stay alive across proxies.
        events: list[tuple[str, dict]] = []
        event_name = ""
        data = ""
        with httpx.stream(
            "POST",
            f"{base.rstrip('/')}/api/stream/system/plugin/add",
            params={"spec": shown["candidate"]["spec"], "confirm": True},
            headers=auth,
            timeout=240,
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            for line in response.iter_lines():
                if line.startswith("event: "):
                    event_name = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data = line.removeprefix("data: ")
                elif not line and event_name and data:
                    events.append((event_name, json.loads(data)))
                    event_name = data = ""
                    if events[-1][0] in {"result", "error"}:
                        break
        assert events[0] == ("task", {"text": "Adding plugin heinzel-dummy"})
        assert events[-1][0] == "result", events
        assert events[-1][1]["data"]["done"] is True
    finally:
        ui.terminate()
        ui.wait(30)
        if ui.stderr is not None:
            ui.stderr.close()
    assert sandbox.hz("dummy", "--help")
