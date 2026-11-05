"""The dedicated recovery parser never enters ordinary CLI or ambient plugin discovery."""

import pytest

from heinzel.heads import daemon_recovery as head
from heinzel.kernel import DaemonLegacyRecovery, DaemonRecoveryPlan, DaemonRecoveryResult

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "arguments",
    [
        ["--help"],
        ["plan", "--help"],
        [],
        ["install"],
        ["retire"],
        ["plan", "extra"],
        ["restore"],
        ["retire", "--target", "/foreign"],
    ],
)
def test_help_and_parser_failures_never_resolve_recovery(monkeypatch, arguments):
    monkeypatch.setattr(head, "recovery", lambda: pytest.fail("parser entered recovery authority"))
    with pytest.raises(SystemExit):
        head.main(arguments)


def test_only_explicit_typed_commands_reach_the_narrow_port(monkeypatch, capsys):
    calls = []
    digest = "a" * 64
    plan = DaemonRecoveryPlan(digest=digest, facts={"historical_profile": "unknown"})

    def retire(confirmation):
        calls.append(("retire", confirmation))
        return DaemonRecoveryResult(digest=digest, status="retired", message="Historical result only.")

    port = DaemonLegacyRecovery(
        lambda: plan, retire, lambda transaction: plan, lambda transaction, confirmation: retire(confirmation)
    )
    monkeypatch.setattr(head, "recovery", lambda: port)
    assert head.main(["plan"]) == 0
    assert calls == []
    assert head.main(["retire", "--confirm", digest]) == 0
    assert calls == [("retire", digest)]
    assert head.main(["restore", digest]) == 0
    assert calls == [("retire", digest)]
    assert head.main(["restore", digest, "--confirm", digest]) == 0
    assert calls == [("retire", digest), ("retire", digest)]
    assert '"historical_profile": "unknown"' in capsys.readouterr().out


@pytest.mark.parametrize("problem", [None, "old-format", "unmatched-checkpoint"])
def test_recovery_binds_verified_persistent_target_before_plugin_disabled_resolution(monkeypatch, tmp_path, problem):
    from types import SimpleNamespace

    from heinzel import HeinzelError, Settings
    from heinzel.kernel import DaemonRecoverySelection

    calls = []
    selected = SimpleNamespace(prefix=tmp_path / "tools/heinzel", store=tmp_path / "tools", binary=tmp_path / "bin")
    settings = Settings(data_dir=tmp_path / "data")
    import json

    profile = json.dumps(
        {
            "format": 1 if problem == "old-format" else 2,
            "core_version": "0.9.15",
            "sdk": 1,
            "artifacts": [
                {
                    "name": "heinzel",
                    "version": "0.9.15",
                    "filename": "heinzel-0.9.15-py3-none-any.whl",
                    "sha256": "a" * 64,
                    "url": "fixture",
                }
            ],
        }
    )
    monkeypatch.setattr(head.install, "uv_executable", lambda: "fixture-uv")

    def resolve(uv, *, create):
        assert uv == "fixture-uv"
        assert create is False
        return selected

    monkeypatch.setattr(head, "resolve_target", resolve)
    monkeypatch.setattr(head.Config, "load", lambda directory: SimpleNamespace(settings=lambda kind, section: settings))
    settings.data_dir.mkdir(mode=0o755)
    settings.data_dir.chmod(0o755)
    receipt = settings.data_dir / "standard-app.json"
    head.install._write_standard_receipt(settings.data_dir, head.install.AppProfile.model_validate_json(profile))
    assert receipt.stat().st_mode & 0o777 == 0o600

    class ObservedInstallation:
        def __init__(self, progress, *, prefix, uv):
            assert prefix == selected.prefix
            assert uv == "fixture-uv"

        def verify_tool_destination(self, store, binary):
            assert (store, binary) == (selected.store, selected.binary)
            calls.append("verify-target")

        def startup_state_matches(self):
            calls.append("verify-checkpoint")
            return problem != "unmatched-checkpoint"

    monkeypatch.setattr(head, "Installation", ObservedInstallation)

    class CandidateKernel:
        def bind(self, kind, instance):
            calls.append(kind)

        def resolve(self, kind):
            assert kind is DaemonLegacyRecovery
            assert calls[-2:] == [ObservedInstallation, DaemonRecoverySelection]
            calls.append("resolve")
            return "narrow-port"

    def boot(settings):
        assert settings.plugins_enabled is False
        calls.append("candidate-builtins-only")
        return CandidateKernel()

    monkeypatch.setattr(head, "boot", boot)
    if problem:
        with pytest.raises(HeinzelError):
            head.recovery()
        assert "candidate-builtins-only" not in calls
    else:
        assert head.recovery() == "narrow-port"
        assert calls[:3] == ["verify-target", "verify-checkpoint", "candidate-builtins-only"]


