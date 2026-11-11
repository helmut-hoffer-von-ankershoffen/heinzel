"""One isolated manager owns actual Core children for independent CLI and authenticated web kernels."""

import json
import logging
import signal
import socket
import stat
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from threading import Event

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.cli import build
from heinzel.kernel import MaintenanceLocks, exposed
from heinzel.kernel._logging import record_fields
from heinzel.modules.system.mcp import _service
from heinzel.modules.system.mcp._records import Maintenance, manager_prefix, root
from tests.fixtures import mcp_managed

managed = mcp_managed.managed
pytestmark = pytest.mark.integration
BASE = "http://127.0.0.1:8000"
SESSION = "fictional_shared_mcp_web_authority_1234567890"
HEADERS = {"Authorization": f"Bearer {SESSION}"}


def cli(kernel, operation, *, confirm=True):
    arguments = ["system", "mcp", operation, "--json"]
    if confirm and operation != "check":
        arguments.append("--confirm")
    return CliRunner().invoke(build(kernel), arguments)


def client(kernel):
    return TestClient(web.build(kernel, port=8000, token=SESSION, static=None), base_url=BASE)


def operation_result(response):
    assert response.status_code == 200, response.text
    lines = response.text.splitlines()
    results = [
        json.loads(value.removeprefix("data: "))["data"] for event, value in pairwise(lines) if event == "event: result"
    ]
    assert len(results) == 1, response.text
    return results[0]


def assert_refused_before_changes(cli_kernel, ui, manager, calls):
    assert ui.get("/api/system/mcp/check").status_code == 401
    cancelled = ui.post("/api/stream/system/mcp/start", headers=HEADERS, json={"confirm": False})
    assert "event: error" in cancelled.text
    assert "event: result" not in cancelled.text
    assert ui.post("/api/stream/system/mcp/start?confirm=true", headers=HEADERS).status_code == 422
    assert (
        ui.post(
            "/api/stream/system/mcp/start",
            headers=HEADERS,
            json={"confirm": True, "endpoint": "http://127.0.0.1:1234/mcp"},
        ).status_code
        == 422
    )
    assert cli(cli_kernel, "start", confirm=False).exit_code != 0
    assert not calls
    assert not manager.calls


def test_shared_cli_web_lifecycle_idempotency_disable_and_real_private_protocol(managed):
    installed, manager, calls, kernel = managed
    cli_kernel = kernel()
    ui = client(kernel())  # Distinct kernel and service instance; one durable profile/manager owner.
    assert_refused_before_changes(cli_kernel, ui, manager, calls)
    first = cli(cli_kernel, "start")
    assert first.exit_code == 0, first.output
    ready = json.loads(first.output)
    assert ready["readiness"] == "authenticated-initialized"
    assert ready["tools"] > 0
    assert ready["ownership"] == "verified"
    assert ui.get("/api/system/mcp/check", headers=HEADERS).json()["generation"] == ready["generation"]
    same = ui.post("/api/stream/system/mcp/start", headers=HEADERS, json={"confirm": True})
    assert operation_result(same)["generation"] == ready["generation"]
    assert calls.count("mcp-token") == 1
    assert sum(call[1] == "bootstrap" for call in manager.calls) == 1

    disabled_kernel = kernel(enabled=False)
    disabled = client(disabled_kernel)
    observed = disabled.get("/api/system/mcp/check", headers=HEADERS).json()
    assert observed["state"] == "ready"
    assert observed["can_stop"]
    assert not observed["can_start"]
    assert json.loads(cli(disabled_kernel, "start").output)["state"] == "unavailable"
    assert calls.count("mcp-token") == 1
    generation = _service.Service(cli_kernel).authority().existing()
    assert generation is not None
    directory = generation.directory(installed.prefix)
    assert stat.S_IMODE((directory / "probe-token").stat().st_mode) == 0o600
    stopped = disabled.post("/api/stream/system/mcp/stop", headers=HEADERS, json={"confirm": True})
    closure = operation_result(stopped)
    assert closure["owned_server"] == "closed"
    assert closure["manager_cleanup"] == "pending"
    assert closure["state"] == "worker-closed"
    assert closure["descendants"] == "unverified"
    assert all(not (directory / name).exists() for name in ("probe-token", "stop-token", "tls-key.pem"))
    assert manager.process.poll() == 0
    assert generation.label(installed.prefix) in manager.jobs
    repeated = cli(cli_kernel, "stop")
    assert repeated.exit_code == 0
    assert json.loads(repeated.output)["generation"] == ready["generation"]
    assert not any(call[1] == "bootout" for call in manager.calls)
    assert (
        "hzmcp_"
        not in first.output + same.text + stopped.text + repeated.output + (directory / "server.stderr").read_text()
    )
    audits = [json.loads(path.read_bytes()) for path in directory.glob(".fixture-audit-*.json")]
    assert len(audits) == 2  # token mint and actual server boot; pinned transport probes never boot config
    assert all(audit == {"private": True, "update_check": False, "external": 0, "updates": 0} for audit in audits)

    restarted = cli(cli_kernel, "start")
    assert restarted.exit_code == 0, restarted.output
    assert json.loads(restarted.output)["generation"] != ready["generation"]
    assert (len(manager.jobs), calls.count("mcp-token")) == (2, 2)
    assert manager.jobs[generation.label(installed.prefix)][2].poll() == 0
    assert cli(cli_kernel, "stop").exit_code == 0


