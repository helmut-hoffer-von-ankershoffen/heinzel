# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fail closed before the single rendered MCP fixture, in the network-none container."""

import errno
import hashlib
import importlib.metadata
import os
import socket
import subprocess
import threading
from pathlib import Path

ROOT = Path("/opt/hz")
EXPECTED = {
    "tests/e2e/mcp_settings_test.py": "9c778734947a47334514a547c6c7b44e0fd5fe500f953183bfe45f45059989c5",
    "web/e2e/mcp-settings.mjs": "0f0b6d04396800d0de7f40c016d63102e801f80cad87a0c3515b313b5ad77781",
}


class IsolationError(Exception):
    """The environment did not meet the isolated fixture contract."""


def run() -> int:  # noqa: C901 - sequential fail-closed guards
    """Run after validating the isolated environment.

    Raises:
        IsolationError: The source or isolation guards failed.
    """
    for name, digest in EXPECTED.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            msg = f"fixture digest mismatch: {name}"
            raise IsolationError(msg)
    if importlib.metadata.entry_points(group="heinzel.modules"):
        msg = "core-only environment required"
        raise IsolationError(msg)
    if any(key in os.environ for key in ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN")):
        msg = "credential environment rejected"
        raise IsolationError(msg)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.sendall(b"fixture-loopback")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    with socket.create_connection(listener.getsockname(), timeout=2) as client:
        if client.recv(32) != b"fixture-loopback":
            msg = "loopback response mismatch"
            raise IsolationError(msg)
    thread.join(2)
    listener.close()
    if thread.is_alive():
        msg = "loopback listener did not finish"
        raise IsolationError(msg)
    print("fixture-loopback: passed", flush=True)
    for name, address in (
        ("synthetic-host", (os.environ["CONTROL_HOST"], int(os.environ["CONTROL_PORT"]))),
        ("external", ("192.0.2.1", 443)),
    ):
        try:
            with socket.create_connection(address, timeout=2):
                msg = f"{name} unexpectedly reachable"
                raise IsolationError(msg)
        except OSError as error:
            if error.errno != errno.ENETUNREACH:
                msg = f"{name} had unexpected network error {error.errno}"
                raise IsolationError(msg) from error
        print(f"{name}: network unreachable", flush=True)
    node = ROOT / ".venv/lib/python3.14/site-packages/nodejs_wheel/bin"
    env = {
        key: os.environ[key]
        for key in ("HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "PLAYWRIGHT_BROWSERS_PATH")
    }
    env["PATH"] = f"{node}:/usr/bin:/bin"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "pytest",
        "-m",
        "e2e",
        "-n",
        "0",
        "-q",
        "-p",
        "no:cacheprovider",
        "--session-timeout=900",
        "--junitxml=/artifacts/junit.xml",
        "tests/e2e/mcp_settings_test.py::test_mounted_mcp_settings_copy_preserves_the_retained_install",
    ]
    print("running selected synthetic rendered Settings fixture", flush=True)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode  # noqa: S603 - fixed test executable and arguments


if __name__ == "__main__":
    raise SystemExit(run())
