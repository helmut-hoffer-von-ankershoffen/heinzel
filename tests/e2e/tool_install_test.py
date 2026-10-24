"""The full lifecycle, as users run it: install Heinzelmännchen with uv, add a plugin, upgrade, use, remove, uninstall.

Two paths are covered: the commands run directly, and the scripts the `hz-init` / `hz-uninstall` agent skills run.
Needs network (PyPI). Every test gets its own uv tool directory, configuration and skill directory.
"""

import json
import os
import re
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


def builtin_modules() -> set[str]:
    """The built-in modules in the checkout: packages whose `__init__.py` declares `module = Module(...)`."""
    return {
        ".".join(init.parent.relative_to(MODULES_DIR).parts)
        for init in MODULES_DIR.rglob("__init__.py")
        if init.parent != MODULES_DIR
        and re.search(r"^module = Module\(", init.read_text(encoding="utf-8"), re.MULTILINE)
    }


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
        for name in ("VIRTUAL_ENV", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            env.pop(name, None)
        if self.own_home:
            for name in ("HZ_CONFIG", "HZ_CONFIG_HOME", "HZ_DATA_DIR", "HZ_CACHE_DIR"):
                env.pop(name, None)
            # Keep uv's caches and Pythons from the real home, so the sandbox doesn't download everything again.
            env |= {"HOME": str(self.home), "UV_CACHE_DIR": UV_CACHE_DIR, "UV_PYTHON_INSTALL_DIR": UV_PYTHON_DIR}
        else:
            env |= {
                "HZ_CONFIG_HOME": str(self.root / "config"),
                "HZ_DATA_DIR": str(self.root / "data"),
                "HZ_CACHE_DIR": str(self.root / "cache"),
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

    sandbox.hz("system", "plugin", "add", DUMMY_PLUGIN)
    assert sandbox.hz("dummy", "hello").strip() == "hello from dummy"
    # ADR 0006: plugins are recorded in the uv tool receipt, so upgrading Heinzelmännchen keeps them.
    sandbox.run("uv", "tool", "upgrade", "heinzel")
    assert sandbox.hz("dummy", "greet", "--name", "Ada").strip() == "Hello, Ada!"
    assert json.loads(sandbox.hz("system", "plugin", "list", "--json")) == [
        {"distribution": "heinzel-dummy", "version": "1.2.3", "modules": ["dummy", "dummy.greet"]}
    ]
    assert json.loads(sandbox.hz("system", "info", "--json"))["sections"] == {"dummy": {"greeting": "hello from dummy"}}

    # `hz system update --source` switches the source and keeps plugins too.
    assert sandbox.hz("system", "update", "--source", str(wheel)).startswith("hz ")
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
