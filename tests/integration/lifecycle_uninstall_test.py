"""The unleased private authority deactivates only the expected runtime and preserves profile work."""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle import _store, claim_command
from heinzel.modules.system.lifecycle._models import EnvironmentManifest, RuntimeManifest, RuntimeProof
from heinzel.modules.system.lifecycle._store import LifecycleStore, atomic_record
from heinzel.modules.system.lifecycle._uninstall import uninstall_runtime

pytestmark = pytest.mark.integration


@pytest.fixture
def installed(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    data = profile / "data.json"
    data.write_text('{"draft":"keep"}')
    store = LifecycleStore(profile / "lifecycle")
    with store.lock():
        deployments = store.directory / "deployments"
        deployments.mkdir(mode=0o700)
        environments = store.directory / "environments"
        environments.mkdir(mode=0o700)
        artifacts = []
        for version in ("0.7.12", "0.7.13"):
            environment = environments / uuid4().hex
            environment.mkdir(mode=0o700)
            (environment / "bin").mkdir(mode=0o700)
            python = environment / "bin" / "python"
            python.write_bytes(version.encode())
            python.chmod(0o700)
            digest = hashlib.sha256(python.read_bytes()).hexdigest()
            inventory = EnvironmentManifest(
                directory=str(environment),
                requested={"heinzel": f"heinzel=={version}"},
                versions={"heinzel": version},
                wheel_sha256={"heinzel": "a" * 64},
                files={"bin/python": digest},
            )
            environment_bytes = inventory.model_dump_json().encode() + b"\n"
            manifest = RuntimeManifest(
                core_version=version,
                python=str(python),
                python_sha256=digest,
                ui_sha256="a" * 64,
                files={"worker.py": digest, "environment.json": hashlib.sha256(environment_bytes).hexdigest()},
            )
            encoded = manifest.model_dump_json().encode() + b"\n"
            identity = hashlib.sha256(encoded).hexdigest()
            deployment = deployments / identity
            deployment.mkdir(mode=0o700)
            (deployment / "worker.py").write_bytes(version.encode())
            (deployment / "worker.py").chmod(0o600)
            (deployment / "environment.json").write_bytes(environment_bytes)
            (deployment / "environment.json").chmod(0o600)
            atomic_record(deployment / "runtime.json", manifest)
            artifacts.append((identity, deployment, environment))
        plan = store.prepare(artifacts[0][0])
        active = store.switch(plan.id)
        store.complete(
            plan.id,
            RuntimeProof(
                deployment=active.deployment,
                generation=active.generation,
                instance="b" * 32,
                version="0.7.12",
                ui_sha256="a" * 64,
            ),
        )
    return store, active, artifacts, data


def invoke(store, active, **options):
    result = subprocess.run(
        [sys.executable, "-m", "heinzel._lifecycle_control"],
        input=json.dumps(
            {
                "directory": str(store.directory),
                "action": "uninstall",
                "expected": active.model_dump(mode="json"),
                **options,
            }
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_default_uninstall_retains_runtime_payloads_and_profile_work(installed):
    store, active, artifacts, data = installed
    answer = invoke(store, active)
    assert answer["ok"] is True
    assert answer["value"]["state"] == "uninstalled"
    assert answer["value"]["payloads"] == "retained"
    assert answer["value"]["data_retained"] is True
    assert store.active() is None
    assert store.pending() is None
    assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
    assert json.loads(data.read_text()) == {"draft": "keep"}
    assert json.loads((store.directory / "generation.json").read_text())["generation"] > active.generation


def test_explicit_payload_removal_preserves_profile_data_and_generation_fence(installed):
    store, active, artifacts, data = installed
    answer = invoke(store, active, remove_payloads=True)
    assert answer["ok"] is True
    assert answer["value"]["state"] == "uninstalled"
    assert answer["value"]["payloads"] == "removed"
    assert answer["value"]["deployments_removed"] == 2
    assert answer["value"]["environments_removed"] == 2
    assert all(not deployment.exists() and not environment.exists() for _, deployment, environment in artifacts)
    assert json.loads(data.read_text()) == {"draft": "keep"}
    assert (store.directory / "generation.json").exists()
    assert not (store.directory / "cleanup.json").exists()


@pytest.mark.parametrize("change", [{"generation": 99}, {"deployment": "b" * 64}, {"previous": "c" * 64}])
def test_stale_expected_generation_is_refused_without_preparing_or_deleting(installed, change):
    store, active, artifacts, data = installed
    stale = active.model_copy(update=change)
    assert invoke(store, stale, remove_payloads=True)["ok"] is False
    assert store.active() == active
    assert store.pending() is None
    assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
    assert data.exists()


def test_live_writer_blocks_removal_and_retry_advances_the_generation(installed):
    store, active, artifacts, data = installed
    with claim_command(store).lease:
        first = invoke(store, active, remove_payloads=True)
        assert first["ok"] is True
        assert first["value"]["state"] == "blocked"
        assert store.active() == active
        assert store.pending() is None
        assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
    second = invoke(store, active)
    assert second["value"]["state"] == "uninstalled"
    assert second["value"]["generation"] > first["value"]["generation"]
    assert json.loads(data.read_text()) == {"draft": "keep"}


def test_live_backend_is_not_terminated_or_removed(installed):
    store, active, artifacts, data = installed
    descriptor = os.open(store.directory.parent / "backend.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        answer = invoke(store, active, remove_payloads=True)
        assert answer["value"]["state"] == "blocked"
        assert store.active() == active
        assert store.pending() is None
        assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
        assert data.exists()
    finally:
        os.close(descriptor)


def test_uninstall_never_cancels_another_pending_plan(installed):
    store, active, artifacts, _data = installed
    with store.lock():
        pending = store.prepare(artifacts[1][0])
    assert invoke(store, active, remove_payloads=True)["ok"] is False
    assert store.active() == active
    assert store.pending().id == pending.id
    assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)


def test_modified_payload_is_retained_and_cleanup_is_not_reported_as_complete(installed):
    store, active, artifacts, data = installed
    changed = artifacts[1][1] / "worker.py"
    changed.write_bytes(b"changed owned payload")
    answer = invoke(store, active, remove_payloads=True)
    assert answer["value"]["state"] == "cleanup-required"
    assert answer["value"]["payloads"] == "cleanup-required"
    assert answer["value"]["deployments_removed"] is None
    assert store.active() is None
    assert changed.read_bytes() == b"changed owned payload"
    assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
    assert json.loads(data.read_text()) == {"draft": "keep"}


def test_pointer_change_before_journal_failure_requires_recovery_without_deleting_payloads(installed, monkeypatch):
    store, active, artifacts, data = installed
    original = _store.atomic_record

    def interrupted(path, value):
        if path.name == "transition.json" and getattr(value, "phase", None) == "switched":
            message = "injected journal boundary"
            raise OSError(message)
        return original(path, value)

    with store.lock():
        with monkeypatch.context() as failure:
            failure.setattr(_store, "atomic_record", interrupted)
            answer = uninstall_runtime(store, active, remove_payloads=True)
        assert answer.state == "recovery-required"
        assert store.active() is None
        assert store.pending() is not None
        assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
        assert store.recover() == "verification-required"
        store.complete(answer.transition)
    assert json.loads(data.read_text()) == {"draft": "keep"}


def test_interrupted_payload_cleanup_reports_partial_state_and_resumes_from_the_existing_journal(
    installed, monkeypatch
):
    store, active, artifacts, data = installed

    def interrupted(_path):
        message = "injected cleanup boundary"
        raise OSError(message)

    with store.lock():
        with monkeypatch.context() as failure:
            failure.setattr(_store.shutil, "rmtree", interrupted)
            answer = uninstall_runtime(store, active, remove_payloads=True)
        assert answer.state == "cleanup-required"
        assert answer.payloads == "cleanup-required"
        assert store.active() is None
        assert store.pending() is None
        assert (store.directory / "cleanup.json").exists()
    result = subprocess.run(
        [sys.executable, "-m", "heinzel._lifecycle_control"],
        input=json.dumps({"directory": str(store.directory), "action": "cleanup"}),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "ok": True,
        "value": {"deployments_removed": 2, "environments_removed": 2, "data_retained": True},
    }
    assert all(not deployment.exists() and not environment.exists() for _, deployment, environment in artifacts)
    assert json.loads(data.read_text()) == {"draft": "keep"}


def test_domain_uninstall_cannot_mutate_without_the_shared_authority_lock(installed):
    store, active, artifacts, _data = installed
    with pytest.raises(HeinzelError, match="exclusive"):
        uninstall_runtime(store, active, remove_payloads=True)
    assert store.active() == active
    assert store.pending() is None
    assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)


def test_payload_cleanup_cannot_remove_an_active_runtime(installed):
    store, active, artifacts, data = installed
    result = subprocess.run(
        [sys.executable, "-m", "heinzel._lifecycle_control"],
        input=json.dumps({"directory": str(store.directory), "action": "cleanup"}),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is False
    assert store.active() == active
    assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
    assert data.exists()


def test_cleanup_validation_failure_after_deactivation_is_reported_as_cleanup_required(installed, monkeypatch):
    store, active, artifacts, data = installed

    def invalid_inventory():
        message = "invalid cleanup inventory"
        raise ValueError(message)

    monkeypatch.setattr(store, "purge_uninstalled_runtime", invalid_inventory)
    with store.lock():
        answer = uninstall_runtime(store, active, remove_payloads=True)
    assert answer.state == "cleanup-required"
    assert answer.payloads == "cleanup-required"
    assert store.active() is None
    assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
    assert data.exists()


def test_failed_drain_cancellation_stays_recoverable_and_never_claims_a_completed_rollback(installed, monkeypatch):
    store, active, artifacts, data = installed

    def interrupted(_identifier):
        message = "injected cancellation boundary"
        raise OSError(message)

    with claim_command(store).lease, store.lock():
        with monkeypatch.context() as failure:
            failure.setattr(store, "cancel", interrupted)
            answer = uninstall_runtime(store, active, remove_payloads=True)
        assert answer.state == "recovery-required"
        assert store.active() == active
        assert store.pending().phase == "prepared"
        store.cancel(answer.transition)
    assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
    assert data.exists()


def test_prepared_uninstall_finishes_the_native_coordinators_same_exact_plan(installed):
    store, active, artifacts, data = installed
    with store.lock():
        prepared = store.prepare(None)
    answer = invoke(store, active, id=str(prepared.id), remove_payloads=True)
    assert answer["ok"] is True
    assert answer["value"]["state"] == "uninstalled"
    assert answer["value"]["transition"] == str(prepared.id)
    assert answer["value"]["generation"] == prepared.generation
    assert store.active() is None
    assert store.pending() is None
    assert all(not deployment.exists() and not environment.exists() for _, deployment, environment in artifacts)
    assert data.exists()


@pytest.mark.parametrize("other_action", [False, True])
def test_stale_or_different_action_plan_is_never_applied_or_cancelled(installed, other_action):
    store, active, artifacts, _data = installed
    with store.lock():
        plan = store.prepare(artifacts[1][0] if other_action else None)
    selected = plan.id if other_action else uuid4()
    assert invoke(store, active, id=str(selected), remove_payloads=True)["ok"] is False
    assert store.active() == active
    assert store.pending().id == plan.id
    assert all(deployment.exists() and environment.exists() for _, deployment, environment in artifacts)
