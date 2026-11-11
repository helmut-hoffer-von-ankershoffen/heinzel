"""Explicit user-manager protocol doubles; no launchd/systemd processes or real home are accessed."""

import plistlib
import shlex
import subprocess
from pathlib import Path


class RecordedManager:
    """Records commands; `uv tool dir --bin` answers `bin_dir`, the rest exit with `codes[verb]` (0 by default)."""

    def __init__(self, bin_dir: Path, codes: dict[str, int] | None = None) -> None:
        self.bin_dir = bin_dir
        self.codes = codes or {}
        self.commands: list[list[str]] = []

    def attach(self, service):
        self.service = service
        self.registered = None
        self.active = None
        self.enabled = None
        self.loaded_data = None
        self.pid = None

    def __call__(self, command):
        self.commands.append(list(command))
        if command[1:] == ["tool", "dir", "--bin"]:
            return subprocess.CompletedProcess(command, self.codes.get("uv", 0), f"{self.bin_dir}\n", "uv broke")
        verb = next(part for part in command[1:] if not part.startswith("-"))
        code = self.codes.get(verb, 0)
        output = ""
        if verb == "managername":
            output = "Aqua\n"
        elif verb == "manageruid":
            output = "501\n"
        elif verb == "list":
            output = "PID\tStatus\tLabel\n"
        elif verb == "print":
            code, output = self._launchd_print(code)
        elif verb == "show":
            output = self._systemd_show(command)
        if not code:
            self._effect(verb, command)
        return subprocess.CompletedProcess(command, code, output, f"{verb} failed")

    def _effect(self, verb, command):
        if verb == "bootstrap":
            self.registered = True
            self.loaded_data = plistlib.loads(self.service._unit_paths()[0].read_bytes())
        if verb == "bootout":
            self.registered = False
        if verb == "daemon-reload":
            self.registered = all(path.exists() for path in self.service._unit_paths())
        if verb == "enable":
            self.enabled = True
        if verb == "disable":
            self.enabled = False
            if "--now" in command:
                self.active = False
        if verb in {"restart", "start"}:
            self.active = True
        if verb == "stop":
            self.active = False

    def _launchd_print(self, code):
        if self.registered is None:
            self.registered = all(path.exists() for path in self.service._unit_paths())
        code = code or (0 if self.registered else 113)
        if not code:
            path = self.service._unit_paths()[0]
            if self.loaded_data is None:
                self.loaded_data = plistlib.loads(path.read_bytes())
            data = self.loaded_data
            output = (
                f"path = {path}\nprogram = {data['ProgramArguments'][0]}\narguments = {{\n"
                + "".join(f"\t{arg}\n" for arg in data["ProgramArguments"])
                + "}\n"
                + (f"working directory = {data['WorkingDirectory']}\n" if "WorkingDirectory" in data else "")
                + "environment = {\n"
                + "".join(f"\t{key} => {value}\n" for key, value in data["EnvironmentVariables"].items())
                + "}\n"
            )
        if not code and self.pid is not None:
            output += f"pid = {self.pid}\n"
        return code, output if not code else ""

    def _systemd_show(self, command):
        if self.registered is None:
            self.registered = all(path.exists() for path in self.service._unit_paths())
            self.active = self.enabled = self.registered
        if not self.registered:
            output = "LoadState=not-found\n"
        else:
            unit = command[3]
            path = next(path for path in self.service._unit_paths() if path.name == unit)
            executable = self.bin_dir / "hz"
            from heinzel.modules.system.daemon._ownership import tick_environment

            env = tick_environment(executable, self.service._profile)
            output = (
                f"Id={path.name}\nLoadState=loaded\nFragmentPath={path}\nDropInPaths=\n"
                f"ExecStart={{ path={executable} ; argv[]={executable} system daemon tick ; ignore_errors=no ; }}\n"
                "Triggers=hz-daemon.service\nEnvironment="
                + shlex.join([f"{k}={v}" for k, v in env.items()])
                + f"\nWorkingDirectory={env['HOME']}\nActiveState={'active' if self.active else 'inactive'}\n"
                + f"UnitFileState={'enabled' if self.enabled else 'disabled'}\nMainPID={self.pid or 0}\n"
                + "ControlPID=0\nJob=\n"
            )
        return output

    def probe(self, command):
        from heinzel.kernel import ProcessOutcome

        result = self(command)
        return ProcessOutcome(result.returncode, result.stdout, result.stderr)

    def verbs(self) -> list[str]:
        return [
            " ".join(c[1:])
            for c in self.commands
            if c[1:] != ["tool", "dir", "--bin"]
            and next(part for part in c[1:] if not part.startswith("-"))
            not in {"managername", "manageruid", "list", "print", "show"}
        ]


def record_tool(prefix, bin_dir):
    from heinzel import Progress
    from heinzel.kernel import Installation

    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    (prefix / "bin/hz").write_text("fixture")
    (bin_dir / "hz").unlink(missing_ok=True)
    (bin_dir / "hz").symlink_to(prefix / "bin/hz")
    (prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements=[{{name="heinzel"}}]\nentrypoints=[{{name="hz",install-path={str(bin_dir / "hz")!r}}}]\n'
    )
    installation = Installation(Progress(), prefix=prefix, uv="uv")
    installation.record_startup_state(expected=installation.startup_fingerprint())
    return installation
