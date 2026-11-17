"""Workspace version observations distinguish loaded, installed and historical daemon code."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from heinzel import HeinzelError, __version__
from heinzel.heads import web
from heinzel.heads.web import _workspace_version as subject

pytestmark = pytest.mark.unit


class FakeKernel:
    def __init__(self, status=None, *, fail=False):
        self.status = status
        self.fail = fail

    def lets(self, _kind):
        operation = SimpleNamespace(name="status", read_only=True, private=False, heads=None, func=lambda: None)
        return [(SimpleNamespace(qualname="system.daemon"), operation)]

    def available(self, _func):
        return True

    def invoke(self, _operation):
        if self.fail:
            message = "private detail"
            raise HeinzelError(message)
        return self.status


def test_daemon_reports_old_observation_as_historical_and_stale():
    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    status = SimpleNamespace(
        version="0.9.43",
        last_tick=now - timedelta(hours=1),
        last_tick_interval_minutes=5,
        installed_at=now - timedelta(days=2),
        loaded=True,
        owned=True,
    )
    result = subject._daemon(FakeKernel(status), now)
    assert result.running_version is None
    assert result.running_since is None
    assert result.last_observed_version == "0.9.43"
    assert result.last_observed_at == status.last_tick
    assert result.observation_stale is True
    assert result.registered_at == status.installed_at
    assert result.loaded is True
    assert result.owned is True


def test_builtin_daemon_status_operation_is_selected_without_running_manager(make_kernel, monkeypatch):
    now = datetime.now(UTC)
    status = SimpleNamespace(
        version="0.9.43",
        last_tick=now - timedelta(minutes=1),
        last_tick_interval_minutes=5,
        installed_at=None,
        loaded=True,
        owned=True,
    )
    kernel = make_kernel(builtins=True)
    invoked = []

    def invoke(operation):
        invoked.append(operation.name)
        return status

    monkeypatch.setattr(kernel, "invoke", invoke)
    result = subject._daemon(kernel, now)
    assert invoked == ["status"]
    assert result.last_observed_version == "0.9.43"
    assert result.observation_stale is False


def test_staleness_uses_observed_tick_interval_not_current_registration():
    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    status = SimpleNamespace(
        version="0.9.43",
        last_tick=now - timedelta(minutes=10),
        interval_minutes=1,
        last_tick_interval_minutes=20,
        installed_at=None,
        loaded=True,
        owned=True,
    )
    assert subject._daemon(FakeKernel(status), now).observation_stale is False
    status.last_tick_interval_minutes = None
    assert subject._daemon(FakeKernel(status), now).observation_stale is None


def test_missing_unavailable_or_unattested_daemon_is_unknown():
    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    assert subject._daemon(FakeKernel(fail=True), now) == subject.WorkspaceDaemonVersion()
    status = SimpleNamespace(
        version="0.9.43", last_tick=None, last_tick_interval_minutes=5, installed_at=None, loaded=False, owned=False
    )
    result = subject._daemon(FakeKernel(status), now)
    assert result.last_observed_version is None
    assert result.last_observed_at is None
    assert result.observation_stale is None
    assert result.loaded is None
    status.last_tick = now + timedelta(minutes=1)
    assert subject._daemon(FakeKernel(status), now).last_observed_version is None


@pytest.mark.parametrize(
    "operation",
    [
        SimpleNamespace(name="status", read_only=False, private=False, heads=None, func=lambda: None),
        SimpleNamespace(name="status", read_only=True, private=True, heads=None, func=lambda: None),
        SimpleNamespace(name="status", read_only=True, private=False, heads={"cli"}, func=lambda: None),
    ],
)
def test_workspace_never_invokes_non_web_or_private_daemon_operation(operation):
    class Restricted(FakeKernel):
        def lets(self, _kind):
            return [(SimpleNamespace(qualname="system.daemon"), operation)]

    assert subject._daemon(Restricted(), datetime.now(UTC)) == subject.WorkspaceDaemonVersion()


def test_installed_version_is_inspected_each_time_and_never_inferred_from_server():
    class Installation:
        is_tool = True
        version = "0.9.43"

        def installed_core_version(self):
            if self.version is None:
                message = "not verified"
                raise HeinzelError(message)
            return self.version

    installation = Installation()
    kernel = SimpleNamespace(resolve=lambda _type: installation)
    assert subject._installed(kernel).version == "0.9.43"
    installation.version = "0.9.49"
    assert subject._installed(kernel).version == "0.9.49"
    installation.version = None
    result = subject._installed(kernel)
    assert result.version is None
    assert result.updated_at is None


def test_workspace_version_is_authenticated_and_never_cached(make_kernel, monkeypatch):
    current = ["0.9.49"]
    monkeypatch.setattr(subject, "_installed", lambda _kernel: subject.WorkspaceInstalledVersion(version=current[0]))
    monkeypatch.setattr(subject, "_daemon", lambda _kernel, _now: subject.WorkspaceDaemonVersion())
    token = "t" * 43
    app = web.build(make_kernel(builtins=True), port=8000, token=token, static=None)
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    assert client.get("/api/workspace-version").status_code == 401
    response = client.get("/api/workspace-version", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["web"]["version"] == __version__
    assert result["web"]["observed_since"] is not None
    assert result["web"]["running_since"] is None
    assert result["installed"] == {"version": "0.9.49", "updated_at": None}
    assert result["daemon"]["running_version"] is None
    assert result["daemon"]["status_observed_at"] is None
    current[0] = "0.9.50"
    refreshed = client.get("/api/workspace-version", headers={"Authorization": f"Bearer {token}"})
    assert refreshed.json()["installed"]["version"] == "0.9.50"
    assert refreshed.json()["web"]["observed_since"] == result["web"]["observed_since"]
