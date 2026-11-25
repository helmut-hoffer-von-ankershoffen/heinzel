"""Exercise real shell startup around a killed writer using an isolated SDK."""

import json
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, private_write
from heinzel.modules.system.lifecycle._recovery_boot import NAME
from heinzel.modules.system.lifecycle._recovery_launcher import interpreter
from heinzel.modules.system.lifecycle._service import WebService
from heinzel.modules.system.updater import _recovery_worker

pytestmark = pytest.mark.unit

_UPDATE = """
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


@pytest.fixture
def installed_launch(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    state, prefix = root / "state", root / "tools" / "heinzel"
    monkeypatch.setenv("HOME", str(root))
    executable = root / "bin" / "hz"
    installed = prefix / "bin" / "hz"
    executable.parent.mkdir()
    installed.parent.mkdir(parents=True)
    installed.write_text("#!/bin/sh\nprintf 'old generation started\\n'\n")
    installed.chmod(0o700)
    executable.symlink_to(installed)
    (prefix / "bin" / "python").symlink_to(Path(sys._base_executable).resolve(strict=True))
    (prefix / "uv-receipt.toml").write_text(f"""
[tool]
requirements = [{{ name = "heinzel" }}]
entrypoints = [{{ name = "hz", install-path = "{executable}" }}]
""")
    # The staged fixture delegates to the separately installed test SDK. The actual recovery
    # remains the production worker, and this fixture avoids copying the full test dependency set.
    library = prefix / "lib/python3.15/site-packages"
    packages = library / "heinzel/modules/system/updater"
    packages.mkdir(parents=True)
    for directory in (packages, *packages.parents):
        if directory == library:
            break
        (directory / "__init__.py").touch()
    worker = packages / "_recovery_worker.py"
    command = "import sys; from heinzel.modules.system.updater._recovery_worker import main; sys.exit(main())"
    worker.write_text(
        "import os, sys\n\n"
        "def main():\n"
        f"    os.execv({sys.executable!r}, [{sys.executable!r}, '-I', '-B', '-c', {command!r}, *sys.argv[1:]])\n"
    )
    installation = Installation(Progress(), prefix=prefix, uv="/unused/uv")
    installation.record_startup_state(expected=installation.startup_fingerprint())
    web = WebService(state, root, executable, prefix=prefix, uv=prefix / "bin/uv")
    return web, installation, worker.relative_to(library)


def _launch(web, *, recovery=None):
    generation = "a" * 32
    content = web._launch_content(8123, generation, recovery=recovery)
    private_write(web.launcher, content)
    web.unit.parent.mkdir(parents=True)
    web.unit.write_text(web._content())
    private_write(
        web.marker,
        json.dumps(
            {
                "port": 8123,
                "generation": generation,
                "executable": str(web.executable),
                "prefix": str(web.prefix),
                "uv": str(web.uv),
                "profile": web.profile,
                "unit": str(web.unit),
                "content": web._content(),
                "launcher": content,
            }
        ),
    )
    return ["/bin/sh", str(web.launcher)]


def _interrupt(web):
    state = web.directory.parent
    dead = subprocess.run(
        [sys.executable, "-c", _UPDATE, str(state), str(web.prefix)], check=False, capture_output=True, timeout=15
    )
    assert dead.returncode == -signal.SIGKILL, dead.stderr
    assert (state / "daemon" / "mutation-pending.json").is_file()
    assert (state / "auto-update" / "before-update").is_dir()


def test_legacy_launcher_refuses_new_updates_and_keeps_its_original_guard(installed_launch):
    web, installation, _ = installed_launch
    command = _launch(web)
    state = web.directory.parent
    with (
        pytest.raises(HeinzelError, match="Migrate the owned web launcher"),
        _recovery_worker.supervised_recovery(installation, state / "auto-update"),
    ):
        pytest.fail("the older launcher admitted an automatic update")
    assert not (state / "auto-update" / "before-update").exists()
    assert not (state / "daemon" / "mutation-pending.json").exists()
    _interrupt(web)
    refused = subprocess.run(command, check=False, capture_output=True, timeout=5)
    assert refused.returncode == 75
    assert refused.stdout == refused.stderr == b""


def test_sealed_worker_restores_checkpoint_before_launcher_enters_the_tool(installed_launch):
    web, installation, worker = installed_launch
    selected = interpreter(web.prefix)
    assert selected is not None
    command = _launch(web, recovery=selected)
    state = web.directory.parent
    assert subprocess.run(command, check=True, capture_output=True, timeout=5).stdout == b"old generation started\n"
    with _recovery_worker.supervised_recovery(installation, state / "auto-update"):
        _interrupt(web)
    staged = state / NAME / "site" / worker
    trusted = staged.read_text()
    staged.write_text("raise AssertionError('modified worker')\n")
    refused = subprocess.run(command, check=False, capture_output=True, timeout=15)
    assert refused.returncode == 75
    assert refused.stdout == b""
    assert b"modified worker" not in refused.stderr
    assert (state / "auto-update" / "before-update").is_dir()

    staged.write_text(trusted)
    recovered = subprocess.run(command, check=False, capture_output=True, timeout=15)
    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout == b"old generation started\n"
    assert installation.startup_state_matches()
    assert not (state / "daemon" / "mutation-pending.json").exists()
    assert not (state / NAME).exists()


def test_insufficient_recovery_space_refuses_before_arming_any_update(installed_launch, monkeypatch):
    web, installation, _ = installed_launch
    selected = interpreter(web.prefix)
    assert selected is not None
    _launch(web, recovery=selected)
    state = web.directory.parent
    monkeypatch.setattr(_recovery_worker.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with (
        pytest.raises(HeinzelError, match="insufficient space"),
        _recovery_worker.supervised_recovery(installation, state / "auto-update"),
    ):
        pytest.fail("an update began without space for verified recovery")
    assert not (state / "auto-update" / "before-update").exists()
    assert not (state / "daemon" / "mutation-pending.json").exists()
    assert not (state / NAME).exists()