def test_published_install_receipt_is_readable_without_changing_directory_permissions(tmp_path):
    """The normal 022-umask data folder is public-searchable; mkstemp keeps receipt secrets private."""
    from heinzel import HeinzelError
    from heinzel.kernel import private_read

    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    data.chmod(0o755)
    receipt = data / "standard-app.json"
    receipt.write_bytes(b'{"format":2}')
    receipt.chmod(0o600)
    before = data.stat()
    with pytest.raises(HeinzelError):
        private_read(receipt)
    assert head._standard_receipt(data) == b'{"format":2}'
    assert data.stat() == before
    assert receipt.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "problem",
    [
        "directory-link",
        "parent-link",
        "writable-data",
        "writable-parent",
        "file-link",
        "hardlink",
        "readable-secret",
        "directory",
        "fifo",
        "oversize",
    ],
)
def test_legacy_directory_compatibility_preserves_private_receipt_boundary(tmp_path, problem):
    import os

    from heinzel import HeinzelError

    parent = tmp_path / "parent"
    parent.mkdir(mode=0o755)
    data = parent / "data"
    data.mkdir(mode=0o755)
    receipt = data / "standard-app.json"
    receipt.write_bytes(b"secret")
    receipt.chmod(0o600)
    selected = data
    if problem == "directory-link":
        selected = parent / "alias"
        selected.symlink_to(data, target_is_directory=True)
    elif problem == "parent-link":
        alias = tmp_path / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        selected = alias / "data"
    elif problem == "writable-data":
        data.chmod(0o775)
    elif problem == "writable-parent":
        parent.chmod(0o777)
    elif problem == "file-link":
        secret = data / "secret"
        receipt.rename(secret)
        receipt.symlink_to(secret)
    elif problem == "hardlink":
        os.link(receipt, data / "second")
    elif problem == "readable-secret":
        receipt.chmod(0o644)
    elif problem == "directory":
        receipt.unlink()
        receipt.mkdir(mode=0o700)
    elif problem == "fifo":
        receipt.unlink()
        os.mkfifo(receipt, 0o600)
    else:
        assert problem == "oversize"
        receipt.write_bytes(b"x" * (16 * 1024 + 1))
    with pytest.raises((HeinzelError, OSError)):
        head._standard_receipt(selected)


def test_receipt_entry_replacement_during_read_is_refused(monkeypatch, tmp_path):
    import os

    from heinzel import HeinzelError

    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    receipt = data / "standard-app.json"
    receipt.write_bytes(b"old")
    receipt.chmod(0o600)
    original = os.stat

    def replace_entry(path, *args, **kwargs):
        if path == "standard-app.json":
            replacement = data / "replacement"
            replacement.write_bytes(b"new")
            replacement.chmod(0o600)
            replacement.replace(receipt)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(head.os, "stat", replace_entry)
    with pytest.raises(HeinzelError, match="changed"):
        head._standard_receipt(data)


@pytest.mark.parametrize("target", ["data", "receipt"])
def test_foreign_owned_data_or_receipt_is_refused(monkeypatch, tmp_path, target):
    import os

    from heinzel import HeinzelError

    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    receipt = data / "standard-app.json"
    receipt.write_bytes(b"secret")
    receipt.chmod(0o600)
    foreign_inode = (data if target == "data" else receipt).stat().st_ino
    original = os.fstat

    def foreign_owner(descriptor):
        info = original(descriptor)
        if info.st_ino == foreign_inode:
            fields = list(info)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(head.os, "fstat", foreign_owner)
    with pytest.raises(HeinzelError):
        head._standard_receipt(data)


def test_directory_entry_replacement_during_read_is_refused(monkeypatch, tmp_path):
    import os

    from heinzel import HeinzelError

    data = tmp_path / "data"
    data.mkdir(mode=0o755)
    receipt = data / "standard-app.json"
    receipt.write_bytes(b"secret")
    receipt.chmod(0o600)
    original = os.stat

    def replace_directory(path, *args, **kwargs):
        if path == "standard-app.json":
            data.rename(tmp_path / "previous")
            data.mkdir(mode=0o755)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(head.os, "stat", replace_directory)
    with pytest.raises(HeinzelError, match="directory changed"):
        head._standard_receipt(data)
