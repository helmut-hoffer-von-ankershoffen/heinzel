"""Plugin mutations use the daemon fence and verify host metadata before target execution."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import tomlkit

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation
from heinzel.modules.system.daemon import _installation_lifecycle
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService
from heinzel.modules.system.plugin._models import PluginCandidate, PluginChange, PluginSettings
from heinzel.modules.system.plugin._service import PluginService

pytestmark = pytest.mark.unit


def _append_guard(receipt):
    document = tomlkit.parse(receipt.read_text())
    tool = document["tool"]
    if "constraints" not in tool:
        tool["constraints"] = []
    tool["constraints"].append({"name": "heinzel", "specifier": "==0.9.6"})
    receipt.write_text(tomlkit.dumps(document))


@pytest.fixture
def guarded(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel()
    prefix = tmp_path / "tools/heinzel"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/hz").write_text("fixture")
    base = tmp_path / "fixture-python"
    assert not base.exists()
    assert not base.is_symlink()
    base.write_text("fixture")
    assert base.resolve().is_relative_to(tmp_path.resolve())
    assert not base.samefile(sys.executable)
    assert not base.samefile(sys._base_executable)
    base.chmod(0o700)
    (prefix / "bin/python").symlink_to(base)
    binary = tmp_path / "commands"
    binary.mkdir()
    (binary / "hz").symlink_to(prefix / "bin/hz")
    receipt = prefix / "uv-receipt.toml"
    receipt.write_text(f"""[tool]
