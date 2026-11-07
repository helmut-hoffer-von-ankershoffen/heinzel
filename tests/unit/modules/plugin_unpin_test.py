"""The explicit plugin unpin changes only the confirmed tool requirement; no package is run or replaced."""

import subprocess
import tomllib
import types
from contextlib import contextmanager

import pytest
import tomlkit

from heinzel import HeinzelError, Progress
from heinzel.kernel import ENTRY_POINT_GROUP, Installation, _installation_continuity
from heinzel.modules.system.plugin._models import PluginSettings
from heinzel.modules.system.plugin._service import PluginService
from tests.unit.modules.plugin_service_test import isolated_python, tool_env

__all__ = ["isolated_python", "tool_env"]
pytestmark = pytest.mark.unit
NAME = "heinzel-old"
OTHER = "heinzel-sample"


@pytest.fixture
def pinned(make_kernel, tool_env, monkeypatch):
    installation = Installation(Progress(), prefix=tool_env, uv="never-run")
    receipt = tool_env / "uv-receipt.toml"
    document = tomlkit.parse(receipt.read_text())
    document["tool"]["requirements"] = [
        {"name": "heinzel"},
        {"name": NAME, "specifier": "==1.0.0"},
        {"name": OTHER, "specifier": "==2.0.0"},
    ]
    receipt.write_text(tomlkit.dumps(document))
    packages = tool_env / "lib/python3.12/site-packages"
    for name, version in ((NAME, "1.0.0"), (OTHER, "2.0.0")):
        metadata = packages / f"{name}-{version}.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        (metadata / "entry_points.txt").write_text(f"[{ENTRY_POINT_GROUP}]\n{name} = never_imported:module\n")
    payload = packages / "never_imported.py"
    payload.write_text("raise RuntimeError('plugin code must not execute during unpin')\n")
    installation.record_startup_state(expected=installation.startup_fingerprint())
    lifecycle = []

    @contextmanager
    def mutation(checkpoint, _identity):
        lifecycle.append("enter")
        yield types.SimpleNamespace(
            checkpoint_matched=checkpoint(),
            capture=object,
            verified_complete=lifecycle.append,
            request=lambda plan: lifecycle.append("handover"),
            abort_unchanged=lambda: lifecycle.append("abort"),
        )

    service = PluginService(
        make_kernel(), installation, PluginSettings(), lifecycle=types.SimpleNamespace(mutation=mutation)
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Unpin must not call uv, run an interpreter, or ask for a new artifact")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(service, "pypi", forbidden)
    monkeypatch.setattr(service, "github_api", forbidden)
    return service, installation, receipt, payload, lifecycle


def test_unpin_preview_and_confirmation_keep_installed_files_and_other_policy_exact(pinned, monkeypatch):
    service, installation, receipt, payload, lifecycle = pinned
    checkpoints = []
    record = installation.record_startup_state

    def record_checkpoint(*, expected):
        checkpoints.append(expected)
        record(expected=expected)

    monkeypatch.setattr(installation, "record_startup_state", record_checkpoint)
    before = receipt.read_bytes()
    package = payload.read_bytes(), payload.stat()
    preview = service.unpin(NAME)
    assert (preview.distribution, preview.version, preview.done) == (NAME, "1.0.0", False)
    assert preview.next == [f"hz system plugin unpin {NAME} --confirm {preview.confirmation}"]
    assert receipt.read_bytes() == before
    assert not lifecycle
    assert not service.history()
    result = service.unpin(NAME, confirm=preview.confirmation)
    assert result.done
    assert result.version == "1.0.0"
    expected = tomllib.loads(before.decode())
    del expected["tool"]["requirements"][1]["specifier"]
    assert tomllib.loads(receipt.read_text()) == expected
    assert (payload.read_bytes(), payload.stat()) == package
    assert installation.requirements() == ["heinzel", NAME, OTHER + "==2.0.0"]
    assert installation.startup_state_matches()
    assert checkpoints == [installation.startup_fingerprint()]
    assert lifecycle == ["enter", installation.startup_fingerprint(), "handover"]
    assert [(event.action, event.distribution) for event in service.history()] == [("unpin", NAME)]
    with pytest.raises(HeinzelError, match="exact index pin"):
        service.unpin(NAME, confirm=preview.confirmation)


def test_unpin_confirmation_cannot_follow_a_new_verified_generation(pinned):
    service, installation, receipt, payload, lifecycle = pinned
    preview = service.unpin(NAME)
    before = receipt.read_bytes()
    payload.write_text("different installation, same pin and version")
    installation.record_startup_state(expected=installation.startup_fingerprint())
    with pytest.raises(HeinzelError, match="changed; preview"):
        service.unpin(NAME, confirm=preview.confirmation)
    assert receipt.read_bytes() == before
    assert not lifecycle
    assert not service.history()


def test_unpin_refuses_checkpoint_restored_after_mutation_admission(pinned, monkeypatch):
    service, installation, receipt, _, lifecycle = pinned
    preview = service.unpin(NAME)
    before = receipt.read_bytes()
    checkpoint_path = installation.prefix / ".heinzel-startup-state"
    held_checkpoint = checkpoint_path.with_name("held-startup-state")
    mutation = service._lifecycle.mutation

    @contextmanager
    def restore_after_admission(checkpoint, identity):
        checkpoint_path.rename(held_checkpoint)
        with mutation(checkpoint, identity) as scope:
            assert scope.checkpoint_matched is False
            held_checkpoint.rename(checkpoint_path)
            assert checkpoint()
            yield scope

    monkeypatch.setattr(service._lifecycle, "mutation", restore_after_admission)
    with pytest.raises(HeinzelError, match="startup checkpoint did not match at admission"):
        service.unpin(NAME, confirm=preview.confirmation)
    assert receipt.read_bytes() == before
    assert installation.startup_state_matches()
    assert lifecycle == ["enter", "abort"]
    assert not service.history()


@pytest.mark.parametrize(
    "defect",
    [
        "range",
        "wildcard",
        "extra-in-specifier",
        "git",
        "path",
        "constraint",
        "duplicates",
        "metadata",
        "missing-plugin",
        "unowned",
    ],
)
def test_unpin_refuses_unsupported_or_unverified_requirements_without_writes(pinned, defect):
    service, installation, receipt, _, lifecycle = pinned
    document = tomlkit.parse(receipt.read_text())
    entry = document["tool"]["requirements"][1]
    specifiers = {"range": ">=1.0.0", "wildcard": "==1.*", "extra-in-specifier": "[feature]==1.0.0"}
    if defect in specifiers:
        entry["specifier"] = specifiers[defect]
    elif defect in {"git", "path"}:
        del entry["specifier"]
        entry["git" if defect == "git" else "path"] = (
            "https://example.test/repo" if defect == "git" else str(installation.prefix / "other-plugin")
        )
    elif defect == "constraint":
        document["tool"]["constraints"] = [{"name": NAME, "specifier": "==1.0.0"}]
    elif defect == "duplicates":
        document["tool"]["requirements"].append({"name": NAME, "specifier": "==1.0.0"})
    elif defect == "metadata":
        entry["specifier"] = "==8.0.0"
    elif defect == "missing-plugin":
        metadata = installation.prefix / f"lib/python3.12/site-packages/{NAME}-1.0.0.dist-info/entry_points.txt"
        metadata.unlink()
    receipt.write_text(tomlkit.dumps(document))
    if defect != "unowned":
        installation.record_startup_state(expected=installation.startup_fingerprint())
    else:
        (installation.prefix / ".heinzel-startup-state").unlink()
    before = receipt.read_bytes()
    with pytest.raises(HeinzelError):
        service.unpin(NAME)
    assert receipt.read_bytes() == before
    assert not lifecycle
    assert not service.history()


def test_interrupted_receipt_replace_preserves_packages_and_retains_recovery_evidence(pinned, monkeypatch):
    service, installation, receipt, payload, lifecycle = pinned
    preview = service.unpin(NAME)
    before, package = receipt.read_bytes(), payload.read_bytes()
    original = _installation_continuity.os.replace

    def interrupted(source, destination, **kwargs):
        if source.startswith(".heinzel-receipt-"):
            raise InterruptedError
        original(source, destination, **kwargs)

    monkeypatch.setattr(_installation_continuity.os, "replace", interrupted)
    with pytest.raises(InterruptedError):
        service.unpin(NAME, confirm=preview.confirmation)
    assert receipt.read_bytes() == before
    assert payload.read_bytes() == package
    assert (installation.prefix / ".heinzel-startup-pending").exists()
    assert not installation.startup_state_matches()
    assert lifecycle == ["enter", "abort"]
    assert not service.history()
