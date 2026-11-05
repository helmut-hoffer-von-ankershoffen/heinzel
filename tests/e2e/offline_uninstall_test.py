"""Real installed command and uv removal under OS network denial, using immutable isolated manager doubles."""

import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

import pytest

from heinzel import Config, Settings
from heinzel.heads import install
from heinzel.kernel import ProcessOutcome, user_config_dir
from heinzel.modules.system.daemon import _service as daemon
from tests.e2e._candidate_wheel import CANDIDATE_VERSION, candidate_wheel
from tests.e2e.tool_install_test import REPO, Sandbox, sandbox, seed_uv_cache, wheel

__all__ = ["sandbox", "seed_uv_cache", "wheel"]
pytestmark = pytest.mark.e2e

PLUGIN_MODULE = b'from heinzel import Module\nmodule = Module("offline-fixture", "Isolated offline fixture.")\n'

MANAGER_FIXTURE = r"""import importlib.metadata, json, os, sys
from pathlib import Path
from urllib.request import urlopen
from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.daemon import _service as daemon

root = Path(os.environ["HZ_OFFLINE_MANAGER_FIXTURE"])
del os.environ["HZ_OFFLINE_MANAGER_FIXTURE"]


def calendar_probe(command):
    import plistlib, shlex

    database = root / "calendar-manager.json"
    entries = json.loads(database.read_text()) if database.exists() else {}
    linux = command[0] == "/usr/bin/systemctl"
    operation = command[2] if linux else command[1]
    if operation == "bootstrap":
        path = Path(command[-1])
        value = plistlib.loads(path.read_bytes())
        entries[value["Label"]] = {"path": str(path), "active": True}
        database.write_text(json.dumps(entries))
        return ProcessOutcome(0, "", "")
    if linux and operation == "enable":
        name = command[-1]
        entries[name] = {"path": str(Path.home() / ".config/systemd/user" / name), "active": True}
        database.write_text(json.dumps(entries))
        return ProcessOutcome(0, "", "")
    name = command[-1].rsplit("/", 1)[-1] if not linux or operation != "show" else command[3]
    if operation in {"bootout", "disable"} and name in entries:
        entries[name]["active"] = False
        database.write_text(json.dumps(entries))
        return ProcessOutcome(0, "", "")
    if operation == "daemon-reload" and database.exists():
        return ProcessOutcome(0, "", "")
    if operation == "list" and database.exists():
        web = json.loads((root / "web-manager.json").read_text()) if (root / "web-manager.json").exists() else None
        return ProcessOutcome(
            0,
            "PID\tStatus\tLabel\n"
            + "".join("123 0 " + name + "\n" for name, item in entries.items() if item["active"])
            + ("123 0 io.github.helmut-hoffer-von-ankershoffen.heinzel.web\n" if web and web["loaded"] else ""),
            "",
        )
    if operation == "list-units" and database.exists():
        return ProcessOutcome(0, "".join(name + " loaded inactive dead fixture\n" for name in entries), "")
    if name not in entries:
        return None
    item = entries[name]
    if operation == "is-enabled":
        return ProcessOutcome(1, "disabled\n", "")
    if not linux:
        if operation != "print" or not item["active"]:
            return ProcessOutcome(113, "", "")
        value = plistlib.loads(Path(item["path"]).read_bytes())
        text = (
            "path = "
            + item["path"]
            + "\nprogram = "
            + value["ProgramArguments"][0]
            + "\nworking directory = "
            + value["WorkingDirectory"]
            + "\narguments = {\n"
            + "\n".join(value["ProgramArguments"])
            + "\n}\nenvironment = {\n"
            + "\n".join(key + " => " + val for key, val in value["EnvironmentVariables"].items())
            + "\n}\npid = 123\n"
        )
        return ProcessOutcome(0, text, "")
    values = Path(item["path"]).read_text().splitlines()
    environment = [
        shlex.split(line.removeprefix("Environment="))[0].replace("%%", "%")
        for line in values
        if line.startswith("Environment=")
    ]
    args = shlex.split(next(line.removeprefix("ExecStart=") for line in values if line.startswith("ExecStart=")))
    directory = json.loads(
        next(line.removeprefix("WorkingDirectory=") for line in values if line.startswith("WorkingDirectory="))
    )
    text = (
        "Id="
        + name
        + "\nLoadState=loaded\nFragmentPath="
        + item["path"]
        + "\nDropInPaths=\nExecStart={ path="
        + args[0]
        + " ; argv[]="
        + " ".join(args)
        + " ; ignore_errors=no ; }\n"
        + "Environment="
        + shlex.join(environment)
        + "\nWorkingDirectory="
        + directory
        + "\nActiveState="
        + ("active" if item["active"] else "inactive")
        + "\nUnitFileState="
        + ("enabled" if item["active"] else "disabled")
        + "\nMainPID="
        + ("123" if item["active"] else "0")
        + "\nControlPID=0\nJob=\n"
    )
    return ProcessOutcome(0, text, "")


def web_probe(command):
    import hashlib, plistlib, shlex, subprocess

    database = root / "web-manager.json"
    item = json.loads(database.read_text()) if database.exists() else None
    linux = command[0] == "/usr/bin/systemctl"
    operation = command[2] if linux else command[1]
    target = command[3] if linux and operation == "show" else command[-1].rsplit("/", 1)[-1]
    web_label = "io.github.helmut-hoffer-von-ankershoffen.heinzel.web"
    if operation == "bootstrap" and target == web_label + ".plist":
        item = {"path": command[-1], "loaded": True, "active": True}
    elif linux and operation == "enable" and target == "hz-web.service":
        item = {"path": str(Path.home() / ".config/systemd/user/hz-web.service"), "loaded": True, "active": True}
    elif item is None:
        return None
    elif operation == "list" and not (root / "calendar-manager.json").exists():
        return ProcessOutcome(0, "PID\tStatus\tLabel\n" + ("123 0 " + web_label + "\n" if item["loaded"] else ""), "")
    elif operation in {"bootout", "disable"} and target in {web_label, "hz-web.service"}:
        if linux:
            values = Path(item["path"]).read_text().splitlines()
            launcher = Path(
                shlex.split(next(line.removeprefix("ExecStart=") for line in values if line.startswith("ExecStart=")))[
                    1
                ]
            )
        else:
            launcher = Path(plistlib.loads(Path(item["path"]).read_bytes())["ProgramArguments"][1])
        prefix = Path(os.environ["UV_TOOL_DIR"]) / "heinzel"
        sdk_gate = (
            prefix.parent
            / ".heinzel-maintenance"
            / hashlib.sha256(str(prefix).encode()).hexdigest()[:24]
            / "pending.json"
        )
        assert sdk_gate.is_file(), "Every new transition/removal must retain the SDK-wide fence"
        if str(sdk_gate) not in launcher.read_text():
            compatibility = launcher.parent.parent / "daemon/standard-install-pending.json"
            assert compatibility.is_file(), "An old shell must consume the compatibility fence"
            assert compatibility.read_bytes() == sdk_gate.read_bytes()
        cold = subprocess.run(["/bin/sh", str(launcher)], env={}, capture_output=True, check=False, timeout=5)
        assert cold.returncode == 75, "The actual historical/current installed shell must refuse before SDK imports"
        item.update(active=False, loaded=linux)
    elif operation == "daemon-reload":
        item["loaded"] = item["loaded"] and Path(item["path"]).exists()
    elif target not in {web_label, "hz-web.service"}:
        return None
    elif operation == "print":
        if not item["loaded"]:
            return ProcessOutcome(113, "", "")
        value = plistlib.loads(Path(item["path"]).read_bytes())
        text = (
            "path = "
            + item["path"]
            + "\nprogram = /bin/sh\narguments = {\n"
            + "\n".join(value["ProgramArguments"])
            + "\n}\n"
        )
        return ProcessOutcome(0, text + ("pid = 123\n" if item["active"] else ""), "")
    elif operation == "show":
        if command[-1] == "--property=ActiveState,MainPID":
            return ProcessOutcome(
                0,
                "ActiveState="
                + ("active" if item["active"] else "inactive")
                + "\nMainPID="
                + ("123" if item["active"] else "0")
                + "\n",
                "",
            )
        if not item["loaded"]:
            return ProcessOutcome(0, "LoadState=not-found\nMainPID=0\nControlPID=0\nJob=\n", "")
        values = Path(item["path"]).read_text().splitlines()
        directory = shlex.split(
            next(line.removeprefix("WorkingDirectory=") for line in values if line.startswith("WorkingDirectory="))
        )[0]
        launcher = shlex.split(
            next(line.removeprefix("ExecStart=") for line in values if line.startswith("ExecStart="))
        )[1]
        text = (
            "Id=hz-web.service\nLoadState=loaded\nFragmentPath="
            + item["path"]
            + "\nDropInPaths=\nExecStart={ path=/bin/sh ; argv[]=/bin/sh "
            + launcher
            + " ; ignore_errors=no ; }\nWorkingDirectory="
            + directory
            + "\nActiveState="
            + ("active" if item["active"] else "inactive")
            + "\nUnitFileState="
            + ("enabled" if item["active"] else "disabled")
            + "\nMainPID="
            + ("123" if item["active"] else "0")
            + "\nControlPID=0\nJob=\n"
        )
        return ProcessOutcome(0, text, "")
    else:
        return None
    database.write_text(json.dumps(item))
    return ProcessOutcome(0, "", "")


def probe(command):
    assert command[0] in {"/bin/launchctl", "/usr/bin/systemctl"}, "No PATH-selected manager may run"
    if result := web_probe(command):
        return result
    if result := calendar_probe(command):
        return result
    with (root / "manager-calls.jsonl").open("a") as stream:
        stream.write(json.dumps(command) + "\n")
    if command[0] == "/usr/bin/systemctl":
        if "list-units" in command:
            return ProcessOutcome(0, "", "")
        if "--property=Version" in command:
            return ProcessOutcome(0, "Version=255\n", "")
        unit = next((item for item in command if item.endswith((".service", ".timer"))), "")
        output = (
            "LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\nJob=\nId=" + unit + "\n"
        )
        return ProcessOutcome(0, output, "")
    name = command[1]
    if name == "managername":
        return ProcessOutcome(0, "Aqua\n", "")
    if name == "manageruid":
        return ProcessOutcome(0, str(os.getuid()) + "\n", "")
    if name == "list":
        return ProcessOutcome(0, "PID\tStatus\tLabel\n", "")
    if name == "print":
        return ProcessOutcome(113, "", "")
    raise AssertionError("A no-service fixture must never mutate its manager")


daemon._quiescence_command = probe
daemon._linux_user_manager_identity = lambda: (1, 2, 3)

from heinzel.modules.ui import _service as web_service
if not hasattr(web_service.WebService, "historical_record"):
    defaults = web_service.WebService.__init__.__kwdefaults__
    assert defaults is not None and "which" in defaults
    # Explicit old constructor seam: OS exec denial also makes native access(X_OK) false.
    defaults["which"] = lambda name: "/bin/launchctl" if name == "launchctl" else "/usr/bin/systemctl"

import subprocess

_native_run, _native_popen = subprocess.run, subprocess.Popen


def manager_command(arguments):
    return (
        isinstance(arguments, (list, tuple))
        and bool(arguments)
        and Path(str(arguments[0])).name in {"launchctl", "systemctl"}
    )


def fixture_run(arguments, *args, **kwargs):
    if manager_command(arguments):
        result = probe(list(arguments))
        if kwargs.get("check") and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, arguments, result.stdout, result.stderr)
        return subprocess.CompletedProcess(arguments, result.returncode, result.stdout, result.stderr)
    return _native_run(arguments, *args, **kwargs)


def no_native_manager(arguments, *args, **kwargs):
    assert not manager_command(arguments), "A manager double must never spawn a real user manager"
    assert not kwargs.get("shell"), "The isolated fixture never runs a shell command"
    return _native_popen(arguments, *args, **kwargs)


subprocess.run, subprocess.Popen = fixture_run, no_native_manager
if sys.argv[1:] == ["fixture", "manager-trap"]:
    try:
        subprocess.Popen(["/bin/launchctl" if sys.platform == "darwin" else "/usr/bin/systemctl", "managername"])
    except AssertionError:
        print("native manager spawn trapped before execution")
        raise SystemExit(0)
    raise AssertionError("The native manager trap was not installed")


def no_discovery(*args, **kwargs):
    raise AssertionError("Offline uninstall must not discover installed plugins")


if sys.argv[1:3] == ["system", "uninstall"]:
    from heinzel.kernel import Credentials, Installation, Kernel, OfflineUninstall, Progress, _discovery
    from heinzel.modules.org._reminder_maintenance import CalendarMaintenance

    importlib.metadata.entry_points = no_discovery
    _discovery.entry_points = no_discovery
    Credentials.__init__ = no_discovery
    original_resolve = Kernel.resolve

    def maintenance_only(kernel, kind):
        if kind not in {OfflineUninstall, CalendarMaintenance, Kernel}:
            # The existing coordinator has no records and reads only its already-bound local objects.
            assert not kernel.modules, "Only the maintenance port may resolve from the discovered registry"
            assert kind in {Installation, Progress} and kind in kernel._instances
        return original_resolve(kernel, kind)

    Kernel.resolve = maintenance_only
if os.environ.get("HZ_OFFLINE_INTERRUPT_FIXTURE") == "1":
    from heinzel.kernel import Installation

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("immutable fixture interruption before uv removal")

    Installation.uninstall_offline = interrupted
if sys.argv[1:3] == ["fixture", "retention"]:
    from datetime import UTC, datetime, timedelta
    from heinzel import Config, NoticeKind, Settings
    from heinzel.modules.system.notify import NotifyService, NotifySettings

    configuration = Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml"
    configuration.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    configuration.write_text("[system.notify]\ncalendar = true\nnative = false\nworking_hours = false\n")
    configuration.chmod(0o600)
    config = Config.load(Path.home())
    service = NotifyService(config.settings(Settings, "hz"), config.settings(NotifySettings, "system.notify"), {})
    arguments = dict(
        route="/", command="", key="calendar-retention-fixture", expires=datetime.now(UTC) + timedelta(hours=1)
    )
    assert service.emit_once(NoticeKind.CALENDAR, "Fixture reminder", "Retained replay proof", **arguments)
    assert service.emit_once(NoticeKind.CALENDAR, "Fixture reminder", "Retained replay proof", **arguments)
    assert [notice.key for notice in service.store.read()] == ["calendar-retention-fixture"]
    assert service.store.has_once("calendar-retention-fixture")
    print("retention prepared")
    raise SystemExit(0)
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
def current_candidate(tmp_path_factory):
    """Build exact current source metadata; published015 bytes stay immutable."""
    root = tmp_path_factory.mktemp("candidate-current")
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
    fixture_run(["git", "init", "--quiet", str(root)], check=True, timeout=15)
    fixture_run(
        [sys.executable, str(root / "scripts/build.py"), "--wheel", "--out-dir", str(root / "dist")],
        check=True,
        timeout=300,
    )
    return candidate_wheel(root)


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
# Landlock handles TCP destinations; seccomp keeps UDP/raw and asynchronous paths closed.
# Unix-domain IPC remains usable. No weaker policy is substituted on an unsupported kernel.
architecture = {
    'x86_64': (0xc000003e, 41, ((44, 3), (46, 2), (307, 3))),
    'aarch64': (0xc00000b7, 198, ((206, 3), (211, 2), (269, 3))),
}
if platform.machine() not in architecture:
    raise SystemExit('Offline network denial: unsupported Linux architecture')
arch, socket_call, sends = architecture[platform.machine()]
allow, denied = 0x7fff0000, 0x00050001
rows = [(0x20,0,0,4),(0x15,1,0,arch),(0x06,0,0,0x80000000),(0x20,0,0,0)]
if platform.machine() == 'x86_64':
    rows += [(0x45,0,1,0x40000000),(0x06,0,0,denied)]
rows += [(0x15,0,1,425),(0x06,0,0,denied)]  # io_uring_setup
# Older Landlock kernels do not mediate TCP Fast Open; inspect the scalar flags of every send variant.
for number, argument in sends:
    body = [(0x20,0,0,16+8*argument),(0x45,0,1,0x20000000),(0x06,0,0,denied),(0x06,0,0,allow)]
    rows += [(0x15,0,len(body),number), *body]
# Only Unix sockets or IPv4/IPv6 TCP streams (default or explicit TCP protocol) may be created.
body = [
    (0x20,0,0,16),(0x15,0,1,1),(0x06,0,0,allow),
    (0x15,2,0,2),(0x15,1,0,10),(0x06,0,0,denied),
    (0x20,0,0,24),(0x54,0,0,0xffffffff ^ (0x80000 | 0x800)),
    (0x15,1,0,1),(0x06,0,0,denied),
    (0x20,0,0,32),(0x15,2,0,0),(0x15,1,0,6),(0x06,0,0,denied),(0x06,0,0,allow),
]
rows += [(0x15,0,len(body),socket_call), *body, (0x06,0,0,allow)]
filters = (Filter*len(rows))(*(Filter(*row) for row in rows))
program = Program(len(rows),filters)
libc = ctypes.CDLL(None,use_errno=True)
libc.syscall.restype = ctypes.c_long

def require(condition, stage):
    if not condition:
        raise SystemExit(f'Offline network denial unavailable: {stage}; errno={ctypes.get_errno()}')

# Query the actual running kernel rather than inferring capability from its distribution or version.
abi = libc.syscall(444, 0, 0, 1)
require(abi >= 4, f'Landlock ABI >= 4 required, observed {abi}')
require(libc.prctl(38,1,0,0,0) == 0, 'no_new_privs')
class Ruleset(ctypes.Structure):
    _fields_ = [('handled_access_fs',ctypes.c_uint64),('handled_access_net',ctypes.c_uint64)]
ruleset = Ruleset(0, 2)  # CONNECT_TCP only, with no allowed ports; bind and filesystem access stay unchanged.
fd = libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
require(fd >= 0, 'Landlock TCP ruleset creation')
try:
    require(libc.syscall(446, fd, 0) == 0, 'Landlock TCP restriction')
finally:
    os.close(fd)
require(libc.prctl(22,2,ctypes.byref(program),0,0) == 0, 'seccomp installation')
print(f'Offline network denial active: Landlock ABI {abi}; TCP connect and non-TCP IP denied', file=sys.stderr)
os.execvpe(sys.argv[1],sys.argv[1:],os.environ)
"""


