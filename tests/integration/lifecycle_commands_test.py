"""Real process locks prevent managed commands from crossing an activation boundary."""

import fcntl
import hashlib
import json
import os
from uuid import uuid4

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._models import Activation, RuntimeManifest
from heinzel.modules.system.lifecycle._store import LifecycleStore, atomic_record

pytestmark = pytest.mark.integration


@pytest.fixture
def installed(tmp_path):
    profile = tmp_path / "profiles" / "stable" / "default"
    profile.mkdir(mode=0o700, parents=True)
    profile.parent.chmod(0o700)
    profile.parent.parent.chmod(0o700)
    (profile / "profile.json").write_text(
        json.dumps(
            {
                "version": 1,
                "id": str(uuid4()),
                "owner": os.getuid(),
                "name": "default",
                "channel": "stable",
            }
        )
    )
    (profile / "profile.json").chmod(0o600)
    store = LifecycleStore(profile / "lifecycle")
    with store.lock():
        deployments = store.directory / "deployments"
        deployments.mkdir(mode=0o700)
        executable = tmp_path / "python"
        executable.write_bytes(b"checked fixture")
        executable.chmod(0o700)
        manifest = RuntimeManifest(
            core_version="0.7.12",
            python=str(executable),
            python_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            ui_sha256="a" * 64,
            files={"worker.py": hashlib.sha256(b"worker").hexdigest()},
        )
        encoded = manifest.model_dump_json().encode() + b"\n"
        deployment = hashlib.sha256(encoded).hexdigest()
        directory = deployments / deployment
        directory.mkdir(mode=0o700)
        (directory / "worker.py").write_bytes(b"worker")
        (directory / "worker.py").chmod(0o600)
        atomic_record(directory / "runtime.json", manifest)
        atomic_record(store.directory / "active.json", Activation(generation=1, deployment=deployment, previous=None))
    return store


