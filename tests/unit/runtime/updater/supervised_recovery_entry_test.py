"""Verify launcher and recovery staging preconditions with an isolated SDK."""

import fcntl
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, host_maintenance_lock, private_write
from heinzel.modules.system._recovery_boot import NAME
from heinzel.modules.system.lifecycle._recovery_launcher import interpreter
from heinzel.modules.system.lifecycle._service import WebService
from heinzel.modules.system.updater import _recovery_staging

pytestmark = pytest.mark.unit


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
    worker = library / "heinzel/modules/system/_auto_recovery_worker.py"
    command = "import sys; from heinzel.modules.system._auto_recovery_worker import main; sys.exit(main())"
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


def test_update_holds_first_install_locks_even_without_a_registered_web_service(installed_launch):
    web, installation, _ = installed_launch
    state = web.directory.parent
    assert not web.marker.exists()
    paths = (host_maintenance_lock(installation.prefix), web.directory / "lifecycle.lock")
    with _recovery_staging.supervised_recovery(installation, state / "auto-update", web.recovery_ready):
        for path in paths:
            with path.open("rb+") as contender, pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_insufficient_recovery_space_refuses_before_arming_any_update(installed_launch, monkeypatch):
    web, installation, _ = installed_launch
    selected = interpreter(web.prefix)
    assert selected is not None
    _launch(web, recovery=selected)
    state = web.directory.parent
    monkeypatch.setattr(_recovery_staging.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with (
        pytest.raises(HeinzelError, match="insufficient space"),
        _recovery_staging.supervised_recovery(installation, state / "auto-update", web.recovery_ready),
    ):
        pytest.fail("an update began without space for verified recovery")
    assert not (state / "auto-update" / "before-update").exists()
    assert not (state / "daemon" / "mutation-pending.json").exists()
    assert not (state / NAME).exists()


def test_registered_launcher_requires_recovery_capability_before_automatic_update(installed_launch):
    web, installation, _ = installed_launch
    _launch(web)
    state = web.directory.parent
    with (
        pytest.raises(HeinzelError, match="Migrate the owned web launcher"),
        _recovery_staging.supervised_recovery(installation, state / "auto-update", None),
    ):
        pytest.fail("a registered launcher admitted an update without a recovery capability")
    assert not (state / "auto-update" / "before-update").exists()
    assert not (state / "daemon" / "mutation-pending.json").exists()
