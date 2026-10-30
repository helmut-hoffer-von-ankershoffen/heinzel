"""Read-only Linux user-manager acceptance for disposable CI runners.

Only queries manager properties and absent probe units; never starts, stops, or writes a service unit.
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Never

from heinzel import Kernel, Progress, Settings
from heinzel.kernel import Installation
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._service import (
    _linux_user_manager_identity,
    _quiescence_command,
    _systemd_quiescent_unit,
)


class ProbeBlockedError(RuntimeError):
    """The existing user manager could not establish a safe observation."""


def _blocked(detail: str) -> Never:
    raise ProbeBlockedError(detail)


def _observe(prefix: str) -> tuple[str, list[str]]:
    if sys.platform != "linux":
        _blocked("Linux user manager required; no query attempted")
    identity = _linux_user_manager_identity()
    if identity is None:
        _blocked("private user manager socket is missing, inaccessible, or not safely owned")
    if not Path("/usr/bin/systemctl").is_file():
        _blocked("/usr/bin/systemctl is unavailable")
    manager = ["/usr/bin/systemctl", "--user", "--no-pager", "show", "--all", "--property=Version"]
    units = [prefix + ".service", prefix + ".timer"]
    properties = "--property=Id,LoadState,ActiveState,SubState,Job,MainPID,ControlPID"
    commands = [
        manager,
        *[["/usr/bin/systemctl", "--user", "--no-pager", "show", "--all", properties, unit] for unit in units * 2],
        manager,
    ]
    outcomes = [_quiescence_command(command) for command in commands]
    if any(item.returncode != 0 or item.truncated for item in outcomes):
        _blocked("systemctl could not return complete manager and unit properties")
    first, *reports, last = [item.stdout for item in outcomes]
    if not re.fullmatch(r"Version=[^\r\n=]{1,128}\n?", first) or first != last:
        _blocked("manager version is missing or changed")
    for name, report in zip(units * 2, reports, strict=True):
        if not _systemd_quiescent_unit(report, name) or "LoadState=not-found\n" not in report:
            _blocked(f"{name} is not a confirmed absent and inactive test unit")
    if _linux_user_manager_identity() != identity:
        _blocked("private manager socket identity changed during inspection")
    return first.strip(), units


def _product_snapshot() -> None:
    with tempfile.TemporaryDirectory(prefix="heinzel-linux-manager-proof-") as temp:
        root = Path(temp).resolve()
        (root / "tools").mkdir()
        (root / "bin").mkdir()
        settings = Settings(
            data_dir=root / "data", cache_dir=root / "cache", state_dir=root / "state", log_dir=root / "logs"
        )
        installation = Installation(Progress(), prefix=root / "tools/heinzel", uv="uv")
        service = DaemonService(
            Kernel(settings), settings, DaemonSettings(unit_dir=root / "units"), quiescence_installation=installation
        )
        if not service._quiescent_snapshot(root / "bin"):
            _blocked("product service/timer state cannot be proven quiescent")


def main() -> int:
    """Inspect the existing current-user manager without mutating it.

    Returns:
        Zero when all observations pass, otherwise two.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True, help="Disposable heinzel-probe- unit prefix")
    args = parser.parse_args()
    if not re.fullmatch(r"heinzel-probe-[a-z0-9][a-z0-9-]{7,48}", args.prefix):
        parser.error("prefix must be an isolated heinzel-probe- name with at least 8 suffix characters")
    try:
        version, units = _observe(args.prefix)
        _product_snapshot()
    except ProbeBlockedError as exc:
        sys.stderr.write(f"BLOCKED: {exc}\n")
        return 2
    sys.stdout.write(
        json.dumps(
            {
                "result": "pass",
                "manager_version": version,
                "units": units,
                "job_format": "Job=",
                "product_snapshot": "quiescent",
                "manager_actions": 0,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
