"""Exact pre-SDK-fence web launcher grammar, retained solely for verified migration/retirement.

The grammar comes from main505 d20e5e44 and published 0.9.15 wheel SHA256
bbcaaf457bd9adacf469293881d9edb2c539c40d3cc3191148714238e8afd435. Its four paths and
session forwarding are frozen independently of current operational launchers (ADRs 0117, 0133).
"""

import shlex
from dataclasses import dataclass
from pathlib import Path

_SESSION = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
    "SSH_AUTH_SOCK",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "SECURITYSESSIONID",
    "__CF_USER_TEXT_ENCODING",
)


def _historical_mutation_paths(prefix: Path, state: Path) -> tuple[Path, ...]:
    """Return unresolved package-mutation gates, excluding ordinary verified daemon handover requests."""
    return (
        state / "auto-update/before-update",
        state / "daemon/mutation-pending.json",
        state / "daemon/standard-install-pending.json",
        prefix / ".heinzel-startup-pending",
    )


def _historical_environment_command(profile: dict[str, str]) -> str:
    """Clear manager credentials and configuration while forwarding only OS-session capabilities at runtime."""
    preserve = "".join(f'if [ "${{{name}+x}}" = x ]; then set -- "$@" "{name}=${name}"; fi\n' for name in _SESSION)
    values = " ".join(shlex.quote(f"{name}={value}") for name, value in sorted(profile.items()))
    return f'set -- /usr/bin/env -i\n{preserve}set -- "$@" {values}\n'


@dataclass(frozen=True)
class HistoricalLauncher:
    """An inert historical byte renderer; this code never installs or executes its result."""

    prefix: Path
    directory: Path
    executable: Path
    uv: Path
    profile: dict[str, str]

    def render(self, port: int, generation: str, profile: dict[str, str] | None = None, *, legacy: bool = False) -> str:
        guards = "".join(
            f"if [ -e {shlex.quote(str(path))} ] || [ -L {shlex.quote(str(path))} ]; then exit 75; fi\n"
            for path in _historical_mutation_paths(self.prefix, self.directory.parent)
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
            + guards
            + _historical_environment_command(self.profile if profile is None else profile)
            + f'exec "$@" PATH={path} {arguments}\n'
        )
