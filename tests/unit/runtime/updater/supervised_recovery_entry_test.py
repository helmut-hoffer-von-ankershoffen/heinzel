"""Reproduce the cold-start deadlock at the physical supervised launcher boundary."""

import hashlib
import shlex
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel import Progress
from heinzel.kernel import Installation, private_write
from heinzel.modules.system.lifecycle._service import WebService


@pytest.fixture
def interrupted_launch(tmp_path):
    root = tmp_path.resolve()
    state, prefix = root / "state", root / "tools" / "heinzel"
    executable = root / "bin" / "hz"
    installed = prefix / "bin" / "hz"
    executable.parent.mkdir()
    installed.parent.mkdir(parents=True)
    installed.write_text("#!/bin/sh\nprintf 'old generation started\\n'\n")
    installed.chmod(0o700)
    executable.symlink_to(installed)
    (prefix / "uv-receipt.toml").write_text(f"""
[tool]
requirements = [{{ name = "heinzel" }}]
entrypoints = [{{ name = "hz", install-path = "{executable}" }}]
""")
    installation = Installation(Progress(), prefix=prefix, uv="/unused/uv")
    installation.record_startup_state(expected=installation.startup_fingerprint())

    web = WebService(state, root, executable, prefix=prefix, uv=Path("/unused/uv"))
    private_write(web.launcher, web._launch_content(8123, "a" * 32))
    command = ["/bin/sh", str(web.launcher)]
    assert subprocess.run(command, check=True, capture_output=True, timeout=5).stdout == b"old generation started\n"

    update = """
import json
import os
import signal
import sys
from pathlib import Path

from heinzel import Progress
from heinzel.kernel import Installation, private_lock, private_write
from heinzel.modules.system.daemon._abandoned_mutation import host_boot, mutation_record
from heinzel.modules.system.updater._adapter_state import FileInstallationState
from heinzel.modules.system.updater._announced import ToolSnapshot

state, prefix = map(Path, sys.argv[1:])
installation = Installation(Progress(), prefix=prefix, uv="/unused/uv")
with (
    private_lock(state / "web" / "auto-update-apply.lock"),
    FileInstallationState(state).lock("update"),
    private_lock(state / "daemon" / "lifecycle.lock"),
):
    marker = mutation_record("b" * 64, os.getpid(), host_boot()[0])
    private_write(state / "daemon" / "mutation-pending.json", json.dumps(marker, indent=2) + "\\n")
    ToolSnapshot.take(prefix, state / "auto-update", content=installation.content_fingerprint())
    (prefix / "bin" / "hz").write_text("#!/bin/sh\\nprintf 'partial generation ran\\\\n'\\n")
    os.kill(os.getpid(), signal.SIGKILL)
"""
    dead = subprocess.run(
        [sys.executable, "-c", update, str(state), str(prefix)], check=False, capture_output=True, timeout=10
    )
    assert dead.returncode == -signal.SIGKILL, dead.stderr
    assert (state / "daemon" / "mutation-pending.json").is_file()
    assert (state / "auto-update" / "before-update").is_dir()
    return web, command, installation


def test_a_killed_update_leaves_the_external_supervised_launcher_gated(interrupted_launch):
    _, command, _ = interrupted_launch
    refused = subprocess.run(command, check=False, capture_output=True, timeout=5)
    assert refused.returncode == 75
    assert refused.stdout == refused.stderr == b""


_WORKER = """
import sys
from datetime import UTC, datetime
from pathlib import Path

from heinzel import Progress
from heinzel.kernel import Installation, private_lock
from heinzel.modules.system.daemon._abandoned_mutation import (
    Evidence, _owner_ended, host_boot, parse_marker, recover_abandoned_mutation,
)
from heinzel.modules.system.daemon._recovery_files import recovery_file
from heinzel.modules.system.updater._adapter_state import FileInstallationState
from heinzel.modules.system.updater._announced import ToolSnapshot

state, prefix = map(Path, sys.argv[1:])
assert not Path(sys.prefix).is_relative_to(prefix)
installation = Installation(Progress(), prefix=prefix, uv="/unused/uv")
daemon = state / "daemon"
with (
    private_lock(state / "web" / "auto-update-apply.lock"),
    FileInstallationState(state).lock("update", wait=0),
    private_lock(daemon / "lifecycle.lock"),
    private_lock(daemon / "tick.lock"),
):
    marker = recovery_file(daemon / "mutation-pending.json")
    owner = parse_marker(marker.content)
    evidence = Evidence(*host_boot())
    assert owner is not None and _owner_ended(owner, marker, evidence) is not None
    assert ToolSnapshot.recover_interrupted(installation, state / "auto-update")
    receipt = recover_abandoned_mutation(state, installation, lambda: evidence, lambda: datetime.now(UTC))
    assert receipt is not None
"""

_VERIFY = """
import hashlib
import os
import stat
import sys

path, digest, *target = sys.argv[1:]
descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
with os.fdopen(descriptor, "rb") as stream:
    info = os.fstat(stream.fileno())
    assert (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and not (info.st_mode & 0o077) and info.st_size < 16384)
    source = stream.read(16384)
assert hashlib.sha256(source).hexdigest() == digest
os.execv(sys.executable, [sys.executable, "-I", "-B", "-c", source.decode(), *target])
"""


def test_external_worker_proof_restores_checkpoint_before_the_launcher_enters_the_tool(interrupted_launch):
    """Exercise the trust order and production recovery from a separate fixture SDK."""
    web, command, installation = interrupted_launch
    worker = web.directory / "probe-recovery.py"
    private_write(worker, _WORKER)
    original = web.launcher.read_text()
    preflight = shlex.join(
        [
            "/usr/bin/env",
            "-i",
            f"HOME={web.home}",
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            _VERIFY,
            str(worker),
            hashlib.sha256(_WORKER.encode()).hexdigest(),
            str(web.directory.parent),
            str(web.prefix),
        ]
    )
    # The probe launcher calls the independent worker first, leaving the original gate samples below it.
    probe = original.replace("set -eu\n", f"set -eu\n{preflight} || exit 75\n", 1)
    private_write(web.launcher, probe)
    private_write(worker, "raise AssertionError('modified worker')\n")
    refused = subprocess.run(command, check=False, capture_output=True, timeout=10)
    assert refused.returncode == 75
    assert refused.stdout == b""
    assert b"modified worker" not in refused.stderr
    assert (web.directory.parent / "auto-update" / "before-update").is_dir()

    private_write(worker, _WORKER)
    recovered = subprocess.run(command, check=False, capture_output=True, timeout=10)
    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout == b"old generation started\n"
    assert installation.startup_state_matches()
    assert not (web.directory.parent / "daemon" / "mutation-pending.json").exists()
