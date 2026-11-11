"""An isolated host manager that starts actual Core without touching the system service manager."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel import Config, Settings
from heinzel.kernel import HostServiceManager
from heinzel.modules.system.mcp import _service


class Installed:
    def __init__(self, folder):
        self.prefix = folder / "sdk"
        self.binary = folder / "bin"
        self.prefix.mkdir(mode=0o700)
        self.binary.mkdir(mode=0o700)
        source = Path(__file__).resolve().parents[1] / "fixtures/mcp_runtime_cli.py"
        executable = self.binary / "hz"
        executable.write_text(f"#!{sys.executable}\n" + source.read_text())
        executable.chmod(0o700)
        self.verified = True
        self.fingerprint = "a" * 64

    def tool_destination(self):
        # The tool store is deliberately different from the exact running SDK prefix.
        return self.prefix.parent / "tool-store", self.binary

    def startup_state_matches(self):
        return self.verified

    def startup_fingerprint(self):
        return self.fingerprint

    def tool_entrypoints(self):
        return (self.binary / "hz",)


class Manager(HostServiceManager):
    """Never calls launchctl: only the actual fixture Core worker is created or stopped."""

    def __init__(self, folder):
        self.calls = []
        self.jobs = {}
        self.fail_bootstrap = False
        self.changed_program = False
        self.hide_inactive_state = False
        self.extra = []
        self.reported_pid = None
        self.poison = folder / "caller"
        self.poison.mkdir(mode=0o700)
        (self.poison / "hz.toml").write_text("fictional poisoned caller = [")
        (self.poison / "hz").mkdir()
        (self.poison / "hz/config.toml").write_text("fictional poisoned site = [")

    def close(self):
        for _, _, process in self.jobs.values():
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self.jobs.clear()

    @property
    def process(self):
        return next(reversed(self.jobs.values()))[2] if self.jobs else None

    def bootstrap(self, unit):
        assert "LaunchAgents" not in unit.parts
        definition = plistlib.loads(unit.read_bytes())
        assert definition["Label"] not in self.jobs
        argv = definition["ProgramArguments"]
        assert argv[:2] == ["/usr/bin/env", "-i"]
        assert definition["KeepAlive"] is False
        with Path(definition["StandardErrorPath"]).open("ab") as stderr:
            process = subprocess.Popen(
                argv,
                cwd=definition["WorkingDirectory"],
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(self.poison),
                    "XDG_CONFIG_DIRS": str(self.poison),
                    "HZ_CONFIG": str(self.poison / "hz.toml"),
                    "HZ_UPDATE_CHECK": "true",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )
        self.jobs[definition["Label"]] = unit, definition, process

    def describe(self, job):
        unit, definition, process = job
        argv = definition["ProgramArguments"]
        program = "/fictional/unowned" if self.changed_program else argv[0]
        output = (
            f"gui/{os.getuid()}/{definition['Label']} = {{\n\tpath = {unit}\n\tprogram = {program}\n\targuments = {{\n"
        )
        output += "".join(f"\t\t{arg}\n" for arg in argv)
        output += f"\t}}\n\tworking directory = {definition['WorkingDirectory']}\n"
        if process.poll() is None:
            output += f"\tactive count = 1\n\tstate = running\n\tpid = {self.reported_pid or process.pid}\n"
        elif not self.hide_inactive_state:
            output += "\tactive count = 0\n\tstate = not running\n"
        return output + "}\n"

    def command(self, arguments):
        self.calls.append(tuple(arguments))
        assert arguments[0] == "/bin/launchctl"
        verb = arguments[1]
        status, output = 0, ""
        label = arguments[2].split("/")[-1] if len(arguments) > 2 else ""
        if verb == "list":
            output = "PID\tStatus\tLabel\n"
            for name, (_, _, process) in self.jobs.items():
                pid = process.pid if process.poll() is None else "-"
                output += f"{pid}\t0\t{name}\n"
            output += "".join(f"-\t0\t{label}\n" for label in self.extra)
        elif verb == "print":
            if label not in self.jobs or arguments[2] != f"gui/{os.getuid()}/{label}":
                status = 113
            else:
                output = self.describe(self.jobs[label])
        elif verb == "bootstrap":
            assert arguments[2] == f"gui/{os.getuid()}"
            if self.fail_bootstrap:
                status = 1
            else:
                self.bootstrap(Path(arguments[3]))
        elif verb == "bootout":
            pytest.fail(f"Managed MCP attempted a label-addressed effect: {label}")
        else:
            pytest.fail(f"Unexpected isolated manager verb: {verb}")
        return subprocess.CompletedProcess(arguments, status, output, "")


@pytest.fixture
def managed(tmp_path, monkeypatch, make_kernel):
    folder = tmp_path.resolve()
    installed = Installed(folder)
    manager = Manager(folder)
    calls = []
    real_call = _service.Service._call
    real_request = _service.Service._request

    def trace(service, authority, generation, args, input_=b""):
        calls.append(args[0])
        return real_call(service, authority, generation, args, input_)

    def trace_request(service, authority, generation, *, stop=False):
        calls.append("managed-stop" if stop else "mcp-check")
        return real_request(service, authority, generation, stop=stop)

    monkeypatch.setattr(_service, "platform", lambda: "darwin")
    monkeypatch.setattr(_service, "installation", lambda: installed)
    monkeypatch.setattr(_service.Service, "manager", lambda _: manager)
    monkeypatch.setattr(_service.Service, "_call", trace)
    monkeypatch.setattr(_service.Service, "_request", trace_request)
    monkeypatch.setattr(_service, "installation_mutation_pending", lambda *_: False)

    def kernel(*, enabled=True):
        settings = Settings(
            state_dir=folder / "selected/state",
            data_dir=folder / "selected/data",
            cache_dir=folder / "selected/cache",
            log_dir=folder / "selected/logs",
            features=frozenset({"managed_mcp"}) if enabled else frozenset(),
            plugins_enabled=False,
            update_check=False,
            auto_update=False,
        )
        return make_kernel(builtins=True, settings_=settings, config=Config.empty())

    try:
        yield installed, manager, calls, kernel
    finally:
        manager.close()
