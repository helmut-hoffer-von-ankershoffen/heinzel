"""Runner snapshot uses real owner configuration without exposing its contents."""

import pytest

from heinzel import Config
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService

pytestmark = pytest.mark.unit


def test_runner_snapshot_tracks_selected_settings_and_redacts_other_values(monkeypatch, tmp_path):
    service = RunnerSnapshotService()
    config = Config(files=(), data={})
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE", str(tmp_path / "first-claude"))
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__COMMAND_ENV", '["sensitive-value"]')
    first = service.snapshot(config)
    assert first.claude_executable == str(tmp_path / "first-claude")
    assert first.model is None
    assert "sensitive-value" not in repr(first)
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__COMMAND_ENV", '["other-value"]')
    changed = service.snapshot(config)
    assert changed != first
    assert changed.claude_executable == first.claude_executable
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE", str(tmp_path / "second-claude"))
    assert service.snapshot(config).claude_executable == str(tmp_path / "second-claude")


def test_runner_snapshot_provider_must_be_the_registered_builtin(make_kernel):
    from heinzel import HeinzelError
    from heinzel.kernel import RunnerSnapshotProvider
    from heinzel.modules.system.runner._snapshot import RunnerSnapshotService

    kernel = make_kernel(builtins=True)
    assert kernel.resolve_builtin_service(RunnerSnapshotProvider, "system.runner") is kernel.resolve(
        RunnerSnapshotProvider
    )
    kernel.bind(RunnerSnapshotProvider, RunnerSnapshotService())
    with pytest.raises(HeinzelError, match="instance changed"):
        kernel.resolve_builtin_service(RunnerSnapshotProvider, "system.runner")


def test_registered_user_chat_resolves_owning_builtin_runner_provider(make_kernel):
    from heinzel.modules.heinzel._user_chat import UserChatService

    kernel = make_kernel(builtins=True)
    assert isinstance(kernel.resolve(UserChatService), UserChatService)
