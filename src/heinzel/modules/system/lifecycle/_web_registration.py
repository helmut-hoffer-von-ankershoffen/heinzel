"""Render and validate the exact web service registration and launcher payloads."""

import plistlib
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import JsonValue

from heinzel.kernel import installation_mutation_paths
from heinzel.modules.system.lifecycle._historical_launcher import HistoricalLauncher
from heinzel.modules.system.lifecycle._profile import environment_command, validated_profile
from heinzel.modules.system.lifecycle._recovery_launcher import recover_config, render

WEB_SERVICE_LABEL = "io.github.helmut-hoffer-von-ankershoffen.heinzel.web"
MAX_PORT = 65535


class WebRegistration:
    """Registration policy and rendering for the service's own paths and installation."""

    if TYPE_CHECKING:
        directory: Path
        executable: Path
        home: Path
        launcher: Path
        platform: str
        prefix: Path
        profile: dict[str, str]
        unit: Path
        uv: Path

    def _launch_content(
        self,
        port: int,
        generation: str,
        profile: dict[str, str] | None = None,
        *,
        legacy: bool = False,
        recovery: tuple[str, str] | None = None,
    ) -> str:
        guards = "".join(
            f"if [ -e {shlex.quote(str(path))} ] || [ -L {shlex.quote(str(path))} ]; then exit 75; fi\n"
            for path in installation_mutation_paths(self.prefix, self.directory.parent)
        )
        directories = dict.fromkeys(
            [
                str(self.uv.parent),
                str(self.executable.parent),
                "/opt/homebrew/bin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
        )
        path = shlex.quote(":".join(directories))
        arguments = shlex.join(
            [
                str(self.executable),
                "ui",
                *([] if legacy else ["serve"]),
                "--no-browser",
                "--port",
                str(port),
                "--supervised",
                generation,
            ]
        )
        return (
            "#!/bin/sh\nset -eu\n"
            "# Recovery requires the existing update authority before any mutable tool code imports.\n"
            + render(self.platform, self.prefix, self.directory.parent, self.home, recovery)
            + guards
            + environment_command(self.profile if profile is None else profile)
            + f'exec "$@" PATH={path} {arguments}\n'
        )

    def _content(self) -> str:
        arguments = ["/bin/sh", str(self.launcher)]
        if self.platform == "darwin":
            return plistlib.dumps(
                {
                    "Label": WEB_SERVICE_LABEL,
                    "ProgramArguments": arguments,
                    "WorkingDirectory": str(self.home),
                    "RunAtLoad": True,
                    "KeepAlive": True,
                    "ThrottleInterval": 10,
                    "ProcessType": "Background",
                    # No session URL or arbitrary startup/plugin error reaches an unrotated OS log.
                    "StandardOutPath": "/dev/null",
                    "StandardErrorPath": "/dev/null",
                    "EnvironmentVariables": {
                        "PATH": f"{self.executable.parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
                    },
                },
                sort_keys=True,
            ).decode()
        command = shlex.join(arguments).replace("%", "%%").replace("$", "$$")
        directory = shlex.quote(str(self.home)).replace("%", "%%")
        return (
            "[Unit]\nDescription=Heinzelmännchen web workspace\nStartLimitIntervalSec=0\n\n"
            "[Service]\nType=simple\n"
            f"ExecStart={command}\nWorkingDirectory={directory}\n"
            "Restart=always\nRestartSec=10\nStandardOutput=null\nStandardError=null\n\n"
            "[Install]\nWantedBy=default.target\n"
        )

    def valid_record_value(self, value: dict[str, JsonValue]) -> bool:
        """Validate only exact current or historical tuples; this does not authorize files, a manager or effects."""
        if set(value) != {
            "port",
            "generation",
            "executable",
            "unit",
            "content",
            "launcher",
            "prefix",
            "uv",
            "profile",
        }:
            return False
        profile = validated_profile(
            value.get("profile"), self.home, self.directory.parent, self.prefix, self.executable
        )
        port, generation, uv = value.get("port"), value.get("generation"), value.get("uv")
        if (
            profile is None
            or type(port) is not int
            or not 1 <= port <= MAX_PORT
            or not isinstance(generation, str)
            or re.fullmatch(r"[0-9a-f]{32}", generation) is None
            or value["executable"] != str(self.executable)
            or value["prefix"] != str(self.prefix)
            or value["unit"] != str(self.unit)
            or value["content"] != self._content()
            or not isinstance(uv, str)
            or not Path(uv).is_absolute()
            or len(uv) > 4 * 1024
            or "\x00" in uv
        ):
            return False
        historical = HistoricalLauncher(self.prefix, self.directory, self.executable, Path(uv), profile)
        recovery = recover_config(str(value["launcher"]))
        return (
            value["uv"] == str(self.uv)
            and value["launcher"] == self._launch_content(port, generation, profile, recovery=recovery)
        ) or value["launcher"] in {
            historical.render(port, generation),
            historical.render(port, generation, legacy=True),
        }
