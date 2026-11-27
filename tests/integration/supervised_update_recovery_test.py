"""Real process death and shell launcher recovery against isolated installations."""

import json
import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation
from heinzel.kernel.install._installation_mutations import installation_mutation_pending
from heinzel.modules.system import recover_announced
from heinzel.modules.system._recovery_boot import NAME
from heinzel.modules.system.daemon._abandoned_mutation import (
    Evidence,
    marker_token_matches,
    mutation_record,
    recover_abandoned_mutation,
)
from heinzel.modules.system.daemon._mutation_scope import MutationAuthority, MutationScope
from heinzel.modules.system.daemon._runtime import write_private
from heinzel.modules.system.lifecycle._recovery_launcher import interpreter
from heinzel.modules.system.updater import _announced as announced
from heinzel.modules.system.updater import _recovery_staging
from heinzel.modules.system.updater._adapter_state import FileInstallationState
from heinzel.modules.system.updater._update import UpdateService
from heinzel.testing import scaled
from tests.unit.runtime.updater import supervised_recovery_entry_test as launcher_fixture
from tests.unit.runtime.updater.system_announced_test import Releases, news, replace_tool_prefix, service

pytestmark = [pytest.mark.integration, pytest.mark.real_system]
installed_launch = launcher_fixture.installed_launch


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


def _interrupt(web):
    state = web.directory.parent
    dead = subprocess.run(
        [sys.executable, "-c", _UPDATE, str(state), str(web.prefix)],
        check=False,
        capture_output=True,
        timeout=scaled(15),
    )
    assert dead.returncode == -signal.SIGKILL, dead.stderr
    assert (state / "daemon" / "mutation-pending.json").is_file()
    assert (state / "auto-update" / "before-update").is_dir()


def _kill_after_scope_verification(update, tool, marker, token, monkeypatch):
    lifecycle = update._lifecycle

    @contextmanager
    def mutation(checkpoint, rollback_identity):
        authority = MutationAuthority(
            pending_path=marker,
            token_matches=lambda token: marker_token_matches(marker.read_bytes(), token),
            capture=lifecycle.capture,
            request=lambda plan, binding: lifecycle.request(plan),
            fingerprint=tool.startup_fingerprint,
            checkpoint=checkpoint,
            had_checkpoint=checkpoint(),
            original_fingerprint=tool.startup_fingerprint(),
            original_identity=rollback_identity(),
            rollback_identity=rollback_identity,
        )
        scope = MutationScope(token, authority)
        yield scope
        scope.commit()

    verify = MutationScope.verified_complete

    def die_before_disarm(scope, expected):
        verify(scope, expected)
        os.kill(os.getpid(), signal.SIGKILL)

    monkeypatch.setattr(lifecycle, "mutation", mutation)
    monkeypatch.setattr(MutationScope, "verified_complete", die_before_disarm)


def _kill_at_update_phase(tmp_path, phase):
    """Subprocess entry point for process death, including the absence of finally handlers."""
    state = tmp_path / "state"
    state_port = FileInstallationState(state)
    with pytest.MonkeyPatch.context() as patch:
        if phase == "recovery":
            tool = Installation(Progress(), prefix=tmp_path / "tools/heinzel", uv="unused")
            patch.setattr(tool, "record_startup_state", lambda **kw: os.kill(os.getpid(), signal.SIGKILL))
            UpdateService(tool, Releases(), state=state_port).recover_announced(state / "auto-update")
            return

        update, tool, _, _ = service(tmp_path, patch)
        update._state = state_port
        token = "a" * 64
        marker = state / "daemon/mutation-pending.json"
        owner = mutation_record(token, os.getpid(), None)
        write_private(marker, json.dumps(owner, indent=2) + "\n")
        (tmp_path / "before.json").write_text(json.dumps([tool.startup_fingerprint(), tool.content_fingerprint()]))

        if phase == "partial":
            copytree = announced.shutil.copytree

            def die_during_copy(src, dst, **kwargs):
                copytree(src, dst, **kwargs)
                os.kill(os.getpid(), signal.SIGKILL)

            patch.setattr(announced.shutil, "copytree", die_during_copy)
        elif phase == "commit":
            patch.setattr(tool, "record_startup_state", lambda **kw: os.kill(os.getpid(), signal.SIGKILL))
        elif phase == "verified":
            _kill_after_scope_verification(update, tool, marker, token, patch)

        def die_during_update(*args, **kwargs):
            if phase in {"commit", "install", "verified"}:
                replace_tool_prefix(tool)
            if phase in {"partial", "snapshot", "install"}:
                os.kill(os.getpid(), signal.SIGKILL)

        patch.setattr(tool, "run", die_during_update)
        update.apply_announced(news(), current="1.0.0", folder=state / "auto-update", trial=lambda _: True)