def test_cold_disabled_and_wrong_heads_have_no_effects(managed):
    installed, manager, calls, kernel = managed
    selected = kernel(enabled=False)
    assert json.loads(cli(selected, "start").output)["state"] == "unavailable"
    assert json.loads(cli(selected, "check").output)["state"] == "unavailable"
    assert json.loads(cli(selected, "stop").output)["state"] == "unavailable"
    assert not calls
    assert not manager.calls
    assert not root(installed.prefix).exists()
    for head in ("api", "rest", "mcp", "mcp-http"):
        assert all(item.record.qualname != "system.mcp" for item in exposed(selected, head))


def test_prepared_failure_retained_and_stop_recovers_without_new_credentials(managed, caplog):
    _, manager, calls, kernel = managed
    selected = kernel()
    manager.fail_bootstrap = True
    caplog.set_level(logging.INFO, logger="heinzel.mcp.managed")
    assert cli(selected, "start").exit_code != 0
    authority = _service.Service(selected).authority()
    prior = authority.existing()
    assert prior is not None
    assert prior.phase == "prepared"
    assert cli(selected, "start").exit_code != 0
    assert authority.existing() == prior
    assert calls.count("mcp-token") == 1
    stopped = cli(kernel(enabled=False), "stop")
    assert stopped.exit_code == 0
    assert json.loads(stopped.output)["owned_server"] == "closed"
    assert authority.existing().phase == "stopped"
    events = [
        (record.message, record_fields(record).get("phase"), record_fields(record).get("outcome"))
        for record in caplog.records
        if record.getMessage().startswith("mcp.managed.")
    ]
    assert [(phase, outcome) for name, phase, outcome in events if name == "mcp.managed.start"] == [
        ("start", None),
        ("end", "error"),
        ("start", None),
        ("end", "error"),
    ]
    assert [(phase, outcome) for name, phase, outcome in events if name == "mcp.managed.stop"] == [
        ("start", None),
        ("end", "ok"),
    ]
    assert "hzmcp_" not in caplog.text


def test_changed_job_and_failed_shutdown_preserve_receipt_and_refuse_restart(managed):
    installed, manager, calls, kernel = managed
    selected = kernel()
    assert cli(selected, "start").exit_code == 0
    authority = _service.Service(selected).authority()
    generation = authority.existing()
    record = generation.path(installed.prefix, ".json")
    before = record.read_bytes()
    manager.changed_program = True
    assert cli(selected, "stop").exit_code != 0
    assert not any(call[1] == "bootout" for call in manager.calls)
    manager.changed_program = False
    credential = generation.directory(installed.prefix) / "stop-token"
    original = credential.read_bytes()
    credential.write_text("fictional corrupted credential\n")
    assert cli(selected, "stop").exit_code != 0
    assert record.read_bytes() == before
    assert manager.process.poll() is None
    assert calls.count("mcp-token") == 1
    credential.write_bytes(original)
    assert cli(selected, "stop").exit_code == 0


