"""The shared Python authority fences real install/update/uninstall metadata and interrupted pointer changes."""

import fcntl
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle import _store
from heinzel.modules.system.lifecycle._models import Activation, HighWater, RuntimeManifest, RuntimeProof
from heinzel.modules.system.lifecycle._store import LifecycleStore

pytestmark = pytest.mark.integration


@pytest.fixture
def inventory(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    store = LifecycleStore(profile / "lifecycle")
    store.directory.mkdir(mode=0o700)
    (store.directory / "deployments").mkdir(mode=0o700)
    interpreter = tmp_path / "python"
    interpreter.write_bytes(b"a persistent checked interpreter fixture")
    interpreter.chmod(0o700)

    def publish(version="0.7.12"):
        payload = version.encode()
        manifest = RuntimeManifest(
            core_version=version,
            python=str(interpreter),
            python_sha256=hashlib.sha256(interpreter.read_bytes()).hexdigest(),
            ui_sha256=hashlib.sha256(b"ui").hexdigest(),
            files={"worker.py": hashlib.sha256(payload).hexdigest()},
        )
        encoded = manifest.model_dump_json().encode() + b"\n"
        identifier = hashlib.sha256(encoded).hexdigest()
        directory = store.directory / "deployments" / identifier
        directory.mkdir(mode=0o700, parents=True)
        (directory / "runtime.json").write_bytes(encoded)
        (directory / "runtime.json").chmod(0o600)
        (directory / "worker.py").write_bytes(payload)
        (directory / "worker.py").chmod(0o600)
        return identifier, directory, manifest

    return store, publish, interpreter


def proof(active, manifest):
    return RuntimeProof(
        deployment=active.deployment,
        generation=active.generation,
        instance="a" * 32,
        version=manifest.core_version,
        ui_sha256=manifest.ui_sha256,
    )


def test_install_update_uninstall_preserves_data_and_never_reuses_a_generation(inventory):
    store, publish, _ = inventory
    first, _, manifest = publish()
    second, _, updated = publish("0.7.13")
    data = store.directory.parent / "data"
    data.mkdir()
    (data / "keep.txt").write_text("owned user work")
    with store.lock():
        initial = store.prepare(first)
        assert initial.action == "install"
        active = store.switch(initial.id)
        assert active.generation == 1
        store.complete(initial.id, proof(active, manifest))
        next_plan = store.prepare(second)
        assert next_plan.action == "update"
        active = store.switch(next_plan.id)
        assert (active.generation, active.previous) == (2, first)
        store.complete(next_plan.id, proof(active, updated))
        removal = store.prepare(None)
        assert removal.action == "uninstall"
        assert store.switch(removal.id) is None
        store.complete(removal.id)
        assert store.active() is None
        reinstall = store.prepare(first)
        active = store.switch(reinstall.id)
        assert active.generation == 4
        store.complete(reinstall.id, proof(active, manifest))
    assert (data / "keep.txt").read_text() == "owned user work"
    assert store.pending() is None


def test_a_disk_pointer_or_wrong_running_proof_cannot_finish_activation(inventory):
    store, publish, _ = inventory
    candidate, _, manifest = publish()
    with store.lock():
        plan = store.prepare(candidate)
        with pytest.raises(HeinzelError, match="stale"):
            store.switch(uuid4())
        active = store.switch(plan.id)
        with pytest.raises(HeinzelError, match="actual owned backend"):
            store.complete(plan.id)
        incorrect = proof(active, manifest).model_copy(update={"generation": 99})
        with pytest.raises(HeinzelError, match="actual owned backend"):
            store.complete(plan.id, incorrect)
        assert store.pending().phase == "switched"
        store.complete(plan.id, proof(active, manifest))


@pytest.mark.parametrize("identifier", ["../runtime", "a" * 63, "g" * 64])
def test_uninventoried_runtime_identity_is_refused_without_creating_activation(inventory, identifier):
    store, _, _ = inventory
    with pytest.raises(HeinzelError):
        store.verify(identifier)
    assert store.active() is None
    assert store.pending() is None


def test_prepared_or_stale_receipt_cannot_complete_the_current_intent(inventory):
    store, publish, _ = inventory
    candidate, _, manifest = publish()
    with store.lock():
        plan = store.prepare(candidate)
        with pytest.raises(HeinzelError, match="transition changed"):
            store.complete(plan.id)
        assert store.pending() == plan
        active = store.switch(plan.id)
        with pytest.raises(HeinzelError, match="transition changed"):
            store.complete(uuid4(), proof(active, manifest))
        assert store.pending().id == plan.id
        assert store.active() == active
        store.complete(plan.id, proof(active, manifest))


@pytest.mark.parametrize("backend", [False, True])
def test_publicly_writable_authority_or_backend_lock_never_admits_a_switch(inventory, backend):
    store, publish, _ = inventory
    candidate, _, _ = publish()
    path = store.directory.parent / "backend.lock" if backend else store.directory / "lifecycle.lock"
    path.touch(mode=0o644)
    path.chmod(0o644)

    def switch_runtime():
        with store.lock():
            plan = store.prepare(candidate)
            store.switch(plan.id)

    with pytest.raises(HeinzelError):
        switch_runtime()
    assert store.active() is None


def test_first_install_cannot_reuse_cancelled_history_or_remove_an_empty_profile(inventory):
    store, publish, _ = inventory
    candidate, _, _ = publish()
    with store.lock():
        with pytest.raises(HeinzelError, match="no runtime"):
            store.prepare(None)
        plan = store.prepare(candidate, first_install=True)
        store.cancel(plan.id)
        with pytest.raises(HeinzelError, match="history"):
            store.prepare(candidate, first_install=True)
        assert store.active() is None
        assert store.pending() is None


def test_recovery_retains_prepared_intent_and_refuses_a_changed_generation_fence(inventory):
    store, publish, _ = inventory
    candidate, _, _ = publish()
    with store.lock():
        plan = store.prepare(candidate)
        assert store.recover() == "prepared"
        active = store.switch(plan.id)
        _store.atomic_record(store.directory / "generation.json", HighWater(generation=active.generation + 1))
        with pytest.raises(HeinzelError, match="disagree"):
            store.recover()
        assert store.active() == active
        assert store.pending().id == plan.id


def test_recovery_finishes_a_verified_marker_left_by_interrupted_journal_removal(inventory, monkeypatch):
    store, publish, _ = inventory
    candidate, _, manifest = publish()
    unlink = Path.unlink

    def interrupted(path, *args, **kwargs):
        if path == store.directory / "transition.json":
            message = "injected journal-removal boundary"
            raise OSError(message)
        return unlink(path, *args, **kwargs)

    with store.lock():
        plan = store.prepare(candidate)
        active = store.switch(plan.id)
        with monkeypatch.context() as failure:
            failure.setattr(Path, "unlink", interrupted)
            with pytest.raises(OSError, match="journal-removal"):
                store.complete(plan.id, proof(active, manifest))
        assert store.pending().phase == "verified"
        assert store.recover() == "settled"
        assert store.pending() is None
        assert store.active() == active


@pytest.mark.parametrize("grows", [False, True])
def test_regular_metadata_is_bounded_even_if_it_grows_after_descriptor_stat(tmp_path, monkeypatch, grows):
    path = tmp_path / "metadata.json"
    path.write_bytes(b"x" if grows else b"oversized")
    path.chmod(0o600)
    actual = os.fstat

    def changed(descriptor):
        before = actual(descriptor)
        path.write_bytes(b"oversized")
        return before

    with monkeypatch.context() as race:
        if grows:
            race.setattr(os, "fstat", changed)
        with pytest.raises(HeinzelError):
            _store.regular_bytes(path, limit=2)


@pytest.mark.parametrize("change", ["foreign-file", "orphan-environment", "extra-directory", "missing-file"])
def test_cleanup_refuses_unowned_or_changed_inventory_before_erasing_any_tree(inventory, monkeypatch, change):
    store, publish, _ = inventory
    candidate, runtime, manifest = publish()
    retained = store.directory.parent / "work.txt"
    retained.write_text("keep user work")
    original = _store.atomic_record

    def changed(path, value):
        original(path, value)
        if path.name == "cleanup.json":
            if change == "extra-directory":
                (runtime / "unexpected").mkdir(mode=0o700)
            elif change == "missing-file":
                (runtime / "worker.py").unlink()

    with store.lock():
        plan = store.prepare(candidate)
        active = store.switch(plan.id)
        store.complete(plan.id, proof(active, manifest))
        removal = store.prepare(None)
        store.switch(removal.id)
        store.complete(removal.id)
        if change == "foreign-file":
            (store.directory / "deployments" / "retained.txt").write_text("unknown payload")
        elif change == "orphan-environment":
            (store.directory / "environments" / ("a" * 32)).mkdir(mode=0o700, parents=True)
            (store.directory / "environments").chmod(0o700)
        with monkeypatch.context() as race:
            race.setattr(_store, "atomic_record", changed)
            with pytest.raises(HeinzelError):
                store.purge_uninstalled_runtime()
        assert runtime.is_dir()
        assert retained.read_text() == "keep user work"
        assert not list(runtime.parent.glob(".cleanup-*"))


def test_pointer_rename_before_journal_failure_is_recovered_without_claiming_runtime_success(inventory, monkeypatch):
    store, publish, _ = inventory
    candidate, _, manifest = publish()
    original = _store.atomic_record

    def crash(path, value):
        if path.name == "transition.json" and getattr(value, "phase", None) == "switched":
            message = "injected durable boundary"
            raise OSError(message)
        original(path, value)

    with store.lock():
        plan = store.prepare(candidate)
        with monkeypatch.context() as failure:
            failure.setattr(_store, "atomic_record", crash)
            with pytest.raises(HeinzelError):
                store.switch(plan.id)
    reopened = LifecycleStore(store.directory)
    with reopened.lock():
        assert reopened.recover() == "verification-required"
        assert reopened.pending().phase == "switched"
        with pytest.raises(HeinzelError, match="already pending"):
            reopened.prepare(candidate)
        reopened.complete(plan.id, proof(reopened.active(), manifest))
        assert reopened.recover() == "settled"


def test_live_backend_lease_blocks_switch_and_uninstall(inventory):
    store, publish, _ = inventory
    candidate, _, _ = publish()
    descriptor = os.open(store.directory.parent / "backend.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with store.lock():
            plan = store.prepare(candidate)
            with pytest.raises(HeinzelError, match="Stop the owned"):
                store.switch(plan.id)
            assert store.active() is None
            assert store.pending().phase == "prepared"
    finally:
        os.close(descriptor)


def test_opt_in_runtime_cleanup_removes_only_verified_payload_after_uninstall(inventory):
    store, publish, _ = inventory
    candidate, runtime_directory, manifest = publish()
    data = store.directory.parent / "data"
    data.mkdir()
    retained = data / "important.txt"
    retained.write_text("keep user work")
    with store.lock():
        install = store.prepare(candidate)
        active = store.switch(install.id)
        store.complete(install.id, proof(active, manifest))
        removal = store.prepare(None)
        store.switch(removal.id)
        store.complete(removal.id)
        assert store.purge_uninstalled_runtime() == (1, 0)
    assert not runtime_directory.exists()
    assert retained.read_text() == "keep user work"
    assert store.active() is None
    assert store.pending() is None


def test_runtime_cleanup_refuses_active_or_unverified_payloads(inventory):
    store, publish, _ = inventory
    candidate, runtime_directory, manifest = publish()
    with store.lock():
        install = store.prepare(candidate)
        active = store.switch(install.id)
        store.complete(install.id, proof(active, manifest))
        with pytest.raises(HeinzelError, match="Uninstall"):
            store.purge_uninstalled_runtime()
        removal = store.prepare(None)
        store.switch(removal.id)
        store.complete(removal.id)
        (runtime_directory / "unexpected.txt").write_text("changed ownership boundary")
        with pytest.raises(HeinzelError):
            store.purge_uninstalled_runtime()
    assert runtime_directory.exists()


def test_runtime_cleanup_retries_partial_deletion_from_durable_journal(inventory, monkeypatch):
    store, publish, manifest = inventory
    candidate, _, manifest = publish()
    data = store.directory.parent / "data"
    data.mkdir()
    retained = data / "keep.txt"
    retained.write_text("keep across cleanup recovery")
    with store.lock():
        install = store.prepare(candidate)
        active = store.switch(install.id)
        store.complete(install.id, proof(active, manifest))
        removal = store.prepare(None)
        store.switch(removal.id)
        store.complete(removal.id)

    generation = json.loads((store.directory / "generation.json").read_text())["generation"]

    def partial_delete(path):
        next(file for file in Path(path).rglob("*") if file.is_file()).unlink()
        message = "simulated interruption during recursive deletion"
        raise OSError(message)

    with monkeypatch.context() as failure:
        failure.setattr(_store.shutil, "rmtree", partial_delete)
        with store.lock(), pytest.raises(HeinzelError, match="interrupted safely"):
            store.purge_uninstalled_runtime()

    journal = store.directory / "cleanup.json"
    assert journal.is_file()
    reopened = LifecycleStore(store.directory)
    cleanup = json.loads(journal.read_text())
    staged = store.directory / "deployments" / f".cleanup-{cleanup['id'].replace('-', '')}-0"
    unexpected = staged / "unexpected.txt"
    unexpected.write_text("must not be removed")
    with reopened.lock():
        with pytest.raises(HeinzelError, match="unavailable or changed"):
            reopened.purge_uninstalled_runtime()
        with pytest.raises(HeinzelError, match="cleanup is already pending"):
            reopened.prepare(candidate)
        unexpected.unlink()
        assert reopened.purge_uninstalled_runtime() == (1, 0)
    assert not journal.exists()
    assert retained.read_text() == "keep across cleanup recovery"
    assert json.loads((store.directory / "generation.json").read_text())["generation"] == generation


def test_nested_or_other_thread_attempt_does_not_steal_the_current_authority(inventory):
    store, publish, _ = inventory
    candidate, _, _ = publish()
    with pytest.raises(HeinzelError, match="exclusive"):
        store.prepare(candidate)
    with store.lock():
        with pytest.raises(HeinzelError), store.lock():
            pytest.fail("A nested independent descriptor must not acquire the lifecycle authority")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(store.prepare, candidate)
            with pytest.raises(HeinzelError, match="exclusive"):
                future.result(timeout=3)
        assert store.prepare(candidate).candidate == candidate


@pytest.mark.parametrize("damage", ["changed", "extra", "missing", "symlink", "hardlink", "permissions"])
def test_candidate_damage_is_refused_without_changing_installed_authority(inventory, damage):
    store, publish, _ = inventory
    candidate, directory, _ = publish()
    source = directory / "worker.py"
    if damage == "changed":
        source.write_text("changed")
    elif damage == "extra":
        (directory / "extra.py").write_text("not in inventory")
    elif damage == "missing":
        source.unlink()
    elif damage == "symlink":
        source.unlink()
        source.symlink_to(directory / "runtime.json")
    elif damage == "hardlink":
        os.link(source, directory / "another.py")
    else:
        source.chmod(0o644)
    with store.lock(), pytest.raises(HeinzelError) as refused:
        store.prepare(candidate)
    assert str(store.directory) not in str(refused.value)
    assert store.active() is None
    assert store.pending() is None


@pytest.mark.parametrize("value", [True, 0, 2])
def test_schema_and_generation_use_strict_integer_validation(value):
    with pytest.raises(ValidationError):
        Activation.model_validate_json(
            json.dumps({"schema": value, "generation": 1, "deployment": "a" * 64, "previous": None})
        )
    if value is True or value == 0:
        with pytest.raises(ValidationError):
            Activation.model_validate_json(
                json.dumps({"schema": 1, "generation": value, "deployment": "a" * 64, "previous": None})
            )
