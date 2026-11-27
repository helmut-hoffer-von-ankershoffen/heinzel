"""Workspace version observations distinguish loaded, installed and historical daemon code."""

from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from types import SimpleNamespace

import psutil
import pytest
from fastapi.testclient import TestClient

from heinzel import HeinzelError, __version__
from heinzel.heads import web
from heinzel.heads.web._workspace_version import _backend_ip
from heinzel.modules.system.client import _workspace_version as subject

pytestmark = pytest.mark.unit


def test_web_process_start_is_distinct_from_instance_initialization(monkeypatch):
    started = datetime.now(UTC) - timedelta(hours=2)
    initialized = started + timedelta(hours=1)
    monkeypatch.setattr(subject.psutil, "Process", lambda: SimpleNamespace(create_time=started.timestamp))
    monkeypatch.setattr(subject, "_installed", lambda _kernel: subject.WorkspaceInstalledVersion())
    monkeypatch.setattr(subject, "_daemon", lambda _kernel, _now: subject.WorkspaceDaemonVersion())
    result = subject.observe(FakeKernel(), initialized)
    assert result.web.running_since == started
    assert result.web.observed_since == initialized
    assert result.web.backend_ip is None
    assert result.daemon.running_since is None
    assert result.installed.updated_at is None


@pytest.mark.parametrize("timestamp", [0, -1, True, float("nan"), float("inf"), 1e100, None, "unknown"])
def test_invalid_process_start_is_unknown(monkeypatch, timestamp):
    monkeypatch.setattr(subject.psutil, "Process", lambda: SimpleNamespace(create_time=lambda: timestamp))
    assert subject._process_started(datetime.now(UTC)) is None


def test_future_process_start_is_unknown_after_wall_clock_change(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        subject.psutil, "Process", lambda: SimpleNamespace(create_time=(now + timedelta(seconds=1)).timestamp)
    )
    assert subject._process_started(now) is None


@pytest.mark.parametrize("error", [psutil.AccessDenied(), psutil.NoSuchProcess(123), OSError("unavailable")])
def test_process_inspection_failure_is_unknown(monkeypatch, error):
    def inaccessible():
        raise error

    monkeypatch.setattr(subject.psutil, "Process", inaccessible)
    assert subject._process_started(datetime.now(UTC)) is None


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
    actual_start = datetime.fromtimestamp(psutil.Process().create_time(), UTC)
    assert datetime.fromisoformat(result["web"]["running_since"]) == actual_start
    assert actual_start <= datetime.fromisoformat(result["web"]["observed_since"])
    assert result["installed"] == {"version": "0.9.49", "updated_at": None}
    assert result["daemon"]["running_version"] is None
    assert result["daemon"]["status_observed_at"] is None
    current[0] = "0.9.50"
    refreshed = client.get("/api/workspace-version", headers={"Authorization": f"Bearer {token}"})
    assert refreshed.json()["installed"]["version"] == "0.9.50"
    assert refreshed.json()["web"]["observed_since"] == result["web"]["observed_since"]
    assert refreshed.json()["web"]["running_since"] == result["web"]["running_since"]

    def unavailable():
        raise psutil.AccessDenied

    monkeypatch.setattr(subject.psutil, "Process", unavailable)
    unavailable_response = client.get("/api/workspace-version", headers={"Authorization": f"Bearer {token}"})
    assert unavailable_response.status_code == 200
    assert unavailable_response.json()["web"]["running_since"] is None
    assert unavailable_response.json()["web"]["observed_since"] == result["web"]["observed_since"]


@pytest.mark.parametrize(
    "server",
    [
        None,
        (),
        ("192.0.2.1",),
        "192.0.2.1",
        (123, 8000),
        ("192.0.2.1", None),
        ("192.0.2.1", True),
        ("192.0.2.1", 0),
        ("backend.example", 8000),
        (str(IPv4Address(0)), 8000),
        ("::", 8000),
        ("192.168.001.1", 8000),
        ("[::1]", 8000),
        ("fe80::1%en0", 8000),
        (" 192.0.2.1", 8000),
    ],
)
def test_backend_ip_rejects_missing_wildcard_or_nonliteral_addresses(server):
    assert _backend_ip(server) is None


@pytest.mark.parametrize(
    ("server", "expected"),
    [
        (("192.0.2.10", 8000), "192.0.2.10"),
        (("2001:db8:0:0::1", 8000), "2001:db8::1"),
        (None, None),
        ((str(IPv4Address(0)), 8000), None),
    ],
)
def test_authenticated_route_reports_local_backend_not_proxy_or_forwarded_headers(
    make_kernel, monkeypatch, server, expected
):
    monkeypatch.setattr(subject, "_installed", lambda _kernel: subject.WorkspaceInstalledVersion())
    monkeypatch.setattr(subject, "_daemon", lambda _kernel, _now: subject.WorkspaceDaemonVersion())
    token = "t" * 43
    app = web.build(make_kernel(builtins=True), port=8000, token=token, static=None)

    async def behind_proxy(scope, receive, send):
        scope = dict(scope)
        scope["client"] = ("198.51.100.25", 45678)
        if server is None:
            scope.pop("server", None)
        else:
            scope["server"] = server
        await app(scope, receive, send)

    client = TestClient(behind_proxy, base_url="http://127.0.0.1:8000")
    headers = {
        "Forwarded": "for=203.0.113.10;host=203.0.113.11",
        "X-Forwarded-For": "203.0.113.12",
        "X-Forwarded-Host": "203.0.113.13",
        "X-Real-IP": "203.0.113.14",
    }
    assert client.get("/api/workspace-version", headers=headers).status_code == 401
    response = client.get("/api/workspace-version", headers={**headers, "Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["web"]["backend_ip"] == expected


def test_old_web_version_payload_keeps_backend_ip_unknown():
    old_payload = {"version": "0.9.57", "observed_since": datetime.now(UTC).isoformat()}
    assert subject.WorkspaceWebVersion.model_validate(old_payload).backend_ip is None