def kill_at_update_phase(tmp_path, phase):
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy, sys; from pathlib import Path; "
                "runpy.run_path(sys.argv[1])['_kill_at_update_phase'](Path(sys.argv[2]), sys.argv[3])"
            ),
            __file__,
            str(tmp_path),
            phase,
        ],
        check=False,
        timeout=scaled(20),
    )
    assert child.returncode == -signal.SIGKILL


@pytest.mark.parametrize("phase", ["partial", "snapshot", "install", "commit", "verified", "recovery"])
def test_killed_updater_restores_verified_continuity(tmp_path, phase):
    kill_at_update_phase(tmp_path, "install" if phase == "recovery" else phase)
    state = tmp_path / "state"
    kernel = SimpleNamespace(settings=SimpleNamespace(state_dir=state))
    tool = Installation(Progress(), prefix=tmp_path / "tools/heinzel", uv="unused")
    update = UpdateService(tool, Releases(), state=FileInstallationState(state))
    original_generation, original_content = json.loads((tmp_path / "before.json").read_text())
    snapshot = state / "auto-update/before-update"
    partial = snapshot.with_suffix(".partial")
    assert partial.is_dir() == (phase == "partial")
    assert snapshot.is_dir() == (phase != "partial")
    assert installation_mutation_pending(tool.prefix, state)

    def recover_daemon():
        return recover_abandoned_mutation(state, tool, lambda: Evidence(None, 1), lambda: datetime.now(UTC))

    assert recover_daemon() is None
    if phase == "verified":
        assert tool.installed_core_version() == "9.9.9"
        assert tool.startup_state_matches()
        assert not (state / "daemon/handover-request.json").exists()
    if phase == "recovery":
        kill_at_update_phase(tmp_path, "recovery")
        assert snapshot.is_dir()
        assert not list(snapshot.iterdir())
        assert installation_mutation_pending(tool.prefix, state)

    assert recover_announced(update, kernel) == (phase != "partial")
    assert tool.content_fingerprint() == original_content
    assert (tool.startup_fingerprint() == original_generation) == (phase in {"partial", "snapshot"})
    assert tool.startup_state_matches()
    receipt = recover_daemon()
    assert receipt is not None
    assert json.loads(receipt.read_text())["status"] == "archived"
    assert not installation_mutation_pending(tool.prefix, state)
    assert not snapshot.exists()
    assert not partial.exists()


def test_legacy_launcher_refuses_new_updates_and_keeps_its_original_guard(installed_launch):
    web, installation, _ = installed_launch
    command = launcher_fixture._launch(web)
    state = web.directory.parent
    with (
        pytest.raises(HeinzelError, match="Migrate the owned web launcher"),
        _recovery_staging.supervised_recovery(installation, state / "auto-update", web.recovery_ready),
    ):
        pytest.fail("the older launcher admitted an automatic update")
    assert not (state / "auto-update" / "before-update").exists()
    assert not (state / "daemon" / "mutation-pending.json").exists()
    _interrupt(web)
    refused = subprocess.run(command, check=False, capture_output=True, timeout=scaled(5))
    assert refused.returncode == 75
    assert refused.stdout == refused.stderr == b""


def test_sealed_worker_restores_checkpoint_before_launcher_enters_the_tool(installed_launch):
    web, installation, worker = installed_launch
    selected = interpreter(web.prefix)
    assert selected is not None
    command = launcher_fixture._launch(web, recovery=selected)
    state = web.directory.parent
    assert (
        subprocess.run(command, check=True, capture_output=True, timeout=scaled(5)).stdout
        == b"old generation started\n"
    )
    with _recovery_staging.supervised_recovery(installation, state / "auto-update", web.recovery_ready):
        _interrupt(web)
    staged = state / NAME / "site" / worker
    trusted = staged.read_text()
    staged.write_text("raise AssertionError('modified worker')\n")
    refused = subprocess.run(command, check=False, capture_output=True, timeout=scaled(15))
    assert refused.returncode == 75
    assert refused.stdout == b""
    assert b"modified worker" not in refused.stderr
    assert (state / "auto-update" / "before-update").is_dir()

    staged.write_text(trusted)
    recovered = subprocess.run(command, check=False, capture_output=True, timeout=scaled(15))
    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout == b"old generation started\n"
    assert installation.startup_state_matches()
    assert not (state / "daemon" / "mutation-pending.json").exists()
    assert not (state / NAME).exists()