requirements = [{{name="heinzel"}}]
entrypoints = [{{name="hz", install-path={str(binary / "hz")!r}}}]
""")
    site = prefix / "lib/python3.14/site-packages"
    core = site / "heinzel-0.9.6.dist-info"
    core.mkdir(parents=True)
    (core / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.6\n")
    events = []
    wrong = []

    def fake_uv(command, environment):
        events.append("uv")
        assert environment["UV_TOOL_DIR"] == str(prefix.parent)
        assert environment["UV_TOOL_BIN_DIR"] == str(binary)
        constraint_file = Path(command[command.index("--constraints") + 1])
        assert constraint_file.read_text().endswith("heinzel==0.9.6\n")
        _append_guard(receipt)
        metadata = site / "heinzel_demo-1.0.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-demo\nVersion: 1.0.0\n")
        (metadata / "entry_points.txt").write_text("[heinzel.modules]\ndemo = untrusted\n")
        if wrong:
            (metadata / "direct_url.json").write_text(json.dumps({"url": "https://example.test/wrong"}))
        return subprocess.CompletedProcess(command, 0, "", "")

    installation = Installation(Progress(), prefix=prefix, uv="fake-uv", runner=fake_uv)
    daemon = DaemonService(
        kernel,
        kernel.settings,
        DaemonSettings(unit_dir=tmp_path / "units"),
        platform="darwin",
        continuity_fingerprint=installation.startup_fingerprint,
        runner=lambda *_: pytest.fail("manager called"),
    )
    service = PluginService(kernel, installation, PluginSettings(), lifecycle=_installation_lifecycle(daemon))
    candidate = PluginCandidate(
        spec="heinzel-demo",
        requirement="heinzel-demo==1.0.0",
        distribution="heinzel-demo",
        version="1.0.0",
        summary="",
        source="pypi",
        url="https://example.test",
        publisher=None,
        first_party=False,
        private=None,
        revision=None,
        installed_version=None,
        warnings=[],
    )
    monkeypatch.setattr(service, "candidate", lambda _: candidate)
    monkeypatch.setattr(service, "_git_environment", dict)

    def changed(action, distribution, requested):
        events.append("target-probe")
        assert daemon._handover_pending()
        return PluginChange(
            action=action,
            done=True,
            distribution=distribution,
            candidate=requested,
            plugins=[],
            restart_required=True,
            next=[],
        )

    monkeypatch.setattr(service, "_changed", changed)
    return service, installation, daemon, events, wrong


@pytest.mark.parametrize("checkpoint", [False, True])
def test_plugin_add_verifies_before_probe_and_preserves_checkpoint_policy(guarded, checkpoint):
    service, installation, daemon, events, _wrong = guarded
    if checkpoint:
        installation.record_startup_state(expected=installation.startup_fingerprint())
    result = service.add("heinzel-demo", confirm=True)
    assert result.done
    assert events == ["uv", "target-probe"]
    assert not daemon._handover_pending()
    assert installation.startup_state_matches() is checkpoint


def test_wrong_plugin_source_keeps_pending_without_target_probe(guarded):
    service, installation, daemon, events, wrong = guarded
    wrong.append(True)
    with pytest.raises(HeinzelError, match="metadata does not match"):
        service.add("heinzel-demo", confirm=True)
    assert events == ["uv"]
    assert daemon._handover_pending()
    assert not installation.startup_state_matches()


def test_missing_lifecycle_denies_tool_change_before_uv(guarded):
    service, _installation, _daemon, events, _wrong = guarded
    service._lifecycle = None
    with pytest.raises(HeinzelError, match="lifecycle boundary is unavailable"):
        service.add("heinzel-demo", confirm=True)
    assert events == []


def test_running_tick_fences_plugin_install_before_uv(guarded):
    from heinzel.modules.system.daemon._runtime import lock

    service, _installation, daemon, events, _wrong = guarded
    with lock(daemon.state_dir / "lifecycle.lock") as acquired:
        assert acquired
        with pytest.raises(HeinzelError, match="lifecycle"):
            service.add("heinzel-demo", confirm=True)
    assert events == []


def test_plugin_install_fences_tick_and_keeps_pause_state(guarded):
    service, installation, daemon, events, _wrong = guarded
    daemon.pause()
    real_runner = installation._runner

    def during_uv(command, environment):
        report = daemon.tick()
        assert report.locked
        assert not report.runs
        return real_runner(command, environment)

    installation._runner = during_uv
    service.add("heinzel-demo", confirm=True)
    assert events == ["uv", "target-probe"]
    assert daemon.paused


def test_unsupported_receipt_policy_denies_before_uv_and_clears_unchanged_pending(guarded):
    service, installation, daemon, events, _wrong = guarded
    receipt = installation.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text() + 'overrides = [{name="other", path="/fixture/other.whl"}]\n')
    with pytest.raises(HeinzelError, match="constraints changed unexpectedly"):
        service.add("heinzel-demo", confirm=True)
    assert events == []
    assert not daemon._handover_pending()


def test_failed_history_keeps_pending_and_does_not_refresh_old_checkpoint(guarded, monkeypatch):
    service, installation, daemon, events, _wrong = guarded
    installation.record_startup_state(expected=installation.startup_fingerprint())
    record = installation.prefix / ".heinzel-startup-state"
    previous = record.read_bytes()

    def fail(*_args, **_kwargs):
        msg = "fixture history write failed"
        raise OSError(msg)

    monkeypatch.setattr(service, "_record", fail)
    with pytest.raises(OSError, match="fixture history write failed"):
        service.add("heinzel-demo", confirm=True)
    assert events == ["uv", "target-probe"]
    assert daemon._handover_pending()
    assert record.read_bytes() == previous
    assert not installation.startup_state_matches()


def test_plugin_remove_verifies_absence_and_refreshes_prior_checkpoint(guarded, monkeypatch):
    import shutil

    service, installation, daemon, events, _wrong = guarded
    receipt = installation.prefix / "uv-receipt.toml"
    receipt.write_text(
        receipt.read_text().replace(
            'requirements = [{name="heinzel"}]', 'requirements = [{name="heinzel"}, {name="heinzel-demo"}]'
        )
    )
    metadata = installation.prefix / "lib/python3.14/site-packages/heinzel_demo-1.0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-demo\nVersion: 1.0.0\n")
    (metadata / "entry_points.txt").write_text("[heinzel.modules]\ndemo = untrusted\n")
    installation.record_startup_state(expected=installation.startup_fingerprint())
    monkeypatch.setattr(service, "_known", lambda _: "heinzel-demo")
    monkeypatch.setattr(service, "installed", lambda **_kwargs: [])

    def fake_remove(command, environment):
        assert daemon._handover_pending()
        events.append("uv")
        shutil.rmtree(metadata)
        receipt.write_text(receipt.read_text().replace(', {name="heinzel-demo"}', ""))
        _append_guard(receipt)
        return subprocess.CompletedProcess(command, 0, "", "")

    installation._runner = fake_remove
    result = service.remove("heinzel-demo")
    assert result.done
    assert events == ["uv", "target-probe"]
    assert installation.startup_state_matches()
    assert not daemon._handover_pending()


def test_plugin_remove_refuses_still_present_distribution_before_probe(guarded, monkeypatch):
    service, installation, daemon, events, _wrong = guarded
    receipt = installation.prefix / "uv-receipt.toml"
    receipt.write_text(
        receipt.read_text().replace(
            'requirements = [{name="heinzel"}]', 'requirements = [{name="heinzel"}, {name="heinzel-demo"}]'
        )
    )
    metadata = installation.prefix / "lib/python3.14/site-packages/heinzel_demo-1.0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-demo\nVersion: 1.0.0\n")
    monkeypatch.setattr(service, "_known", lambda _: "heinzel-demo")
    monkeypatch.setattr(service, "installed", lambda **_kwargs: [])

    def fake_remove(command, environment):
        events.append("uv")
        (installation.prefix / "bin/hz").write_text("different fixture generation")
        return subprocess.CompletedProcess(command, 0, "", "")

    installation._runner = fake_remove
    with pytest.raises(HeinzelError, match="metadata does not match"):
        service.remove("heinzel-demo")
    assert events == ["uv"]
    assert daemon._handover_pending()


def test_lost_receipt_final_ack_is_unknown_without_replaying_verified_change(guarded, monkeypatch):
    service, installation, daemon, events, _wrong = guarded
    installation.record_startup_state(expected=installation.startup_fingerprint())
    preview = service.add("heinzel-demo", confirm=False)
    operation_id = preview.operation_id
    assert operation_id

    def failed_ack(_operation_id, _result):
        assert not daemon._handover_pending()
        assert installation.startup_state_matches()
        msg = "fixture receipt acknowledgement lost"
        raise OSError(msg)

    monkeypatch.setattr(service._receipts, "complete", failed_ack)
    with pytest.raises(OSError, match="acknowledgement lost"):
        service.add("heinzel-demo", confirm=True, operation_id=operation_id)
    assert events == ["uv", "target-probe"]
    assert service.operation(operation_id).status == "unknown"
    assert installation.startup_state_matches()
    assert not daemon._handover_pending()
    with pytest.raises(HeinzelError, match="unknown result"):
        service.add("heinzel-demo", confirm=True, operation_id=operation_id)
    assert events == ["uv", "target-probe"]


def test_existing_constraints_and_exact_core_pin_reach_uv_together(guarded):
    service, installation, _daemon, events, _wrong = guarded
    receipt = installation.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text() + 'constraints = [{name="other", specifier="<3"}]\n')
    original = installation._runner

    def checked(command, environment):
        path = Path(command[command.index("--constraints") + 1])
        assert path.read_text() == "other<3\nheinzel==0.9.6\n"
        return original(command, environment)

    installation._runner = checked
    assert service.add("heinzel-demo", confirm=True).done
    assert events == ["uv", "target-probe"]
    assert installation.tool_constraints() == ["other<3"]


def test_unexpected_post_uv_constraints_keep_pending_without_target_probe(guarded):
    service, installation, daemon, events, _wrong = guarded
    original = installation._runner

    def unexpected(command, environment):
        result = original(command, environment)
        receipt = installation.prefix / "uv-receipt.toml"
        document = tomlkit.parse(receipt.read_text())
        document["tool"]["constraints"].append({"name": "other", "specifier": "==1"})
        receipt.write_text(tomlkit.dumps(document))
        return result

    installation._runner = unexpected
    with pytest.raises(HeinzelError, match="constraints changed unexpectedly"):
        service.add("heinzel-demo", confirm=True)
    assert events == ["uv"]
    assert daemon._handover_pending()
    assert not installation.startup_state_matches()


def test_failed_unchanged_uv_preserves_original_policy_and_clears_pending(guarded):
    service, installation, daemon, events, _wrong = guarded
    receipt = installation.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text() + 'constraints = [{name="other", specifier="<3"}]\n')
    before = receipt.read_bytes()

    def fail(command, _environment):
        events.append("uv")
        assert Path(command[command.index("--constraints") + 1]).read_text() == "other<3\nheinzel==0.9.6\n"
        return subprocess.CompletedProcess(command, 1, "", "fixture resolution failed")

    installation._runner = fail
    with pytest.raises(HeinzelError, match="fixture resolution failed"):
        service.add("heinzel-demo", confirm=True)
    assert receipt.read_bytes() == before
    assert events == ["uv"]
    assert not daemon._handover_pending()


def test_failed_uv_restore_with_extra_guard_remains_pending_without_target_probe(guarded, tmp_path):
    service, installation, daemon, events, _wrong = guarded
    executable = installation.prefix / "bin/hz"
    receipt = installation.prefix / "uv-receipt.toml"
    before = receipt.read_bytes()

    def fail_then_restore(command, _environment):
        events.append("uv")
        assert Path(command[command.index("--constraints") + 1]).read_text() == "heinzel==0.9.6\n"
        if len(events) == 1:
            assert executable.resolve().is_relative_to(tmp_path.resolve())
            assert not executable.samefile(sys.executable)
            assert not executable.samefile(sys._base_executable)
            executable.unlink()
            return subprocess.CompletedProcess(command, 1, "", "fixture interrupted")
        assert not executable.exists()
        assert not executable.is_symlink()
        assert executable.parent.resolve().is_relative_to(tmp_path.resolve())
        executable.write_text("fixture")
        _append_guard(receipt)
        return subprocess.CompletedProcess(command, 0, "", "")

    installation._runner = fail_then_restore
    with pytest.raises(HeinzelError, match="previous tool may not start"):
        service.add("heinzel-demo", confirm=True)
    assert events == ["uv", "uv"]
    assert receipt.read_bytes() != before
    assert installation.tool_constraints() == ["heinzel==0.9.6"]
    assert daemon._handover_pending()
    assert not installation.startup_state_matches()


def test_unexpected_core_version_denies_before_target_probe(guarded):
    service, installation, daemon, events, _wrong = guarded
    original = installation._runner

    def changed_core(command, environment):
        result = original(command, environment)
        path = installation.prefix / "lib/python3.14/site-packages/heinzel-0.9.6.dist-info/METADATA"
        path.write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.7\n")
        return result

    installation._runner = changed_core
    with pytest.raises(HeinzelError, match="metadata does not match"):
        service.add("heinzel-demo", confirm=True)
    assert events == ["uv"]
    assert daemon._handover_pending()


def test_generation_changed_after_host_identity_check_never_probes(guarded, monkeypatch):
    service, installation, daemon, events, _wrong = guarded
    verify = service._verify_added

    def changed(*args):
        result = verify(*args)
        (installation.prefix / "bin/hz").write_text("changed after host check")
        return result

    monkeypatch.setattr(service, "_verify_added", changed)
    with pytest.raises(HeinzelError, match="changed during verification"):
        service.add("heinzel-demo", confirm=True)
    assert events == ["uv"]
    assert daemon._handover_pending()


def test_registered_plugin_factory_uses_kernel_installation_and_lifecycle(make_kernel):
    from unittest.mock import Mock

    from heinzel.kernel import DaemonInstallationLifecycle

    kernel = make_kernel(builtins=True)
    lifecycle = Mock(spec=DaemonInstallationLifecycle)
    kernel.bind(DaemonInstallationLifecycle, lifecycle)
    service = kernel.resolve(PluginService)
    assert service._installation is kernel.resolve(Installation)
    assert service._lifecycle is lifecycle


def test_nested_tool_transaction_refuses_changed_owned_entrypoint(guarded, tmp_path):
    _service, installation, _daemon, events, _wrong = guarded
    shim = tmp_path / "commands/hz"
    replacement = tmp_path / "foreign"
    replacement.write_text("foreign")
    with installation.tool_transaction():
        assert shim.parent.resolve().is_relative_to(tmp_path.resolve())
        assert shim.is_symlink()
        assert shim.resolve().is_relative_to(tmp_path.resolve())
        shim.unlink()
        shim.symlink_to(replacement)
        with pytest.raises(HeinzelError, match="verify the running uv tool"), installation.tool_transaction():
            pytest.fail("changed target was adopted")
    assert events == []


def test_receipt_sync_failure_after_verified_replace_does_not_replay(guarded, monkeypatch):
    from heinzel.modules.system.plugin import _receipts

    service, installation, daemon, events, _wrong = guarded
    installation.record_startup_state(expected=installation.startup_fingerprint())
    preview = service.add("heinzel-demo", confirm=False)
    operation_id = preview.operation_id
    assert operation_id
    original = _receipts._fsync_directory

    def fail_final(directory):
        if directory == service._receipts.directory and events and not daemon._handover_pending():
            msg = "receipt final directory sync denied"
            raise OSError(msg)
        return original(directory)

    monkeypatch.setattr(_receipts, "_fsync_directory", fail_final)
    with pytest.raises(HeinzelError, match="could not be made durable"):
        service.add("heinzel-demo", confirm=True, operation_id=operation_id)
    assert service.operation(operation_id).status == "completed"
    assert service.add("heinzel-demo", confirm=True, operation_id=operation_id).done
    assert events == ["uv", "target-probe"]


def test_develop_uses_same_fence_and_host_source_verification(guarded, tmp_path):
    service, installation, daemon, events, _wrong = guarded
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="heinzel-demo"\nversion="1.0.0"\n')

    def fake_develop(command, environment):
        events.append("uv")
        assert daemon._handover_pending()
        path = Path(command[command.index("--constraints") + 1])
        assert path.read_text() == "heinzel==0.9.6\n"
        _append_guard(installation.prefix / "uv-receipt.toml")
        metadata = installation.prefix / "lib/python3.14/site-packages/heinzel_demo-1.0.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-demo\nVersion: 1.0.0\n")
        (metadata / "entry_points.txt").write_text("[heinzel.modules]\ndemo = untrusted\n")
        (metadata / "direct_url.json").write_text(json.dumps({"url": source.as_uri(), "dir_info": {}}))
        return subprocess.CompletedProcess(command, 0, "", "")

    installation._runner = fake_develop
    assert service.develop(source).done
    assert events == ["uv", "target-probe"]
    assert not daemon._handover_pending()
    assert not installation.startup_state_matches()