RECOVERY_FIXTURE = """import importlib.util, json, os, sys
from pathlib import Path
assert importlib.util.find_spec("heinzel") is None, "Recovery Python must not have an importable SDK"
script = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("copied_recovery", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def manager(command, platform):
    assert command[0] in {"/bin/launchctl", "/usr/bin/systemctl"}
    if platform == "linux":
        if command[-1] == "daemon-reload" or "list-units" in command: return ""
        return "LoadState=not-found\\nMainPID=0\\nControlPID=0\\nJob=\\n"
    return {"managername":"Aqua\\n", "manageruid":str(os.getuid())+"\\n", "list":"PID\\tStatus\\tLabel\\n"}[command[1]]
module._manager = manager
module._linux_identity = lambda: (1,2,3,4)
result = module.recover(script.parent)
assert importlib.util.find_spec("heinzel") is None
print(json.dumps({"phase":result["phase"]}))
"""


def recover_installed_interruption(selected, settings, environment, result):
    assert result.returncode != 0
    assert (selected.root / "tools/heinzel").is_dir()
    journals = list((settings.state_dir / "uninstall").glob("*/journal.json"))
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text())
    assert journal["phase"] == "removing"
    assert Path(journal["gate"]).is_file()
    (selected.root / "interruption-result.txt").write_text(result.stdout + result.stderr)
    result = fixture_run(
        deny_network([journal["python"], "-I", "-c", RECOVERY_FIXTURE, str(journals[0].parent / "recover.py")]),
        env=environment,
        cwd=selected.home,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["phase"] == "complete"
    assert not Path(journal["gate"]).exists()
    assert Path(journal["uv"]).is_file()
    assert not (selected.root / "tools/heinzel").exists()
    return result


MANAGER_EXEC_DENIAL = '(deny process-exec (literal "/bin/launchctl") (literal "/usr/bin/systemctl"))'


def deny_managers(command):
    if sys.platform != "darwin":
        return command  # Linux uses the explicit subprocess trap; native Linux acceptance remains separate.
    if command[0] == "/usr/bin/sandbox-exec":
        return [*command[:2], command[2] + MANAGER_EXEC_DENIAL, *command[3:]]
    return ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)" + MANAGER_EXEC_DENIAL, *command]


