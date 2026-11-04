"""Immutable installed proof double: all native manager effects are recorded, never executed."""

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from heinzel import __version__
from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.daemon import _service as daemon
from heinzel.modules.system.daemon._units import LABEL

ROOT = Path(os.environ["HZ_LEGACY_MANAGER_FIXTURE"])
STATE = ROOT / "manager.json"


def probe(command):
    if __version__ in {"0.9.14", "0.9.15"} and command[0] == "launchctl":
        command = ["/bin/launchctl", *command[1:]]  # Historical source only; never executed by this double.
    assert command[0] == "/bin/launchctl", "Recovery must never select a manager through PATH"
    with (ROOT / "manager-calls.jsonl").open("a") as stream:
        stream.write(json.dumps(command) + "\n")
    state = json.loads(STATE.read_text()) if STATE.exists() else {"registered": False}
    name = command[1]
    fixed = {"managername": "Aqua\n", "manageruid": f"{os.getuid()}\n"}
    if name in fixed:
        return ProcessOutcome(0, fixed[name], "")
    if name == "list":
        return listing(state, command)
    if name == "print":
        if not state["registered"]:
            return ProcessOutcome(113, "", "")
        return loaded(state)
    if name == "kickstart":
        with (ROOT / "tick-stdout.txt").open("w") as output, (ROOT / "tick-stderr.txt").open("w") as errors:
            child = subprocess.Popen(
                [sys.executable, "-I", "-m", "legacy_recovery_fixture", "system", "daemon", "tick", "--json"],
                env=os.environ,
                stdout=output,
                stderr=errors,
                start_new_session=True,
            )
        state["pid"] = child.pid
    elif name == "bootstrap":
        path = Path(command[-1])
        state = {"registered": True, "path": str(path), "loaded": plistlib.loads(path.read_bytes())}
    elif name == "bootout":
        state["registered"] = False
    else:
        message = f"Unexpected manager request: {command}"
        raise AssertionError(message)
    STATE.write_text(json.dumps(state))
    return ProcessOutcome(0, "", "")


def listing(state, command):
    if command[-1] == LABEL:
        pid = state.get("pid", 0)
        return ProcessOutcome(0, f'{{\n"Label" = "{LABEL}";\n"PID" = {pid};\n}}\n', "")
    output = "PID\tStatus\tLabel\n"
    if state["registered"]:
        output += f"{state.get('pid') or '-'}\t0\t{LABEL}\n"
    return ProcessOutcome(0, output, "")


def loaded(state):
    data = state["loaded"]
    output = f"path = {state['path']}\nprogram = {data['ProgramArguments'][0]}\narguments = {{\n"
    output += "".join(f"{arg}\n" for arg in data["ProgramArguments"]) + "}\n"
    if "WorkingDirectory" in data:
        output += f"working directory = {data['WorkingDirectory']}\n"
    output += (
        "environment = {\n"
        + "".join(f"{key} => {value}\n" for key, value in data["EnvironmentVariables"].items())
        + "}\n"
    )
    output += (
        "default environment = {\nPATH => /usr/bin:/bin\n}\n"
        f"inherited environment = {{\nHOME => {Path.home()}\n}}\nstate = not running\n"
    )
    return ProcessOutcome(0, f"gui/{os.getuid()}/{LABEL} = {{\n{output}}}\n", "")


def runner(command):
    if __version__ in {"0.9.14", "0.9.15"} and command[1:] == ["tool", "dir", "--bin"]:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)
    if command == ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"]:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=5)
    result = probe(command)
    return subprocess.CompletedProcess(command, result.returncode, result.stdout, result.stderr)


# Replace only the native manager seam. Installation, receipt, CLI and update code remain real.
daemon._quiescence_command = probe
original_init = daemon.DaemonService.__init__


def initialize(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
    self._runner = runner
    self._quiescence_probe = probe
    self._desktop_session = lambda: "Aqua"


daemon.DaemonService.__init__ = initialize
if sys.argv[1:2] == ["automatic-015"]:
    from functools import partial

    from heinzel.kernel import boot
    from heinzel.modules.system._announced import Announcement, trial_start
    from heinzel.modules.system._update import UpdateService

    kernel = boot()
    update = kernel.resolve(UpdateService)
    trial = partial(
        trial_start,
        command=[sys.executable, "-I", "-m", "legacy_recovery_fixture", "ui", "--port", "0", "--no-browser"],
    )
    result = update.apply_announced(
        Announcement(core="0.9.15", plugins={}),
        current=__version__,
        folder=kernel.settings.state_dir / "auto-update",
        trial=trial,
    )
    sys.stdout.write(result.model_dump_json() + "\n")
    assert result.updated
    assert not result.restored
    assert not result.refused
elif sys.argv[1:2] == ["recovery"]:
    import traceback

    from heinzel.heads import daemon_recovery
    from heinzel.kernel import DaemonLegacyRecovery

    original_recovery = daemon_recovery.recovery

    def traced(call):
        def invoke(*args):
            try:
                return call(*args)
            except Exception:
                traceback.print_exc()
                raise

        return invoke

    def selected():
        port = traced(original_recovery)()
        return DaemonLegacyRecovery(
            traced(port.plan), traced(port.retire), traced(port.restore_plan), traced(port.restore)
        )

    daemon_recovery.recovery = selected
    result = daemon_recovery.main(sys.argv[2:])
    assert "legacy_manager_plugin" not in sys.modules, "Recovery must not discover installed plugins"
    sys.exit(result)
else:
    from heinzel.heads.cli import main

    try:
        main()
    finally:
        if sys.argv[1:4] == ["system", "daemon", "tick"] and STATE.exists():
            state = json.loads(STATE.read_text())
            if state.get("pid") == os.getpid():
                state["pid"] = None
                STATE.write_text(json.dumps(state))
