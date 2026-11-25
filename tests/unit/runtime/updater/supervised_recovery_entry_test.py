"""Reproduce the cold-start deadlock at the physical supervised launcher boundary."""

import signal
import subprocess
import sys
from pathlib import Path

from heinzel import Progress
from heinzel.kernel import Installation, private_write
from heinzel.modules.system.lifecycle._service import WebService


def test_a_killed_update_leaves_the_external_supervised_launcher_gated(tmp_path):
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

    refused = subprocess.run(command, check=False, capture_output=True, timeout=5)
    assert refused.returncode == 75
    assert refused.stdout == refused.stderr == b""