def fixture_run(command, **kwargs):
    return subprocess.run(deny_managers(command), check=kwargs.pop("check", False), **kwargs)


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
        if "list-units" in command:
            return ProcessOutcome(0, "", "")
        if "--property=Version" in command:
            return ProcessOutcome(0, "Version=255\n", "")
        unit = next((item for item in command if item.endswith((".service", ".timer"))), "")
        output = (
            "LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\nJob=\nId=" + unit + "\n"
        )
        return ProcessOutcome(0, output, "")
    assert command[0] == "/bin/launchctl"
    if command[1] == "print":
        return ProcessOutcome(113, "", "")
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
    # Use a real selected user profile, rather than inheriting pytest's process-only Core overrides.
    environment = {
        key: value for key, value in selected.env.items() if not key.upper().startswith("HZ_") and key != "VIRTUAL_ENV"
    } | {"HZ_OFFLINE_MANAGER_FIXTURE": str(selected.root)}
    for key, value in environment.items():
        if key in {"HOME", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON"}:
            monkeypatch.setenv(key, value)
    for key in tuple(os.environ):
        if key.upper().startswith("HZ_") or key == "VIRTUAL_ENV":
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
    result = fixture_run(
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
    updated = fixture_run(
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
    assert CANDIDATE_VERSION in updated.stdout
    installed = list((selected.root / "tools/heinzel/lib").glob("python*/site-packages/uv-0.13.7.dist-info"))
    assert len(installed) == 1, "The immutable 015 updater must resolve the new runtime dependency"


def normal_embedded_uv_paths(selected, environment, candidate):
    """Use the genuine local add/list/remove/update paths with SDK uv first on the normal bootstrap PATH."""
    prefix = selected.root / "tools/heinzel"
    environment = environment | {"PATH": f"{prefix / 'bin'}{os.pathsep}{environment['PATH']}"}

    def run(*arguments):
        result = fixture_run(
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

    discovery = fixture_run(
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
    version = fixture_run([str(prefix / "bin/uv"), "--version"], capture_output=True, text=True, check=True, timeout=15)
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
    assert CANDIDATE_VERSION in run("system", "update", "--source", str(candidate))
    receipt = (prefix / "uv-receipt.toml").read_text()
    assert str(selected.root / "bin") in receipt
    assert str(candidate) in receipt
    assert "heinzel-offline-manager-fixture" in receipt
    assert not (selected.home / ".local/share/uv/tools").exists()


def prepare_calendar_workers(selected, environment):
    from heinzel.modules.org import _reminder_service

    paths, data = [], []
    for name in ("first", "second"):
        base = selected.home / name
        profile = {"HZ_CONFIG_HOME": str(base / "config"), "HZ_CONFIG": ""}
        profile.update({f"HZ_{field.upper()}_DIR": str(base / field) for field in ("state", "data", "cache", "log")})
        result = fixture_run(
            [
                str(selected.root / "tools/heinzel/bin/python"),
                "-I",
                "-m",
                "offline_uninstall_fixture",
                "org",
                "calendar-reminders-install",
                "--confirm",
            ],
            env=environment | profile,
            cwd=selected.home,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        paths.append(_reminder_service.path_for(sys.platform, selected.home, _reminder_service.Profile(profile)))
        retained = base / "data/reminder-consent-and-replay"
        retained.parent.mkdir(parents=True, exist_ok=True)
        retained.write_text("retained")
        result = fixture_run(
            [
                str(selected.root / "tools/heinzel/bin/python"),
                "-I",
                "-m",
                "offline_uninstall_fixture",
                "fixture",
                "retention",
            ],
            env=environment | profile,
            cwd=selected.home,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        stored = [path for path in (base / "state/notify").iterdir() if path.is_file()]
        assert any(path.name == "notices.json" for path in stored)
        data.extend((path, path.read_bytes()) for path in [retained, base / "config/config.toml", *stored])
    listed = fixture_run(
        [str(selected.root / "tools/heinzel/bin/uv"), "--offline", "--no-config", "tool", "list", "--show-paths"],
        env=environment,
        cwd=selected.home,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert listed.returncode == 0, listed.stderr
    assert "heinzel" in listed.stdout
    assert ".heinzel-maintenance" not in listed.stdout
    return paths, data


def prepare_released_web(selected, environment):
    configuration = user_config_dir() / "config.toml"
    configuration.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    configuration.write_text(
        '[hz]\nfeatures=["persistent_web"]\nupdate_check=false\nauto_update=false\n'
        '[org]\nname="Tester"\nhandle="tester"\nrole="engineer"\ntimezone="UTC"\n[team]\nname="Examples"\n'
        "[system.notify]\nnative=false\n"
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    result = fixture_run(
        [
            str(selected.root / "tools/heinzel/bin/python"),
            "-I",
            "-m",
            "offline_uninstall_fixture",
            "ui",
            "install",
            "--port",
            str(port),
            "--json",
        ],
        env=environment,
        cwd=selected.home,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"]["installed"]
    state = Config.load(selected.home).settings(Settings, "hz").state_dir
    marker = state / "ui/service.json"
    record = json.loads(marker.read_text())
    assert " ui --no-browser " in record["launcher"], (
        "The immutable published015 generator creates this actual old launcher"
    )
    (selected.root / "published015-web-record.json").write_bytes(marker.read_bytes())
    return [Path(record["unit"]), state / "ui/launch.sh", marker]


def migrate_released_web(selected, environment, paths):
    record = json.loads(paths[2].read_text())
    poison = selected.root / "migration-poison"
    poison.mkdir()
    (poison / "uv").write_text("#!/bin/sh\nexit 98\n")
    (poison / "uv").chmod(0o700)
    result = fixture_run(
        deny_network(
            [
                str(selected.root / "tools/heinzel/bin/python"),
                "-I",
                "-m",
                "offline_uninstall_fixture",
                "ui",
                "install",
                "--port",
                str(record["port"]),
                "--json",
            ]
        ),
        env=environment | {"PATH": str(poison)},
        cwd=selected.home,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["changed"]
    replacement = json.loads(paths[2].read_text())
    assert " ui serve --no-browser " in replacement["launcher"]
    assert ".heinzel-maintenance" in replacement["launcher"]
    assert replacement["uv"] == str(selected.root / "tools/heinzel/bin/uv")
    assert replacement["profile"] == record["profile"]
    assert replacement["generation"] != record["generation"]


def installed_uninstall(selected, environment, change):
    interpreter = selected.root / "tools/heinzel/bin/python"
    # Execute the installed isolated module, even when the public hz script itself is the negative target.
    command = [str(interpreter), "-I", "-m", "offline_uninstall_fixture", "system", "uninstall", "--json"]
    poisoned = selected.root / "poisoned-bin"
    poisoned.mkdir()
    (poisoned / "uv").write_text("#!/bin/sh\necho unsafe-PATH-uv >&2\nexit 98\n")
    (poisoned / "uv").chmod(0o700)
    environment = environment | {"PATH": str(poisoned)}
    if change in {"recovery", "calendar-recovery", "old015-web-recovery"}:
        environment["HZ_OFFLINE_INTERRUPT_FIXTURE"] = "1"
    result = fixture_run(
        deny_network(command),
        env=environment,
        cwd=selected.home,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return result, environment


@pytest.mark.long_running
@pytest.mark.timeout(300)
@pytest.mark.parametrize(
    "change",
    [
        "none",
        "old015-upgrade",
        "old015-web-migrate",
        "old015-web-retire",
        "old015-web-recovery",
        "old015-web-foreign",
        "recovery",
        "calendar",
        "calendar-recovery",
        "calendar-foreign",
        "tampered-receipt",
        "foreign-script",
        "missing-remover",
        "unsafe-remover",
    ],
)
def test_installed_uninstall_needs_no_network_or_discovery_and_keeps_user_work(
    sandbox, wheel, monkeypatch, change, request
):
    candidate = request.getfixturevalue("current_candidate") if change.startswith("old015") else None
    selected = Sandbox(sandbox.root, sandbox.python_directory, sandbox.python, own_home=True)
    selected.home.mkdir(mode=0o700)
    environment = isolated_environment(selected, monkeypatch)
    monkeypatch.setattr(daemon, "_quiescence_command", absent_manager)
    monkeypatch.setattr(daemon, "_linux_user_manager_identity", lambda: (1, 2, 3))
    if change.startswith("old015"):
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
    if change.startswith("old015-web"):
        trapped = fixture_run(
            [
                str(selected.root / "tools/heinzel/bin/python"),
                "-I",
                "-m",
                "offline_uninstall_fixture",
                "fixture",
                "manager-trap",
            ],
            env=environment,
            cwd=selected.home,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert trapped.returncode == 0, trapped.stdout + trapped.stderr
        assert "trapped before execution" in trapped.stdout
    web_paths = prepare_released_web(selected, environment) if change.startswith("old015-web") else []
    if change.startswith("old015"):
        upgrade_published_015(selected, environment, candidate)
        if change == "old015-upgrade":
            normal_embedded_uv_paths(selected, environment, candidate)
    if change == "old015-web-migrate":
        migrate_released_web(selected, environment, web_paths)
    if change == "old015-web-foreign":
        web_paths[1].write_bytes(web_paths[1].read_bytes() + b"\n# foreign launcher change\n")
    web_expected = {path: path.read_bytes() for path in web_paths}
    data = settings.data_dir / "retained-work.txt"
    data.write_text("retained user work")
    foreign = selected.root / "tools/unrelated"
    foreign.mkdir()
    (foreign / "kept.txt").write_text("another tool")
    calendar_paths, calendar_data = (
        prepare_calendar_workers(selected, environment) if change.startswith(("calendar", "old015-web")) else ([], [])
    )
    if change == "calendar-foreign":
        calendar_paths[0].write_bytes(calendar_paths[0].read_bytes() + b"\n# foreign change\n")
    calendar_expected = {path: path.read_bytes() for path in calendar_paths}
    receipt = selected.root / "tools/heinzel/uv-receipt.toml"
    alter_installation(change, receipt, selected, settings)
    result, environment = installed_uninstall(selected, environment, change)
    if change in {"recovery", "calendar-recovery", "old015-web-recovery"}:
        result = recover_installed_interruption(selected, settings, environment, result)
    assert data.read_text() == "retained user work"
    assert (foreign / "kept.txt").read_text() == "another tool"
    _assert_removal_outcome(
        change,
        result,
        selected,
        receipt,
        ((calendar_paths, calendar_data, calendar_expected), (web_paths, web_expected)),
    )


def _assert_removal_outcome(change, result, selected, receipt, owned):
    calendar, web = owned
    web_paths, web_expected = web
    calendar_paths, calendar_data, calendar_expected = calendar
    if web_paths:
        assert result.returncode == (1 if change == "old015-web-foreign" else 0), result.stdout + result.stderr
        if change == "old015-web-foreign":
            assert {path: path.read_bytes() for path in web_paths} == web_expected
            assert json.loads((selected.root / "web-manager.json").read_text())["active"]
        else:
            assert all(not path.exists() for path in web_paths)
            assert not json.loads((selected.root / "web-manager.json").read_text())["active"]
    if change in {
        "none",
        "old015-upgrade",
        "recovery",
        "calendar",
        "calendar-recovery",
        "old015-web-migrate",
        "old015-web-retire",
        "old015-web-recovery",
    }:
        assert result.returncode == 0, result.stderr
        if change not in {"recovery", "calendar-recovery", "old015-web-recovery"}:
            assert json.loads(result.stdout)["removed"] is True
            assert json.loads(result.stdout)["web_removed"] is bool(web_paths)
        assert not (selected.root / "tools/heinzel").exists()
        assert not selected.hz_bin.exists()
        calls = [json.loads(line) for line in (selected.root / "manager-calls.jsonl").read_text().splitlines()]
        assert calls
        assert all(call[0] in {"/bin/launchctl", "/usr/bin/systemctl"} for call in calls)
        if not calendar_paths and not web_paths:
            assert not any("bootout" in call or "disable" in call or "stop" in call for call in calls)
        else:
            manager = json.loads((selected.root / "calendar-manager.json").read_text())
            assert len(manager) == 2
            assert all(not item["active"] for item in manager.values())
            assert all(not path.exists() for path in calendar_paths)
            assert all(path.read_bytes() == content for path, content in calendar_data)
    else:
        assert result.returncode == 1
        assert receipt.exists()
        if change in {"calendar-foreign", "old015-web-foreign"}:
            manager = json.loads((selected.root / "calendar-manager.json").read_text())
            assert all(item["active"] for item in manager.values())
            assert all(path.read_bytes() == content for path, content in calendar_expected.items())
            assert all(path.read_bytes() == content for path, content in calendar_data)
            calls = [json.loads(line) for line in (selected.root / "manager-calls.jsonl").read_text().splitlines()]
            assert not any("bootout" in call or "disable" in call or "stop" in call for call in calls)
        else:
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
            result = fixture_run(
                deny_network([sys.executable, "-I", "-c", code]), capture_output=True, text=True, check=False, timeout=5
            )
            assert result.returncode == 0, result.stderr
            connection, _ = ipc.accept()
            with connection:
                assert connection.recv(64) == b"local manager IPC"
