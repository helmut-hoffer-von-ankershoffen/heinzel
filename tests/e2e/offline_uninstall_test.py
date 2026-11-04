"""Real installed command and uv removal under OS network denial, using immutable isolated manager doubles."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

import pytest

from heinzel import Config, Settings
from heinzel.heads import install
from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.daemon import _service as daemon
from tests.e2e.tool_install_test import REPO, Sandbox, sandbox, seed_uv_cache, wheel

__all__ = ["sandbox", "seed_uv_cache", "wheel"]
pytestmark = pytest.mark.e2e

PLUGIN_MODULE = b'from heinzel import Module\nmodule = Module("offline-fixture", "Isolated offline fixture.")\n'

MANAGER_FIXTURE = """import importlib.metadata, json, os, sys
from pathlib import Path
from urllib.request import urlopen
from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.daemon import _service as daemon
root = Path(os.environ["HZ_OFFLINE_MANAGER_FIXTURE"])
def probe(command):
    assert command[0] in {"/bin/launchctl", "/usr/bin/systemctl"}, "No PATH-selected manager may run"
    with (root / "manager-calls.jsonl").open("a") as stream:
        stream.write(json.dumps(command) + "\\n")
    if command[0] == "/usr/bin/systemctl":
        if "--property=Version" in command: return ProcessOutcome(0, "Version=255\\n", "")
        unit = next((item for item in command if item.endswith((".service", ".timer"))), "")
        output = ("LoadState=not-found\\nActiveState=inactive\\nSubState=dead\\nMainPID=0\\n"
                  "ControlPID=0\\nJob=\\nId=" + unit + "\\n")
        return ProcessOutcome(0, output, "")
    name = command[1]
    if name == "managername": return ProcessOutcome(0, "Aqua\\n", "")
    if name == "manageruid": return ProcessOutcome(0, str(os.getuid()) + "\\n", "")
    if name == "list": return ProcessOutcome(0, "PID\\tStatus\\tLabel\\n", "")
    if name == "print": return ProcessOutcome(113, "", "")
    raise AssertionError("A no-service fixture must never mutate its manager")
daemon._quiescence_command = probe
daemon._linux_user_manager_identity = lambda: (1,2,3)
def no_discovery(*args, **kwargs):
    raise AssertionError("Offline uninstall must not discover installed plugins")
if sys.argv[1:3] == ["system", "uninstall"]:
    importlib.metadata.entry_points = no_discovery