def test_web_start_cannot_cross_cli_shutdown_under_shared_locks(managed, monkeypatch):
    _, manager, calls, kernel = managed
    selected = kernel()
    ui = client(kernel())
    assert cli(selected, "start").exit_code == 0
    entered, release = Event(), Event()
    original = _service.Service._request

    def pause(service, authority, generation, *, stop=False):
        if stop:
            entered.set()
            assert release.wait(5)
        return original(service, authority, generation, stop=stop)

    monkeypatch.setattr(_service.Service, "_request", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        stop = pool.submit(cli, selected, "stop")
        try:
            assert entered.wait(5)
            refused = ui.post("/api/stream/system/mcp/start", headers=HEADERS, json={"confirm": True})
            assert "event: error" in refused.text
            assert "event: result" not in refused.text
            assert calls.count("mcp-token") == 1
        finally:
            release.set()
        assert stop.result(timeout=10).exit_code == 0
    assert manager.process.poll() == 0
    started = ui.post("/api/stream/system/mcp/start", headers=HEADERS, json={"confirm": True})
    assert operation_result(started)["state"] == "ready"
    assert len(manager.jobs) == 2
    assert calls.count("mcp-token") == 2
    assert cli(selected, "stop").exit_code == 0


def test_missing_pid_without_positive_inactivity_keeps_recovery(managed):
    _, manager, _, kernel = managed
    selected = kernel()
    assert cli(selected, "start").exit_code == 0
    manager.process.terminate()
    assert manager.process.wait(timeout=5) in {0, -signal.SIGTERM}
    authority = _service.Service(selected).authority()
    before = authority.existing()
    manager.hide_inactive_state = True
    assert cli(selected, "stop").exit_code != 0
    assert authority.existing() == before
    assert cli(selected, "start").exit_code != 0
    manager.hide_inactive_state = False
    assert json.loads(cli(selected, "stop").output)["manager_cleanup"] == "pending"


def test_inventory_bound_reclaims_only_old_confirmed_absent_files(managed, monkeypatch):
    installed, manager, calls, kernel = managed
    selected = kernel()
    authority = _service.Service(selected).authority()
    monkeypatch.setattr(_service, "MAX_DEFINITIONS", 4)
    assert cli(selected, "start").exit_code == 0
    first = authority.existing()
    assert cli(selected, "stop").exit_code == 0
    assert cli(selected, "start").exit_code == 0
    second = authority.existing()
    assert cli(selected, "stop").exit_code == 0
    assert cli(selected, "start").exit_code != 0
    assert calls.count("mcp-token") == 2
    assert first.path(installed.prefix, ".plist").exists()
    manager.jobs.pop(first.label(installed.prefix))  # manager registration cleared after this worker exited
    assert cli(selected, "start").exit_code == 0
    assert not first.path(installed.prefix, ".plist").exists()
    assert not first.path(installed.prefix, ".json").exists()
    assert second.path(installed.prefix, ".plist").exists()
    assert calls.count("mcp-token") == 3
    assert cli(selected, "stop").exit_code == 0


def test_foreign_listener_never_receives_probe_bearer(managed, monkeypatch):
    _, manager, calls, kernel = managed
    selected = kernel()
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        monkeypatch.setattr(_service, "available_port", lambda: foreign.getsockname()[1])
        monkeypatch.setattr(_service, "START_WAIT", 1.5)
        assert cli(selected, "start").exit_code != 0
        assert calls == ["mcp-token"]  # no check/token submission to a listener not observed in owned stderr
        assert cli(selected, "stop").exit_code != 0
        assert foreign.fileno() >= 0
    assert manager.process.poll() is not None
    assert cli(selected, "stop").exit_code == 0


def test_physical_lock_and_foreign_registration_refuse_before_credential_mint(managed):
    installed, manager, calls, kernel = managed
    selected = kernel()
    authority = _service.Service(selected).authority()
    with MaintenanceLocks(authority.locks):
        assert cli(selected, "start").exit_code != 0
    assert not calls
    assert not manager.calls
    manager.extra.append(manager_prefix(installed.prefix) + authority.profile)
    assert cli(selected, "start").exit_code != 0
    assert not calls
    assert authority.existing() is None


def test_unverified_cli_entrypoint_cannot_mint_or_bootstrap(managed, monkeypatch):
    installed, manager, calls, kernel = managed
    monkeypatch.setattr(installed, "tool_entrypoints", lambda: ())
    selected = kernel()
    assert cli(selected, "start").exit_code != 0
    assert _service.Service(selected).authority().existing() is None
    assert not calls
    assert not manager.calls


def test_sdk_maintenance_inventories_only_scoped_closed_receipts_without_credentials(managed):
    installed, manager, _, kernel = managed
    selected = kernel()
    owner = Maintenance(installed.prefix, "darwin")
    assert cli(selected, "start").exit_code == 0
    with pytest.raises(HeinzelError, match="Stop the owned"):
        owner.inventory(installed.prefix, installed.binary, manager)
    assert cli(kernel(enabled=False), "stop").exit_code == 0
    with pytest.raises(HeinzelError, match="registration to clear"):
        owner.inventory(installed.prefix, installed.binary, manager)
    manager.close()  # fixture observes the user manager ending its registration after worker exit
    (item,) = owner.inventory(installed.prefix, installed.binary, manager)
    assert {file.path.suffix for file in item.files} == {".json", ".plist"}
    assert all(b"hzmcp_" not in file.content for file in item.files)
    assert owner.discovery_scopes()[0].directory == item.unit.parent
    with MaintenanceLocks(item.locks) as locks:
        guarded = owner.guard(item, manager, locks)
        manager.extra.append(item.name)
        with pytest.raises(HeinzelError, match="integration manager"):
            guarded.stop()
        manager.extra.remove(item.name)
    manager.extra.append(manager_prefix(installed.prefix) + "f" * 24)
    with pytest.raises(HeinzelError, match="could not be verified"):
        owner.inventory(installed.prefix, installed.binary, manager)


def test_authenticated_probe_cannot_accept_changed_manager_identity(managed, monkeypatch):
    _, manager, calls, kernel = managed
    selected = kernel()
    assert cli(selected, "start").exit_code == 0
    real_request = _service.Service._request

    def changed(service, authority, generation, *, stop=False):
        result = real_request(service, authority, generation, stop=stop)
        if not stop:
            manager.reported_pid = manager.process.pid + 1
        return result

    monkeypatch.setattr(_service.Service, "_request", changed)
    result = client(kernel()).get("/api/system/mcp/check", headers=HEADERS)
    assert result.status_code == 200
    assert result.json()["state"] == "unverified"
    assert calls.count("mcp-check") == 2
    assert calls.count("mcp-token") == 1
    manager.reported_pid = None
    assert cli(selected, "stop").exit_code == 0


def test_changed_private_configuration_refuses_probe_but_not_owned_stop(managed):
    installed, _, _, kernel = managed
    selected = kernel()
    assert cli(selected, "start").exit_code == 0
    current = _service.Service(selected).authority().existing()
    assert current is not None
    directory = current.directory(installed.prefix)
    prior = set(directory.glob(".fixture-audit-*.json"))
    (directory / "hz.toml").write_text("fictional poisoned generation = [")
    observed = json.loads(cli(selected, "check").output)
    assert observed["state"] == "unverified"
    assert observed["can_stop"]
    assert set(directory.glob(".fixture-audit-*.json")) == prior
    assert cli(kernel(enabled=False), "stop").exit_code == 0
