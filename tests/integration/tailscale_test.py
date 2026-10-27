"""Offline CLI and authenticated web sharing use the same persisted intent and conditional vendor state."""

import copy
import json
import os
import time
import tomllib
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from heinzel_tailscale import module
from heinzel_tailscale._cli import TailscaleCli
from heinzel_tailscale._localapi import Connection, ServeStore, Snapshot
from heinzel_tailscale._mapping import Mapping
from heinzel_tailscale._service import SharingActions
from mcp import Client
from typer.testing import CliRunner

from heinzel import Config, ConfigEditor, CurrentUser, HeinzelError, Settings
from heinzel.heads import api, mcp, web
from heinzel.heads.cli import build
from heinzel.heads.web import _terminal

pytestmark = pytest.mark.integration
DNS = "workstation.tail-example.ts.net."
ORIGIN = "https://workstation.tail-example.ts.net"
OTHER = "https://other.example.test"
TOKEN = "isolated-session-token-for-sharing-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeStore(ServeStore):
    def __init__(self):
        self.config = {}
        self.version = 0
        self.writes = 0
        self.connects = 0
        self.concurrent = False
        self.uncertain = False
        self.discard = False
        self.dormant = False

    def connect(self):
        self.connects += 1
        return Connection("http://127.0.0.1:1")

    def read(self, connection):
        return Snapshot(copy.deepcopy(self.config), str(self.version))

    def write(self, connection, snapshot, updated):
        self.writes += 1
        if self.concurrent:
            self.config["FutureField"] = {"anotherOwner": True}
            self.version += 1
        if str(self.version) != snapshot.etag:
            msg = "Serve changed; nothing was overwritten"
            raise HeinzelError(msg)
        if self.discard:
            return
        if self.dormant:
            self.config["TCP"] = {}
        else:
            self.config = copy.deepcopy(updated)
        self.version += 1
        if self.uncertain:
            msg = "The action's outcome is unknown"
            raise HeinzelError(msg)


class FakeCli(TailscaleCli):
    def __init__(self, store):
        self.store = store
        self.calls = []

    def executable(self):
        return "/fake/tailscale"

    def json(self, arguments):
        self.calls.append(tuple(arguments))
        assert tuple(arguments) in {("status", "--json", "--peers=false"), ("serve", "status", "--json")}
        if arguments[0] == "status":
            return {"BackendState": "Running", "Self": {"DNSName": DNS}, "CertDomains": [DNS[:-1]]}
        return copy.deepcopy(self.store.config)


@pytest.fixture
def setup(make_kernel, tmp_path):
    store = FakeStore()

    def fresh():
        config = Config.load(tmp_path)
        kernel = make_kernel(builtins=True, tailscale=module, config=config, settings_=config.settings(Settings, "hz"))
        kernel.bind(TailscaleCli, FakeCli(store))
        kernel.bind(ServeStore, store)
        return kernel

    return fresh, store