def test_existing_writer_refuses_switch_without_replacing_active_generation(installed):
    store = installed
    before = store.active()
    descriptor = os.open(store.directory / "writers.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with store.lock():
            plan = store.prepare(None)
            with pytest.raises(HeinzelError, match="commands"):
                store.switch(plan.id)
            assert store.active() == before
            assert store.pending().phase == "prepared"
    finally:
        os.close(descriptor)
    with store.lock():
        assert store.switch(plan.id) is None
        store.complete(plan.id)


def test_only_exact_prepared_intent_can_be_cancelled_without_rewinding_generation(installed):
    store = installed
    before = store.active()
    with store.lock():
        plan = store.prepare(None)
        with pytest.raises(HeinzelError, match="stale"):
            store.cancel(uuid4())
        store.cancel(plan.id)
        assert store.active() == before
        assert store.pending() is None
        next_plan = store.prepare(None)
        assert next_plan.id != plan.id
        store.switch(next_plan.id)
        with pytest.raises(HeinzelError, match="stale"):
            store.cancel(next_plan.id)
        assert store.active() is None


def test_prepare_drains_new_claims_while_existing_commands_finish(installed):
    from heinzel.modules.system.lifecycle import claim_command

    store = installed
    first = claim_command(store)
    second = claim_command(store)
    try:
        assert first.activation == second.activation == store.active()
        with store.lock():
            plan = store.prepare(None)
        with pytest.raises(HeinzelError, match="draining"):
            claim_command(store)
        first.lease.close()
        with store.lock(), pytest.raises(HeinzelError, match="commands"):
            store.switch(plan.id)
        second.lease.close()
        with store.lock():
            store.cancel(plan.id)
        with claim_command(store).lease, store.lock():
            next_plan = store.prepare(None)
            assert next_plan.generation > plan.generation
            with pytest.raises(HeinzelError, match="commands"):
                store.switch(next_plan.id)
    finally:
        first.lease.close()
        second.lease.close()


def test_forwarded_child_holds_writer_after_parent_crash_until_actual_child_exit(installed):
    import selectors
    import subprocess
    import sys

    store = installed
    child = "import os,sys; print('ready',flush=True); sys.stdin.buffer.read(1); print('done',flush=True)"
    owner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import os,subprocess,sys
from pathlib import Path
from heinzel.modules.system.lifecycle import claim_command
from heinzel.modules.system.lifecycle._store import LifecycleStore
claim = claim_command(LifecycleStore(Path(sys.argv[1])))
subprocess.Popen([sys.executable, '-c', sys.argv[2]], pass_fds=(claim.lease.descriptor,))
os._exit(0)
""",
            str(store.directory),
            child,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(owner.stdout, selectors.EVENT_READ)
            assert ready.select(5), "child did not reach its inherited-lease boundary"
        assert owner.stdout.readline() == "ready\n"
        assert owner.wait(timeout=5) == 0
        with store.lock():
            plan = store.prepare(None)
            with pytest.raises(HeinzelError, match="commands"):
                store.switch(plan.id)
        owner.stdin.close()
        with selectors.DefaultSelector() as finished:
            finished.register(owner.stdout, selectors.EVENT_READ)
            assert finished.select(5), "child did not finish after its input closed"
        assert owner.stdout.read() == "done\n"
        with store.lock():
            assert store.switch(plan.id) is None
            store.complete(plan.id)
    finally:
        owner.stdin.close()
        owner.wait(timeout=5)
        owner.stdout.close()
        owner.stderr.close()


def test_private_typed_authority_controls_do_not_take_an_ordinary_writer_lease(installed):
    import json
    import subprocess
    import sys

    store = installed
    with store.lock():
        plan = store.prepare(None)
    result = subprocess.run(
        [sys.executable, "-m", "heinzel._lifecycle_control"],
        input=json.dumps({"directory": str(store.directory), "action": "cancel", "id": str(plan.id)}),
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "value": None}
    assert store.pending() is None
    with store.lock():
        plan = store.prepare(None)
    result = subprocess.run(
        [sys.executable, "-m", "heinzel._lifecycle_control"],
        input=json.dumps({"directory": str(store.directory), "action": "switch", "id": str(plan.id)}),
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "value": None}
    assert store.active() is None


@pytest.mark.parametrize("damage", ["symlink", "hardlink", "public", "directory"])
def test_unsafe_barrier_is_refused_before_command_or_transition(installed, damage):
    from heinzel.modules.system.lifecycle import claim_command

    store = installed
    path = store.directory / "writers.lock"
    if damage == "directory":
        path.mkdir(mode=0o700)
    elif damage == "symlink":
        path.symlink_to(store.directory / "active.json")
    elif damage == "hardlink":
        target = store.directory / "other.lock"
        target.touch(mode=0o600)
        os.link(target, path)
    else:
        path.touch(mode=0o644)
    with pytest.raises(HeinzelError, match="barrier"):
        claim_command(store)
    with store.lock():
        plan = store.prepare(None)
        with pytest.raises(HeinzelError, match="barrier"):
            store.switch(plan.id)
    assert store.active().generation == 1


def test_inactive_or_modified_runtime_never_receives_a_command_lease(installed):
    from heinzel.modules.system.lifecycle import claim_command

    store = installed
    before = store.active()
    (store.directory / "deployments" / before.deployment / "worker.py").write_bytes(b"changed")
    with pytest.raises(HeinzelError, match="unavailable"):
        claim_command(store)
    (store.directory / "active.json").unlink()
    with pytest.raises(HeinzelError, match="no active runtime"):
        claim_command(store)


def test_profile_lookup_reads_existing_identity_without_creating_a_missing_profile(tmp_path):
    from heinzel.modules.system.lifecycle import profile_directory

    root = tmp_path / "profiles"
    assert profile_directory(root, "default", "stable", os.getuid()) is None
    assert not root.exists()
    root.mkdir(mode=0o700)
    assert profile_directory(root, "default", "stable", os.getuid()) is None
    (root / "stable").mkdir(mode=0o700)
    assert profile_directory(root, "default", "stable", os.getuid()) is None
    assert not (root / "stable" / "default").exists()


@pytest.mark.parametrize(
    "change", [None, "owner", "name", "channel", "link", "hardlink", "mode", "directory-mode", "invalid"]
)
def test_profile_lookup_never_adopts_a_changed_or_foreign_identity(installed, change):
    from heinzel.modules.system.lifecycle import profile_directory

    profile = installed.directory.parent
    root = profile.parent.parent
    path = profile / "profile.json"
    before = json.loads(path.read_text())
    if change in {"owner", "name", "channel"}:
        value = {"owner": os.getuid() + 1, "name": "other", "channel": "preview"}[change]
        path.write_text(json.dumps(before | {change: value}))
    elif change in {"link", "hardlink"}:
        other = profile / "retained.json"
        path.rename(other)
        if change == "link":
            path.symlink_to(other)
        else:
            os.link(other, path)
    elif change == "mode":
        path.chmod(0o644)
    elif change == "directory-mode":
        profile.chmod(0o755)
    elif change == "invalid":
        path.write_text("not-json: rejected-fixture")
    if change is None:
        assert profile_directory(root, "default", "stable", os.getuid()) == profile
    else:
        with pytest.raises(HeinzelError):
            profile_directory(root, "default", "stable", os.getuid())
    assert path.exists()


@pytest.mark.parametrize("name", ["../default", "UPPER", ""])
def test_profile_lookup_refuses_path_input_before_creating_any_state(tmp_path, name):
    from heinzel.modules.system.lifecycle import profile_directory

    root = tmp_path / "profiles"
    with pytest.raises(HeinzelError):
        profile_directory(root, name, "stable", os.getuid())
    assert not root.exists()
