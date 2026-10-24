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
UV_CACHE_DIR = subprocess.run(
    ["uv", "--color", "never", "cache", "dir"], capture_output=True, text=True, check=False, timeout=60
).stdout.strip()
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
            "HZ_SYSTEM_AGENT__SKILL_DIRS": json.dumps([str(self.skills)]),
        }
        for name in ("VIRTUAL_ENV", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
            env.pop(name, None)
        if self.own_home:
            for name in ("HZ_CONFIG", "HZ_CONFIG_HOME", "HZ_DATA_DIR", "HZ_CACHE_DIR", "HZ_STATE_DIR", "HZ_LOG_DIR"):
                env.pop(name, None)
            # Keep uv's caches and Pythons from the real home, so the sandbox doesn't download everything again.
            env |= {"HOME": str(self.home), "UV_CACHE_DIR": UV_CACHE_DIR, "UV_PYTHON_INSTALL_DIR": UV_PYTHON_DIR}
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


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    dist = tmp_path_factory.mktemp("dist")
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(dist), str(REPO)], check=True, timeout=300)
    return next(dist.glob("heinzel-*.whl"))


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


def test_direct_lifecycle(sandbox, wheel):
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
    if (
        not (REPO / ".git").is_dir()
        or subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], check=False, timeout=60).returncode != 0
    ):
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
    """Core installed from this checkout's HEAD, so first-party plugins come from the same repository and revision."""
    if not (REPO / ".git").is_dir() or not HEAD:
        pytest.skip("needs a git checkout with at least one commit")
    sandbox.run("uv", "tool", "install", f"git+file://{REPO}@{HEAD}")


def test_a_first_party_plugin_is_added_by_name_and_the_previews_hint_works_pasted(sandbox):
    installed_from_git(sandbox)
    preview = sandbox.hz("system", "plugin", "add", "clock")
    assert "Not installed yet" in preview
    assert "not reviewed" not in preview  # first-party: no community warning
    [hint] = [line for line in preview.splitlines() if line.startswith("To install: ")]
    command = shlex.split(hint.removeprefix("To install: "))
    assert command[0] == "hz"
    assert "Installed heinzel-clock" in sandbox.run(sandbox.hz_bin, *command[1:])
    assert [p["distribution"] for p in json.loads(sandbox.hz("system", "plugin", "list", "--json"))] == [
        "heinzel-clock"
    ]
    assert sandbox.hz("clock", "--help")  # the plugin loads


def test_a_plugin_from_a_git_repository_is_added_with_confirm(sandbox, tmp_path):
    """A git spec over https, served from a local bare repository through git's insteadOf (no network)."""
    installed_from_git(sandbox)
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
    spec = "git+https://git.example.test/acme/heinzel-dummy@main"
    preview = sandbox.run(sandbox.hz_bin, "system", "plugin", "add", spec, **rewrite)
    assert "Not installed yet" in preview
    assert "✓ Installed" in sandbox.run(sandbox.hz_bin, "system", "plugin", "add", spec, "--confirm", **rewrite)
    assert sandbox.hz("dummy", "hello").strip() == "hello from dummy"


def test_the_web_head_installs_a_first_party_catalog_entry_by_its_install_spec(sandbox):
    import httpx

    installed_from_git(sandbox)
    entry = next(
        p
        for p in json.loads(sandbox.hz("system", "plugin", "search", "--text", "clock", "--json"))["plugins"]
        if p["short_name"] == "clock"
    )
    ui = subprocess.Popen(
        [str(sandbox.hz_bin), "ui", "--port", "0"],
        env={**sandbox.env, "BROWSER": "true"},
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
        shown = httpx.post(add, params={"spec": entry["install_spec"]}, headers=auth, timeout=120).json()
        assert shown["done"] is False
        assert shown["candidate"]["first_party"] is True
        done = httpx.post(add, params={"spec": shown["candidate"]["spec"], "confirm": True}, headers=auth, timeout=240)
        assert done.status_code == 200, done.text
        assert done.json()["done"] is True
    finally:
        ui.terminate()
        ui.wait(30)
        if ui.stderr is not None:
            ui.stderr.close()
    assert sandbox.hz("clock", "--help")