def command(kernel, operation):
    result = CliRunner().invoke(build(kernel), ["tailscale", operation, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def saved():
    return tomllib.loads((Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").read_text())


def edit(kernel, key, value):
    with kernel.resolve(CurrentUser).local("cli"):
        return kernel.resolve(ConfigEditor).write(key, value)


def client(kernel, port=8737):
    return TestClient(web.build(kernel, port=port, token=TOKEN, static=None), base_url=f"http://127.0.0.1:{port}")


def test_cli_enable_persists_request_and_a_fresh_authenticated_web_owner_verifies_it(setup):
    fresh, store = setup
    pending = command(fresh(), "enable")
    assert pending["state"] == "apply-required"
    assert pending["desired_enabled"]
    assert pending["mapped"]
    assert saved()["hz"]["ui_allowed_origins"] == [ORIGIN]
    assert saved()["tailscale"]["intent"]["enabled"]
    assert store.writes == 1
    assert Mapping.for_node(DNS, 8737).matches(store.config)
    assert command(fresh(), "status")["state"] == "apply-required"
    web_client = client(fresh())
    assert web_client.get("/api/tailscale/status").status_code == 401
    verified = web_client.get("/api/tailscale/status", headers=AUTH)
    assert verified.status_code == 200, verified.text
    assert verified.json()["state"] == "ready"
    assert verified.json()["origin"] == ORIGIN
    assert store.writes == 1  # Status never replays desired state.


def test_missing_client_does_not_forget_the_persisted_requested_state(setup, monkeypatch):
    fresh, _store = setup
    command(fresh(), "enable")
    kernel = fresh()
    monkeypatch.setattr(kernel.resolve(TailscaleCli), "executable", lambda: None)
    result = command(kernel, "status")
    assert result["state"] == "unavailable"
    assert result["desired_enabled"]


def test_running_web_enable_hot_applies_origins_without_a_backend_restart(setup):
    fresh, store = setup
    web_client = client(fresh(), port=9123)
    external = AUTH | {"host": "workstation.tail-example.ts.net", "origin": ORIGIN}
    assert web_client.get("/api/tailscale/status", headers=external).status_code == 403
    response = web_client.post("/api/tailscale/enable", json={}, headers=AUTH)
    assert response.status_code == 200, response.text
    ready = response.json()
    assert ready["state"] == "ready"
    assert ready["apply_settings"] == {}
    assert ready["target"] == "http://127.0.0.1:9123"
    assert command(fresh(), "enable")["target"] == "http://127.0.0.1:9123"
    assert store.writes == 1  # A separate CLI preserves the observed override; it cannot prove a replacement.
    assert web_client.get("/api/tailscale/status", headers=external).status_code == 200
    disabled = web_client.post("/api/tailscale/disable", json={}, headers=AUTH)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["state"] == "off"
    assert disabled.json()["apply_settings"] == {}
    assert web_client.get("/api/tailscale/status", headers=external).status_code == 403


@pytest.mark.skipif(os.name != "posix", reason="the terminal needs a POSIX pseudo-terminal")
def test_disabling_tailscale_closes_external_terminal_and_keeps_local_terminal_open(setup, monkeypatch, tmp_path):
    fresh, _store = setup
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("HOME", str(tmp_path))
    web_client = client(fresh(), port=9123)
    enabled = web_client.post("/api/tailscale/enable", json={}, headers=AUTH)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "ready"

    ticks_file = tmp_path / "external-shell.ticks"
    quoted_ticks_file = str(ticks_file).replace("'", "'\\''")
    external_headers = {"host": "workstation.tail-example.ts.net", "origin": ORIGIN}
    with web_client.websocket_connect("/api/terminal", headers=external_headers) as external:
        external.send_text(json.dumps({"token": TOKEN}))
        external.send_bytes(f"while :; do printf x >> '{quoted_ticks_file}'; sleep 0.05; done\n".encode())
        deadline = time.monotonic() + 5
        while not ticks_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ticks_file.exists(), "the external terminal shell did not start"

        local_origin = "http://127.0.0.1:9123"
        with web_client.websocket_connect(
            "/api/terminal", headers={"host": "127.0.0.1:9123", "origin": local_origin}
        ) as local:
            local.send_text(json.dumps({"token": TOKEN}))
            disabled = web_client.post("/api/tailscale/disable", json={}, headers=AUTH)
            assert disabled.status_code == 200, disabled.text
            assert disabled.json()["state"] == "off"

            def receive_until_closed() -> None:
                while True:
                    external.receive_bytes()

            with pytest.raises(WebSocketDisconnect) as closed:
                receive_until_closed()
            assert closed.value.code == 1008

            local.send_bytes(b"echo local-terminal-still-open\n")
            output = b""
            deadline = time.monotonic() + 3
            while b"local-terminal-still-open" not in output and time.monotonic() < deadline:
                output += local.receive_bytes()
            assert b"local-terminal-still-open" in output

        ticks_after_close = ticks_file.read_bytes()
        time.sleep(0.3)
        assert ticks_file.read_bytes() == ticks_after_close, "the shell kept running after its terminal was revoked"


@pytest.mark.skipif(os.name != "posix", reason="the terminal needs a POSIX pseudo-terminal")
def test_disabling_tailscale_during_terminal_auth_does_not_start_a_shell(setup, monkeypatch):
    fresh, _store = setup
    spawned = False

    def spawn() -> tuple[int, object]:
        nonlocal spawned
        spawned = True
        message = "a shell must not start after its external origin was removed"
        raise AssertionError(message)

    monkeypatch.setattr(_terminal, "_spawn", spawn)
    web_client = client(fresh(), port=9123)
    enabled = web_client.post("/api/tailscale/enable", json={}, headers=AUTH)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "ready"

    external_headers = {"host": "workstation.tail-example.ts.net", "origin": ORIGIN}
    with web_client.websocket_connect("/api/terminal", headers=external_headers) as external:
        disabled = web_client.post("/api/tailscale/disable", json={}, headers=AUTH)
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["state"] == "off"
        external.send_text(json.dumps({"token": TOKEN}))
        with pytest.raises(WebSocketDisconnect) as closed:
            external.receive_text()
        assert closed.value.code == 1008
    assert not spawned


def test_a_new_bound_port_replaces_only_the_previous_exact_owned_target(setup):
    fresh, store = setup
    command(fresh(), "enable")
    response = client(fresh(), port=9123).post("/api/tailscale/enable", json={}, headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "ready"
    assert response.json()["target"] == "http://127.0.0.1:9123"
    assert Mapping.for_node(DNS, 9123).matches(store.config)
    assert store.writes == 2


def test_external_origin_change_requires_disabling_the_previous_owned_request(setup):
    fresh, store = setup
    command(fresh(), "enable")
    edit(fresh(), "tailscale.https_port", 9443)
    before = copy.deepcopy(store.config)
    result = CliRunner().invoke(build(fresh()), ["tailscale", "enable", "--json"])
    assert result.exit_code == 1
    assert "previous request off" in result.output
    assert store.config == before
    assert store.writes == 1
    assert saved()["hz"]["ui_allowed_origins"] == [ORIGIN]


def test_disable_preserves_other_services_and_manually_configured_origins(setup):
    fresh, store = setup
    edit(fresh(), "hz.ui_allowed_origins", [OTHER, ORIGIN])
    store.config = {"Services": {"svc:other": {"TCP": {"3000": {"TCPForward": "127.0.0.1:3000"}}}}}
    unrelated = copy.deepcopy(store.config)
    command(fresh(), "enable")
    result = command(fresh(), "disable")
    assert result["state"] == "off"
    assert not result["desired_enabled"]
    assert set(saved()["hz"]["ui_allowed_origins"]) == {OTHER, ORIGIN}
    assert store.config == unrelated | {"TCP": {}, "Web": {}}


def test_disable_removes_only_the_origin_the_plugin_added_and_reads_never_reenable(setup):
    fresh, store = setup
    edit(fresh(), "hz.ui_allowed_origins", [OTHER])
    command(fresh(), "enable")
    web_client = client(fresh())
    response = web_client.post("/api/tailscale/disable", json={}, headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "off"
    assert response.json()["apply_settings"] == {}
    assert saved()["hz"]["ui_allowed_origins"] == [OTHER]
    assert not saved()["tailscale"]["intent"]["enabled"]
    before = copy.deepcopy(store.config)
    command(fresh(), "status")
    assert store.config == before
    assert store.writes == 2


def test_reenable_and_repeated_disable_preserve_origin_ownership(setup):
    fresh, _store = setup
    command(fresh(), "enable")
    command(fresh(), "disable")
    command(fresh(), "enable")
    assert saved()["tailscale"]["intent"]["added_origin"]
    command(fresh(), "disable")
    assert saved()["hz"]["ui_allowed_origins"] == []
    edit(fresh(), "hz.ui_allowed_origins", [ORIGIN])
    command(fresh(), "disable")
    assert saved()["hz"]["ui_allowed_origins"] == [ORIGIN]  # A later manual grant belongs to its owner.


def test_off_without_an_owned_intent_preserves_a_matching_manual_mapping(setup):
    fresh, store = setup
    store.config = Mapping.for_node(DNS, 8737).enable({})
    before = copy.deepcopy(store.config)
    command(fresh(), "disable")
    assert store.config == before
    assert store.writes == 0
    assert not (Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").exists()


def test_completed_off_relinquishes_mapping_ownership_before_manual_recreation(setup):
    fresh, store = setup
    command(fresh(), "enable")
    command(fresh(), "disable")
    store.config = Mapping.for_node(DNS, 8737).enable({})
    before = copy.deepcopy(store.config)
    writes = store.writes
    command(fresh(), "disable")
    assert store.config == before
    assert store.writes == writes


def test_enable_does_not_adopt_a_preexisting_matching_manual_handler(setup):
    fresh, store = setup
    store.config = Mapping.for_node(DNS, 8737).enable({})
    before = copy.deepcopy(store.config)
    result = CliRunner().invoke(build(fresh()), ["tailscale", "enable", "--json"])
    assert result.exit_code == 1
    assert "not owned" in result.output
    assert store.config == before
    assert store.writes == 0
    assert not (Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").exists()


def test_origin_granted_by_a_manual_writer_before_the_locked_insert_is_never_claimed(setup, monkeypatch):
    fresh, store = setup
    kernel = fresh()
    editor = kernel.resolve(ConfigEditor)
    change = editor.change_list

    def grant_before_lock(key, **arguments):
        editor.write("hz.ui_allowed_origins", [ORIGIN])
        return change(key, **arguments)

    monkeypatch.setattr(editor, "change_list", grant_before_lock)
    command(kernel, "enable")
    assert not saved()["tailscale"]["intent"]["added_origin"]
    command(fresh(), "disable")
    assert saved()["hz"]["ui_allowed_origins"] == [ORIGIN]
    assert store.writes == 2


def test_public_configuration_api_cannot_approve_an_external_origin(setup):
    fresh, _store = setup
    rest = TestClient(api.build(fresh(), port=8737), base_url="http://127.0.0.1:8737")
    response = rest.post("/system/config/set", params={"key": "hz.ui_allowed_origins", "value": f'["{ORIGIN}"]'})
    assert response.status_code == 400
    assert "authenticated" in response.text
    assert not (Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").exists()
    assert rest.get("/system/config/get", params={"key": "hz.ui_allowed_origins"}).status_code == 400
    assert not any(
        setting["key"] == "ui_allowed_origins" for setting in rest.get("/system/config/show").json()["settings"]
    )
    authenticated = client(fresh())
    permitted = authenticated.post(
        "/api/system/config/set", params={"key": "hz.ui_allowed_origins", "value": f'["{ORIGIN}"]'}, headers=AUTH
    )
    assert permitted.status_code == 200, permitted.text
    assert saved()["hz"]["ui_allowed_origins"] == [ORIGIN]


@pytest.mark.parametrize("conflict", ["other-target", "funnel"])
def test_conflicts_are_refused_before_configuration_or_serve_changes(setup, conflict):
    fresh, store = setup
    mapping = Mapping.for_node(DNS, 8737)
    store.config = (
        {"AllowFunnel": {mapping.authority: True}}
        if conflict == "funnel"
        else {"Web": {mapping.authority: {"Handlers": {"/": {"Text": "Another app"}}}}}
    )
    before = copy.deepcopy(store.config)
    result = CliRunner().invoke(build(fresh()), ["tailscale", "enable", "--json"])
    assert result.exit_code == 1
    assert not (Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").exists()
    assert store.config == before
    assert store.writes == 0


def test_environment_owned_origin_refusal_does_not_mutate_serve(setup, monkeypatch):
    fresh, store = setup
    monkeypatch.setenv("HZ_UI_ALLOWED_ORIGINS", OTHER)
    result = CliRunner().invoke(build(fresh()), ["tailscale", "enable", "--json"])
    assert result.exit_code == 1
    assert "controlled by env" in result.output
    assert store.config == {}
    assert store.writes == 0
    assert saved()["tailscale"]["intent"]["enabled"]  # Desired state is saved; readiness is never claimed.
    assert "hz" not in saved()


def test_conditional_conflict_keeps_other_owner_state_without_retry_or_rollback(setup):
    fresh, store = setup
    store.concurrent = True
    result = CliRunner().invoke(build(fresh()), ["tailscale", "enable", "--json"])
    assert result.exit_code == 1
    assert "nothing was overwritten" in result.output
    assert store.writes == 1
    assert store.config == {"FutureField": {"anotherOwner": True}}
    assert command(fresh(), "status")["state"] == "off"
    assert saved()["tailscale"]["intent"]["enabled"]


def test_uncertain_create_reports_observed_state_without_guessing_mapping_ownership(setup):
    fresh, store = setup
    store.uncertain = True
    result = CliRunner().invoke(build(fresh()), ["tailscale", "enable", "--json"])
    assert result.exit_code == 1
    assert "outcome is unknown" in result.output
    assert store.writes == 1
    result = client(fresh()).get("/api/tailscale/status", headers=AUTH)
    assert result.json()["state"] == "blocked"
    assert result.json()["mapped"]
    assert saved()["tailscale"]["intent"]["phase"] == "enabling"
    before = copy.deepcopy(store.config)
    off = CliRunner().invoke(build(fresh()), ["tailscale", "disable", "--json"])
    assert off.exit_code == 1
    assert "ownership is unverified" in off.output
    assert not saved()["tailscale"]["intent"]["enabled"]
    assert store.config == before
    assert store.writes == 1


@pytest.mark.parametrize("operation", ["enable", "disable"])
def test_successful_vendor_acknowledgment_does_not_replace_fresh_mapping_verification(setup, operation):
    fresh, store = setup
    if operation == "disable":
        command(fresh(), "enable")
    before = copy.deepcopy(store.config)
    store.discard = True
    result = CliRunner().invoke(build(fresh()), ["tailscale", operation, "--json"])
    assert result.exit_code == 1
    assert "not verified" in result.output or "not confirmed off" in result.output
    assert store.config == before
    result = client(fresh()).get("/api/tailscale/status", headers=AUTH)
    assert result.json()["state"] == ("blocked" if operation == "disable" else "off")
    assert store.writes == (2 if operation == "disable" else 1)


def test_uncertain_owned_removal_keeps_reconciliation_state_until_absence_is_verified(setup):
    fresh, store = setup
    command(fresh(), "enable")
    store.uncertain = True
    result = CliRunner().invoke(build(fresh()), ["tailscale", "disable", "--json"])
    assert result.exit_code == 1
    assert saved()["tailscale"]["intent"]["phase"] == "disabling"
    assert not Mapping.for_node(DNS, 8737).matches(store.config)
    store.uncertain = False
    command(fresh(), "disable")
    assert saved()["tailscale"]["intent"]["phase"] == "released"
    assert saved()["hz"]["ui_allowed_origins"] == []
    assert store.writes == 2  # The fresh absence check does not resend the uncertain removal.


def test_disable_does_not_release_ownership_when_only_the_https_listener_disappears(setup):
    fresh, store = setup
    command(fresh(), "enable")
    store.dormant = True
    result = CliRunner().invoke(build(fresh()), ["tailscale", "disable", "--json"])
    assert result.exit_code == 1
    assert "not confirmed off" in result.output
    assert saved()["tailscale"]["intent"]["phase"] == "disabling"
    assert saved()["hz"]["ui_allowed_origins"] == [ORIGIN]
    status = command(fresh(), "status")
    assert status["state"] == "blocked"
    assert not status["mapped"]
    assert "incomplete" in status["message"]


def test_unapproved_or_unauthenticated_heads_cannot_change_vendor_state(setup):
    fresh, store = setup
    web_client = client(fresh())
    assert web_client.post("/api/tailscale/enable", json={}).status_code == 401
    assert web_client.post("/api/tailscale/enable", json={}, headers={**AUTH, "Origin": OTHER}).status_code == 403
    assert (
        web_client.post("/api/tailscale/enable", json={}, headers={**AUTH, "Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )
    rest = TestClient(api.build(fresh(), port=8737), base_url="http://127.0.0.1:8737")
    assert rest.post("/tailscale/enable", json={}).status_code == 404
    forged = (
        '{enabled = true, origin = "https://workstation.tail-example.ts.net", '
        "backend_port = 8737, https_port = 443, added_origin = true}"
    )
    refused = rest.post("/system/config/set", params={"key": "tailscale.intent", "value": forged})
    assert refused.status_code == 400
    assert "authenticated" in refused.text
    assert not (Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").exists()
    assert not store.connects
    assert not store.writes


def test_changed_target_is_never_removed_by_disable(setup):
    fresh, store = setup
    command(fresh(), "enable")
    mapping = Mapping.for_node(DNS, 8737)
    store.config["Web"][mapping.authority]["Handlers"]["/"] = {"Proxy": "http://127.0.0.1:3000"}
    before = copy.deepcopy(store.config)
    with pytest.raises(HeinzelError, match="target changed"):
        fresh().resolve(SharingActions).disable()
    assert store.config == before
    assert store.writes == 1
    assert saved()["tailscale"]["intent"]["enabled"]


def test_stdio_mcp_uses_the_same_mutations_and_http_mcp_excludes_private_tools(setup):
    fresh, store = setup

    async def scenario():
        async with Client(mcp.build(fresh(), transport="stdio")) as agent:
            tools = {tool.name for tool in (await agent.list_tools()).tools}
            assert {"tailscale_status", "tailscale_enable", "tailscale_disable"} <= tools
            enabled = await agent.call_tool("tailscale_enable", {})
            assert not enabled.is_error
            assert enabled.structured_content["state"] == "apply-required"
            assert enabled.structured_content["desired_enabled"]
            disabled = await agent.call_tool("tailscale_disable", {})
            assert not disabled.is_error
            assert disabled.structured_content["state"] == "off"
        async with Client(mcp.build(fresh(), transport="http")) as agent:
            tools = {tool.name for tool in (await agent.list_tools()).tools}
            assert not {"tailscale_status", "tailscale_enable", "tailscale_disable"} & tools

    anyio.run(scenario)
    assert store.writes == 2
