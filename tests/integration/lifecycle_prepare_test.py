"""Managed lifecycle confirmation names the exact activation before durable intent is created."""

import contextlib
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._authority import Request, execute
from heinzel.modules.system.lifecycle._models import Activation, RuntimeManifest, RuntimeProof
from heinzel.modules.system.lifecycle._store import LifecycleStore, atomic_record

pytestmark = pytest.mark.integration


@pytest.fixture
def staged(tmp_path):
    store = LifecycleStore(tmp_path / "lifecycle")
    with store.lock():
        deployments = store.directory / "deployments"
        deployments.mkdir(mode=0o700)
        candidates = []
        # This suite verifies lifecycle metadata; it never executes this candidate runtime. Keep its
        # executable-shaped fixture independent from the CI interpreter, which may be linked or public.
        python = tmp_path / "candidate-python"
        python.write_bytes(b"fixture runtime executable")
        python.chmod(0o700)
        for version in ("0.7.12", "0.7.13"):
            worker = version.encode()
            manifest = RuntimeManifest(
                core_version=version,
                python=str(python),
                python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
                ui_sha256="a" * 64,
                files={"worker.py": hashlib.sha256(worker).hexdigest()},
            )
            encoded = manifest.model_dump_json().encode() + b"\n"
            identity = hashlib.sha256(encoded).hexdigest()
            directory = deployments / identity
            directory.mkdir(mode=0o700)
            path = directory / "worker.py"
            path.write_bytes(worker)
            path.chmod(0o600)
            atomic_record(directory / "runtime.json", manifest)
            candidates.append(identity)
    data = tmp_path / "work.json"
    data.write_text('{"draft":"keep"}')
    return store, candidates, data


def activate(store, candidate):
    with store.lock():
        plan = store.prepare(candidate)
        active = store.switch(plan.id)
        store.complete(
            plan.id,
            RuntimeProof(
                deployment=candidate,
                generation=active.generation,
                instance="b" * 32,
                version=store.verify(candidate)[1].core_version,
                ui_sha256="a" * 64,
            ),
        )
        return active


def invoke(store, candidate, expected):
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "heinzel._lifecycle_control"],
        input=json.dumps(
            {
                "directory": str(store.directory),
                "action": "prepare",
                "candidate": candidate,
                "expected": expected,
                "first_install": expected is None,
            }
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_first_install_requires_and_retains_explicit_absent_activation(staged):
    store, candidates, data = staged
    result = invoke(store, candidates[0], None)
    assert result["ok"] is True
    plan = store.pending()
    assert plan.before is None
    assert plan.candidate == candidates[0]
    assert plan.action == "install"
    assert result["value"] == plan.model_dump(mode="json")
    assert store.active() is None
    assert json.loads(data.read_text()) == {"draft": "keep"}


def test_update_receipt_is_bound_to_selected_activation_and_target(staged):
    store, candidates, data = staged
    before = activate(store, candidates[0])
    result = invoke(store, candidates[1], before.model_dump(mode="json"))
    assert result["ok"] is True
    plan = store.pending()
    assert plan.before == before
    assert plan.candidate == candidates[1]
    assert plan.action == "update"
    assert plan.generation > before.generation
    assert result["value"] == plan.model_dump(mode="json")
    assert store.active() == before
    assert json.loads(data.read_text()) == {"draft": "keep"}


@pytest.mark.parametrize("change", [{"generation": 99}, {"deployment": "b" * 64}, {"previous": "c" * 64}])
def test_changed_activation_tuple_is_refused_before_any_plan_or_generation_write(staged, change):
    store, candidates, data = staged
    before = activate(store, candidates[0])
    high_water = (store.directory / "generation.json").read_bytes()
    expected = before.model_copy(update=change).model_dump(mode="json")
    assert invoke(store, candidates[1], expected)["ok"] is False
    assert store.active() == before
    assert store.pending() is None
    assert (store.directory / "generation.json").read_bytes() == high_water
    assert all(store.verify(candidate) for candidate in candidates)
    assert json.loads(data.read_text()) == {"draft": "keep"}


def test_absent_install_expectation_cannot_overwrite_a_concurrent_install(staged):
    store, candidates, _data = staged
    before = activate(store, candidates[0])
    assert invoke(store, candidates[1], None)["ok"] is False
    assert store.active() == before
    assert store.pending() is None


def test_same_deployment_at_a_new_generation_cannot_reuse_old_approval(staged):
    store, candidates, _data = staged
    approved = activate(store, candidates[0])
    current = activate(store, candidates[0])
    assert current.deployment == approved.deployment
    assert current.generation > approved.generation
    assert invoke(store, candidates[1], approved.model_dump(mode="json"))["ok"] is False
    assert store.active() == current
    assert store.pending() is None


def test_lifecycle_owner_lock_blocks_preparation_without_creating_intent(staged):
    store, candidates, _data = staged
    with store.lock():
        assert invoke(store, candidates[0], None)["ok"] is False
        assert store.pending() is None
    assert invoke(store, candidates[0], None)["ok"] is True


def test_bound_plan_roundtrip_keeps_recovery_cancellation_and_uninstall_on_one_authority(staged):
    store, candidates, data = staged

    def control(action, **fields):
        return execute(Request.model_validate({"directory": str(store.directory), "action": action, **fields}))

    planned = control("prepare", candidate=candidates[0], expected=None)
    active = control("switch", id=planned["id"])
    assert control("recover") == "verification-required"
    assert (
        control(
            "complete",
            id=planned["id"],
            proof={
                "deployment": candidates[0],
                "generation": active["generation"],
                "instance": "b" * 32,
                "version": "0.7.12",
                "ui_sha256": "a" * 64,
            },
        )
        is None
    )
    assert control("recover") == "settled"
    pending = control("prepare", candidate=candidates[1], expected=active)
    assert control("cancel", id=pending["id"]) is None
    assert store.active().model_dump(mode="json") == active
    assert store.pending() is None
    removed = control("uninstall", expected=active)
    assert removed["state"] == "uninstalled"
    assert removed["payloads"] == "retained"
    assert store.active() is None
    assert store.pending() is None
    assert control("cleanup") == {"deployments_removed": 2, "environments_removed": 0, "data_retained": True}
    assert all(not (store.directory / "deployments" / candidate).exists() for candidate in candidates)
    assert Path(sys.executable).resolve().is_file()
    assert json.loads(data.read_text()) == {"draft": "keep"}


def test_expected_activation_is_checked_after_acquiring_the_real_lifecycle_lock(staged, monkeypatch):
    store, candidates, data = staged
    approved = activate(store, candidates[0])
    original = LifecycleStore.lock
    replacement = Activation(generation=approved.generation + 1, deployment=candidates[1], previous=candidates[0])

    @contextlib.contextmanager
    def interleaved(instance):
        with original(instance):
            atomic_record(instance.directory / "active.json", replacement)
            yield

    monkeypatch.setattr(LifecycleStore, "lock", interleaved)
    request = Request(directory=str(store.directory), action="prepare", candidate=candidates[0], expected=approved)
    with pytest.raises(HeinzelError, match="selected runtime changed"):
        execute(request)
    assert store.active() == replacement
    assert store.pending() is None
    assert json.loads(data.read_text()) == {"draft": "keep"}
