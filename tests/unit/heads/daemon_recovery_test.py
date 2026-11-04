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
    monkeypatch.setattr(head, "private_read", lambda path: profile)

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
