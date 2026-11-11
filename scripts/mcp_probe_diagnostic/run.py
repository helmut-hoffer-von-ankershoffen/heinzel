"""Run the original two real-server cases with one synthetic CPU companion."""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from overlap import overlap_milliseconds

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("--workers", choices=("2", "3"), default="3")
args = parser.parse_args()
if sys.platform != "darwin" or sys.version_info[:2] != (3, 15):
    msg = "diagnostic requires macOS Python 3.15"
    raise SystemExit(msg)
allowed = re.compile(
    r"VNXD case=(?:true|false) elapsed_ms=[0-9]+ sample=(?:not_needed|unavailable|other|"
    r"import|network|wait|import_network|import_wait|network_wait|import_network_wait)\Z"
)
phase_allowed = re.compile(
    r"VNXD case=(?:true|false) phase=(?:setup|call|teardown) "
    r"outcome=(?:passed|failed|skipped) kind=(?:none|timeout|assertion|exception|unknown) duration_ms=[0-9]+\Z"
)
with tempfile.TemporaryDirectory(dir=root / ".venv", prefix="vnxd-") as tmp:
    env = os.environ.copy()
    env.update(
        {
            "HOME": tmp,
            "TMPDIR": tmp,
            "XDG_CONFIG_HOME": tmp + "/config",
            "XDG_CACHE_HOME": tmp + "/cache",
            "VNXD_PHASE_DIR": tmp,
            "UV_CACHE_DIR": tmp + "/uv-cache",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_PROJECT_ENVIRONMENT": str(root / ".venv"),
            "PYTHONPATH": os.pathsep.join(filter(None, (str(Path(__file__).parent), os.environ.get("PYTHONPATH")))),
        }
    )
    # Existing explicit coordinator state variables must never escape into the nested process.
    for key in tuple(env):
        if key.startswith("HZ_") or key == "PYTEST_DISABLE_PLUGIN_AUTOLOAD":
            del env[key]
    cmd = [
        "uv",
        "run",
        "--frozen",
        "--no-sync",
        "--python",
        str(root / ".venv/bin/python"),
        "python",
        "-m",
        "pytest",
        "-n",
        args.workers,
        "-q",
        "-rP",
        "--tb=no",
        "-p",
        "vnxd_plugin",
        "-p",
        "no:cacheprovider",
        "tests/unit/heads/mcp_http_check_test.py::test_check_initializes_and_lists_actual_authenticated_server",
        "scripts/mcp_probe_diagnostic/companion_test.py::test_cpu_companion",
    ]
    try:
        run = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=45, check=False)  # noqa: S603
    except subprocess.TimeoutExpired:
        print("VNXD runner=timeout")
        raise SystemExit(1) from None
    lines = []
    for case in ("true", "false"):
        file = Path(tmp) / case
        if file.is_file():
            line = file.read_text().strip()
            if allowed.fullmatch(line):
                lines.append(line)
    for line in lines:
        print(line)
    for case in ("true", "false"):
        for phase in ("setup", "call", "teardown"):
            path = Path(tmp) / f"{case}.{phase}"
            line = path.read_text().strip() if path.is_file() else ""
            if phase_allowed.fullmatch(line):
                print(line)
            else:
                print(f"VNXD case={case} phase={phase} outcome=not_recorded")
    if run.returncode and "ImportError" in run.stderr:
        print("VNXD error=plugin_import")
    if run.returncode and "usage: pytest" in run.stderr:
        print("VNXD error=pytest_usage")
    companion_file = Path(tmp) / "companion.call"
    companion = companion_file.read_text().strip() if companion_file.is_file() else "not_recorded"
    if companion not in ("passed", "failed", "skipped"):
        companion = "not_recorded"
    print(f"VNXD companion={companion}")
    overlap = overlap_milliseconds(Path(tmp))
    if overlap is None:
        print("VNXD overlap=unavailable")
    elif overlap == 0:
        print("VNXD overlap=none ms=0")
    else:
        print(f"VNXD overlap=confirmed ms={overlap}")
    print("VNXD runner=" + ("pass" if run.returncode == 0 else "fail"))
    if len(lines) != len(("true", "false")) or companion != "passed" or overlap is None or overlap == 0:
        print("VNXD evidence=incomplete")
        raise SystemExit(1)
    raise SystemExit(run.returncode)