from heinzel.heads.cli import main
main()
"""


def manager_wheel(directory: Path) -> Path:
    name, version = "heinzel_offline_manager_fixture", "1.0.0"
    files = {
        "offline_uninstall_fixture.py": MANAGER_FIXTURE.encode(),
        "offline_manager_plugin/__init__.py": PLUGIN_MODULE,
        f"{name}-{version}.dist-info/entry_points.txt": (
            b"[heinzel.modules]\noffline-fixture = offline_manager_plugin\n"
        ),
        f"{name}-{version}.dist-info/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{name}-{version}.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = f"{name}-{version}.dist-info/RECORD"
    lines = [
        f"{path},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')},{len(data)}"
        for path, data in files.items()
    ]
    files[record] = ("\n".join([*lines, f"{record},,"]) + "\n").encode()
    target = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return target


@pytest.fixture(scope="module")
def candidate_016(tmp_path_factory):
    """Build real current source under isolated release metadata; published 015 bytes stay immutable."""
    root = tmp_path_factory.mktemp("candidate-016")
    for name in (
        "pyproject.toml",
        "uv.lock",
        "hatch_build.py",
        "README.md",
        "PYPI.md",
        "ATTRIBUTIONS.md",
        "LICENSE",
        "CHANGELOG.md",
        ".gitignore",
    ):
        shutil.copy2(REPO / name, root / name)
    for name in ("src", "scripts", "web", "agent-skills"):
        shutil.copytree(
            REPO / name,
            root / name,
            ignore=shutil.ignore_patterns("node_modules", "__pycache__", "test-results", "work", ".vite"),
        )
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=15)
    project = root / "pyproject.toml"
    project.write_text(project.read_text().replace('version = "0.9.15"', 'version = "0.9.16"', 1))
    lock = root / "uv.lock"
    lock.write_text(
        lock.read_text().replace('name = "heinzel"\nversion = "0.9.15"', 'name = "heinzel"\nversion = "0.9.16"', 1)
    )
    subprocess.run(
        [sys.executable, str(root / "scripts/build.py"), "--wheel", "--out-dir", str(root / "dist")],
        check=True,
        timeout=300,
    )
    target = root / "dist/heinzel-0.9.16-py3-none-any.whl"
    assert target.is_file()
    return target


@pytest.fixture(scope="module")
def published_015(tmp_path_factory):
    """Download immutable publisher bytes once online, with independently recorded artifact SHA256."""
    target = tmp_path_factory.mktemp("published-015") / "heinzel-0.9.15-py3-none-any.whl"
    url = (
        "https://files.pythonhosted.org/packages/ed/31/"
        "331f330e2afeb9d1d0138f38f3156742ad9852957c0bb5eb08695f330622/heinzel-0.9.15-py3-none-any.whl"
    )
    with urlopen(url, timeout=60) as response:
        content = response.read(8 * 1024 * 1024)
    assert hashlib.sha256(content).hexdigest() == "bbcaaf457bd9adacf469293881d9edb2c539c40d3cc3191148714238e8afd435"
    target.write_bytes(content)
    return target


DENY_LINUX = """import ctypes, os, platform, sys
class Filter(ctypes.Structure):
    _fields_ = [('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint)]
class Program(ctypes.Structure):
    _fields_ = [('len',ctypes.c_ushort),('filter',ctypes.POINTER(Filter))]
arch, number = {'x86_64':(0xc000003e,41),'aarch64':(0xc00000b7,198)}[platform.machine()]
rows = [(0x20,0,0,4),(0x15,1,0,arch),(0x06,0,0,0x80000000),(0x20,0,0,0),
        (0x15,0,4,number),(0x20,0,0,16),(0x15,1,0,2),(0x15,0,1,10),
        (0x06,0,0,0x00050001),(0x06,0,0,0x7fff0000)]
filters = (Filter*len(rows))(*(Filter(*row) for row in rows))
program = Program(len(rows),filters)
libc = ctypes.CDLL(None,use_errno=True)
assert libc.prctl(38,1,0,0,0) == 0, ctypes.get_errno()
assert libc.prctl(22,2,ctypes.byref(program),0,0) == 0, ctypes.get_errno()
os.execvpe(sys.argv[1],sys.argv[1:],os.environ)
"""


def deny_network(command: list[str]) -> list[str]:
    if sys.platform == "darwin":
        return [
            "/usr/bin/sandbox-exec",
            "-p",
            "(version 1)(allow default)(deny network-outbound (remote ip))",
            *command,
        ]
    assert sys.platform == "linux", "The offline installer supports only macOS and Linux"
    return [sys.executable, "-I", "-c", DENY_LINUX, *command]


def absent_manager(command):
    if command[0] == "/usr/bin/systemctl":
        if "--property=Version" in command:
            return ProcessOutcome(0, "Version=255\n", "")
        unit = next((item for item in command if item.endswith((".service", ".timer"))), "")
        output = (
            "LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\nJob=\nId=" + unit + "\n"
        )
        return ProcessOutcome(0, output, "")
    assert command[0] == "/bin/launchctl"
    output = {"managername": "Aqua\n", "manageruid": f"{os.getuid()}\n", "list": "PID\tStatus\tLabel\n"}
    return ProcessOutcome(0, output[command[1]], "")


def alter_installation(change, receipt, selected, settings):
    if change == "tampered-receipt":
        receipt.write_text(receipt.read_text() + "\n# changed after verification\n")
    elif change == "foreign-script":
        selected.hz_bin.unlink()
        selected.hz_bin.write_text("foreign command must stay")
        selected.hz_bin.chmod(0o700)
    elif change == "missing-remover":
        (selected.root / "tools/heinzel/bin/uv").unlink()
    elif change == "unsafe-remover":
        (selected.root / "tools/heinzel/bin/uv").chmod(0o770)


def isolated_environment(selected, monkeypatch):
    environment = selected.env | {"HZ_OFFLINE_MANAGER_FIXTURE": str(selected.root)}
    for key, value in environment.items():
        if key in {
            "HOME",
            "UV_TOOL_DIR",
            "UV_TOOL_BIN_DIR",
            "UV_CACHE_DIR",
            "UV_PYTHON_INSTALL_DIR",
            "UV_PYTHON",
            "HZ_OFFLINE_MANAGER_FIXTURE",
        }:
            monkeypatch.setenv(key, value)
    for key in (
        "HZ_CONFIG",
        "HZ_CONFIG_HOME",
        "HZ_DATA_DIR",
        "HZ_CACHE_DIR",
        "HZ_STATE_DIR",
        "HZ_LOG_DIR",
        "VIRTUAL_ENV",
    ):
        monkeypatch.delenv(key, raising=False)
    return environment


def retain_manager_fixture_source(selected, environment):
    """Use the genuine local plugin mutation so the old updater retains this unpublished fixture source."""
    source = selected.root / "manager-source"
    source.mkdir()
    (source / "offline_uninstall_fixture.py").write_text(MANAGER_FIXTURE)
    (source / "offline_manager_plugin").mkdir()
    (source / "offline_manager_plugin/__init__.py").write_bytes(PLUGIN_MODULE)
    (source / "pyproject.toml").write_text(
        '[project]\nname = "heinzel-offline-manager-fixture"\nversion = "1.0.0"\n'
        '[project.entry-points."heinzel.modules"]\noffline-fixture = "offline_manager_plugin"\n'
        '[build-system]\nrequires = ["hatchling>=1.32.4"]\nbuild-backend = "hatchling.build"\n'
        '[tool.hatch.build.targets.wheel]\nonly-include = ["offline_uninstall_fixture.py", "offline_manager_plugin"]\n'
    )
    interpreter = selected.root / "tools/heinzel/bin/python"
    result = subprocess.run(
        [str(interpreter), "-I", "-m", "offline_uninstall_fixture", "system", "plugin", "develop", str(source)],
        env=environment,
        cwd=selected.home,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def upgrade_published_015(selected, environment, candidate):
    retain_manager_fixture_source(selected, environment)
    assert not list((selected.root / "tools/heinzel/lib").glob("python*/site-packages/uv-*.dist-info"))
    interpreter = selected.root / "tools/heinzel/bin/python"
    updated = subprocess.run(
        [str(interpreter), "-I", "-m", "offline_uninstall_fixture", "system", "update", "--source", str(candidate)],
        env=environment,
        cwd=selected.home,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    (selected.root / "old015-update-result.txt").write_text(updated.stdout + updated.stderr)
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert "0.9.16" in updated.stdout
    installed = list((selected.root / "tools/heinzel/lib").glob("python*/site-packages/uv-0.13.7.dist-info"))
    assert len(installed) == 1, "The immutable 015 updater must resolve the new runtime dependency"


def normal_embedded_uv_paths(selected, environment, candidate):
    """Use the genuine local add/list/remove/update paths with SDK uv first on the normal bootstrap PATH."""
    prefix = selected.root / "tools/heinzel"
    environment = environment | {"PATH": f"{prefix / 'bin'}{os.pathsep}{environment['PATH']}"}

    def run(*arguments):
        result = subprocess.run(
            [str(prefix / "bin/python"), "-I", "-m", "offline_uninstall_fixture", *arguments],
            env=environment,
            cwd=selected.home,
            capture_output=True,
            text=True,
            check=False,
            timeout=240,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    discovery = subprocess.run(
        [
            str(prefix / "bin/python"),
            "-I",
            "-c",
            "from heinzel.heads.bootstrap import installed_app; print(installed_app())",
        ],
        env=environment,
        cwd=selected.home,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert discovery.stdout.strip() == str(prefix / "bin/hz")
    version = subprocess.run(
        [str(prefix / "bin/uv"), "--version"], capture_output=True, text=True, check=True, timeout=15
    )
    assert version.stdout.startswith("uv 0.13.7")
    probe = selected.root / "probe-source"
    probe.mkdir()
    (probe / "heinzel_offline_probe").mkdir()
    (probe / "heinzel_offline_probe/__init__.py").write_text(
        'from heinzel import Module\nmodule = Module("offline-probe", "Isolated plugin fixture.")\n'
    )
    (probe / "pyproject.toml").write_text(
        '[project]\nname = "heinzel-offline-probe"\nversion = "1.0.0"\n'
        '[project.entry-points."heinzel.modules"]\noffline-probe = "heinzel_offline_probe"\n'
        '[build-system]\nrequires = ["hatchling>=1.32.4"]\nbuild-backend = "hatchling.build"\n'
    )
    assert "heinzel-offline-manager-fixture" in run("system", "plugin", "list", "--json")
    assert json.loads(run("system", "plugin", "develop", str(probe), "--json"))["done"]
    assert "heinzel-offline-probe" in run("system", "plugin", "list", "--json")
    assert json.loads(run("system", "plugin", "remove", "heinzel-offline-probe", "--json"))["done"]
    assert "heinzel-offline-probe" not in run("system", "plugin", "list", "--json")
    assert "0.9.16" in run("system", "update", "--source", str(candidate))
    receipt = (prefix / "uv-receipt.toml").read_text()
    assert str(selected.root / "bin") in receipt
    assert str(candidate) in receipt
    assert "heinzel-offline-manager-fixture" in receipt
    assert not (selected.home / ".local/share/uv/tools").exists()


@pytest.mark.long_running
@pytest.mark.timeout(300)
@pytest.mark.parametrize(
    "change", ["none", "old015-upgrade", "tampered-receipt", "foreign-script", "missing-remover", "unsafe-remover"]
)
def test_installed_uninstall_needs_no_network_or_discovery_and_keeps_user_work(
    sandbox, wheel, monkeypatch, change, request
):
    candidate = request.getfixturevalue("candidate_016") if change == "old015-upgrade" else None
    selected = Sandbox(sandbox.root, sandbox.python_directory, sandbox.python, own_home=True)
    selected.home.mkdir(mode=0o700)
    environment = isolated_environment(selected, monkeypatch)
    monkeypatch.setattr(daemon, "_quiescence_command", absent_manager)
    monkeypatch.setattr(daemon, "_linux_user_manager_identity", lambda: (1, 2, 3))
    if change == "old015-upgrade":
        wheel = request.getfixturevalue("published_015")
    fixture = manager_wheel(selected.root)
    artifacts = [
        install.Artifact(
            name=name,
            version=version,
            filename=path.name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            url=str(path),
        )
        for name, version, path in (
            ("heinzel", install.__version__ if hasattr(install, "__version__") else "0.9.15", wheel),
            ("heinzel-offline-manager-fixture", "1.0.0", fixture),
        )
    ]
    # Use the wheel's actual version; the candidate can be built from a release or a development branch.
    from packaging.utils import parse_wheel_filename

    artifacts[0] = artifacts[0].model_copy(update={"version": str(parse_wheel_filename(wheel.name)[1])})
    profile = install.AppProfile(format=2, core_version=artifacts[0].version, sdk=1, artifacts=artifacts)
    settings = Config.load(selected.home).settings(Settings, "hz")
    paths = install.prepare(profile, settings.data_dir / "app-artifacts", set())
    assert install._managed_install(profile, artifacts, paths, settings, set()) == 0
    if change == "old015-upgrade":
        upgrade_published_015(selected, environment, candidate)
        normal_embedded_uv_paths(selected, environment, candidate)
    data = settings.data_dir / "retained-work.txt"
    data.write_text("retained user work")
    foreign = selected.root / "tools/unrelated"
    foreign.mkdir()
    (foreign / "kept.txt").write_text("another tool")
    receipt = selected.root / "tools/heinzel/uv-receipt.toml"
    alter_installation(change, receipt, selected, settings)
    interpreter = selected.root / "tools/heinzel/bin/python"
    # Execute the installed isolated module, even when the public hz script itself is the negative target.
    command = [str(interpreter), "-I", "-m", "offline_uninstall_fixture", "system", "uninstall", "--json"]
    poisoned = selected.root / "poisoned-bin"
    poisoned.mkdir()
    (poisoned / "uv").write_text("#!/bin/sh\necho unsafe-PATH-uv >&2\nexit 98\n")
    (poisoned / "uv").chmod(0o700)
    result = subprocess.run(
        deny_network(command),
        env=environment | {"PATH": str(poisoned)},
        cwd=selected.home,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert data.read_text() == "retained user work"
    assert (foreign / "kept.txt").read_text() == "another tool"
    if change in {"none", "old015-upgrade"}:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["removed"] is True
        assert not (selected.root / "tools/heinzel").exists()
        assert not selected.hz_bin.exists()
        calls = [json.loads(line) for line in (selected.root / "manager-calls.jsonl").read_text().splitlines()]
        assert calls
        assert all(call[0] in {"/bin/launchctl", "/usr/bin/systemctl"} for call in calls)
        assert not any("bootout" in call or "disable" in call or "stop" in call for call in calls)
    else:
        assert result.returncode == 1
        assert receipt.exists()
        assert not (selected.root / "manager-calls.jsonl").exists()
        if change == "foreign-script":
            assert selected.hz_bin.read_text() == "foreign command must stay"


def test_network_denial_blocks_ip_but_preserves_local_unix_ipc(tmp_path):
    import errno
    import socket
    import tempfile

    with tempfile.TemporaryDirectory(prefix="hz-offline-", dir="/tmp") as directory:
        address = str(Path(directory) / "manager.sock")
        with socket.socket(socket.AF_UNIX) as ipc, socket.socket() as ip:
            ipc.bind(address)
            ipc.listen()
            ipc.settimeout(2)
            ip.bind(("127.0.0.1", 0))
            ip.listen()
            port = ip.getsockname()[1]
            code = (
                "import socket\n"
                "try:\n"
                f"    ip=socket.socket(); result=ip.connect_ex(('127.0.0.1',{port}))\n"
                "except OSError as error:\n"
                "    result=error.errno\n"
                f"assert result in ({errno.EPERM},{errno.EACCES}), result\n"
                f"ipc=socket.socket(socket.AF_UNIX); ipc.connect({address!r}); ipc.sendall(b'local manager IPC')\n"
            )
            result = subprocess.run(
                deny_network([sys.executable, "-I", "-c", code]), capture_output=True, text=True, check=False, timeout=5
            )
            assert result.returncode == 0, result.stderr
            connection, _ = ipc.accept()
            with connection:
                assert connection.recv(64) == b"local manager IPC"
