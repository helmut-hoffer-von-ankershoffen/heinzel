"""Managing plugins from the web app (ADR 0047): behind the session token, never on the REST head, confirmed first."""

import asyncio
import json
import os
import subprocess
import threading
import tomllib

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, Module, Progress, Settings
from heinzel.heads import api, web
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build as cli_app
from heinzel.kernel import Installation, user_config_dir
from heinzel.modules.system.plugin import PluginService, _sources

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests-that-is-long-enough"
BASE = "http://127.0.0.1:8000"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CHANGES = {
    "system_plugin_add",
    "system_plugin_remove",
    "system_plugin_enable",
    "system_plugin_disable",
    "system_plugin_history",
}


def crm() -> Module:
    module = Module("crm", "A test plugin.")

    @module.operation
    def notes() -> str:
        """Show notes."""
        return "notes"

    return module


@pytest.fixture
def kernel(make_kernel, tmp_path, monkeypatch):
    """A kernel with one plugin (heinzel-test, module `crm`) and your configuration files."""
    kernel = make_kernel(builtins=True, config=Config.load(tmp_path), crm=crm())
    service = kernel.resolve(PluginService)
    runs: list[list[str]] = []
    installed = []

    def runner(command, environment=None):
        runs.append(command)
        installed.clear()
        if "install" in command:
            source = _sources.parse(command[-1])
            name = source.name or "heinzel-crm"
            installed.append(
                {
                    "distribution": name,
                    "version": "1.1.0",
                    "modules": ["crm"],
                    "name": "crm",
                    "source_kind": source.kind,
                    "revision": "a" * 40 if source.kind == "git" else None,
                    "source_url": _sources.installed_repository_url(source.url, "a" * 40, source.subdirectory),
                }
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    service._installation = Installation(Progress(), prefix=tmp_path, uv="uv", runner=runner)
    monkeypatch.setattr(
        service._installation,
        "hz",
        lambda *args: json.dumps(installed) if args[:3] == ("system", "plugin", "list") else "[]",
    )
    monkeypatch.setattr(_sources.shutil, "which", lambda name: None)
    service.pypi = lambda url: {
        "info": {"version": "1.1.0", "summary": "CRM notes.", "author": "Ada Example"},
        "releases": {"1.1.0": [{}]},
    }
    # Each test owns its source metadata; discovery has no concrete-plugin allowlist.
    monkeypatch.setattr(PluginService, "_github_repositories", lambda *_args: [])
    service._settings = service._settings.model_copy(update={"catalog_sources": []})
    kernel.runs = runs  # type: ignore[attr-defined] - for the assertions
    return kernel


@pytest.fixture
def client(kernel):
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)


def test_the_web_head_manages_plugins_and_the_public_rest_head_only_lists(kernel):
    on_the_web = {item.name for item in exposed(kernel, web.HEAD)}
    on_rest = {item.name for item in exposed(kernel, api.HEAD)}
    assert {"system_plugin_list", "system_plugin_search", "system_plugin_operation", *CHANGES} <= on_the_web
    assert not on_rest & CHANGES
    assert "system_plugin_list" in on_rest
    assert "system_plugin_search" not in on_rest  # authenticated GitHub search can return private repositories
    assert not {"system_plugin_develop", "system_plugin_new"} & (on_the_web | on_rest)  # paths, terminals: CLI only
    rest = TestClient(api.build(kernel, port=8000), base_url=BASE)
    assert rest.post("/system/plugin/add", params={"spec": "heinzel-crm", "confirm": True}).status_code == 404
    assert kernel.runs == []


def test_adding_needs_the_token(client, kernel):
    assert client.post("/api/system/plugin/add", params={"spec": "heinzel-crm", "confirm": True}).status_code == 401
    assert client.get("/api/system/plugin/operation", params={"operation_id": "r" * 43}).status_code == 401
    assert kernel.runs == []


def test_public_rest_cannot_spawn_a_fresh_plugin_inventory_process(kernel, monkeypatch):
    calls = []
    service = kernel.resolve(PluginService)
    monkeypatch.setattr(service._installation, "hz", lambda *args: calls.append(args) or "[]")
    rest = TestClient(api.build(kernel, port=8000), base_url=BASE)
    response = rest.get("/system/plugin/list", params={"fresh": True})
    assert response.status_code >= 400
    assert calls == []
    assert kernel.runs == []


def test_web_inventory_refresh_is_authenticated_and_does_not_replace_loaded_kernel_state(client, kernel, monkeypatch):
    service = kernel.resolve(PluginService)
    cached = service.installed()
    fresh = [cached[0].model_dump(mode="json") | {"version": "9.8.7"}]
    calls = []

    def current(*args):
        calls.append(args)
        return json.dumps(fresh)

    monkeypatch.setattr(service._installation, "hz", current)
    assert client.get("/api/system/plugin/list", params={"fresh": True}).status_code == 401
    assert client.get("/api/system/plugin/list", headers=AUTH).json() == [p.model_dump(mode="json") for p in cached]
    assert calls == []
    response = client.get("/api/system/plugin/list", params={"fresh": True}, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == fresh
    assert calls == [("system", "plugin", "list", "--json")]
    assert service.installed() == cached
    assert kernel.runs == []


def test_cli_inventory_refresh_uses_the_same_explicit_read_only_contract(kernel, monkeypatch):
    service = kernel.resolve(PluginService)
    fresh = [service.installed()[0].model_dump(mode="json") | {"version": "9.8.7"}]
    calls = []

    def current(*args):
        calls.append(args)
        return json.dumps(fresh)

    monkeypatch.setattr(service._installation, "hz", current)
    response = CliRunner().invoke(cli_app(kernel), ["system", "plugin", "list", "--fresh", "--json"])
    assert response.exit_code == 0, response.output
    assert json.loads(response.output) == fresh
    assert calls == [("system", "plugin", "list", "--json")]
    assert kernel.runs == []


def test_inventory_schema_declares_optional_fresh_boolean_without_a_mutation_route(kernel):
    paths = api.build(kernel, port=8000).openapi()["paths"]
    endpoint = paths["/system/plugin/list"]
    assert set(endpoint) == {"get"}
    parameter = next(value for value in endpoint["get"]["parameters"] if value["name"] == "fresh")
    assert parameter["in"] == "query"
    assert parameter["required"] is False
    assert parameter["schema"]["type"] == "boolean"
    assert parameter["schema"]["default"] is False


def test_without_confirm_the_web_app_gets_what_to_show_and_nothing_is_installed(client, kernel):
    answer = client.post("/api/system/plugin/add", params={"spec": "heinzel-crm"}, headers=AUTH)
    assert answer.status_code == 200
    change = answer.json()
    assert (change["done"], change["restart_required"]) == (False, False)
    candidate = change["candidate"]
    assert (candidate["requirement"], candidate["version"], candidate["publisher"], candidate["source"]) == (
        "heinzel-crm==1.1.0",
        "1.1.0",
        "Ada Example",
        "pypi",
    )
    assert kernel.runs == []


def test_confirmed_the_shown_requirement_is_installed_recorded_and_a_restart_asked_for(client, kernel):
    shown = client.post("/api/system/plugin/add", params={"spec": "heinzel-crm"}, headers=AUTH).json()
    params = {
        "spec": shown["candidate"]["requirement"],
        "confirm": True,
        "operation_id": shown["operation_id"],
    }
    change = client.post("/api/system/plugin/add", params=params, headers=AUTH).json()
    assert (change["done"], change["restart_required"]) == (True, True)
    assert kernel.runs[0][-1] == "heinzel-crm==1.1.0"
    replay = client.post("/api/system/plugin/add", params=params, headers=AUTH).json()
    assert replay == change
    assert len(kernel.runs) == 1
    receipt = client.get(
        "/api/system/plugin/operation", params={"operation_id": shown["operation_id"]}, headers=AUTH
    ).json()
    assert receipt["status"] == "completed"
    assert receipt["result"] == change
    history = client.get("/api/system/plugin/history", headers=AUTH).json()
    assert [(e["action"], e["requirement"]) for e in history] == [("add", "heinzel-crm==1.1.0")]


def test_an_installer_noop_is_an_error_in_the_web_app_and_never_records_success(client, kernel, monkeypatch):
    service = kernel.resolve(PluginService)
    monkeypatch.setattr(service._installation, "hz", lambda *_args: "[]")
    shown = client.post("/api/system/plugin/add", params={"spec": "heinzel-crm"}, headers=AUTH).json()
    response = client.post(
        "/api/system/plugin/add",
        params={"spec": "heinzel-crm==1.1.0", "confirm": True, "operation_id": shown["operation_id"]},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "not present after installation" in response.json()["error"]
    assert service.history() == []
    assert service.operation(shown["operation_id"]).status == "unknown"


def test_a_confirmed_plugin_install_streams_progress_and_its_result(client, kernel):
    """The UI's long-running install path uses the authenticated operation stream, not a timed REST request."""
    # The fixture constructs PluginService before the web head replaces Progress with its request-scoped stream sink.
    kernel.resolve(PluginService)._installation._progress = kernel.resolve(Progress)
    shown = client.post("/api/system/plugin/add", params={"spec": "heinzel-crm"}, headers=AUTH).json()
    response = client.post(
        "/api/stream/system/plugin/add",
        params={
            "spec": shown["candidate"]["requirement"],
            "confirm": True,
            "operation_id": shown["operation_id"],
        },
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: task\ndata: {"text": "Adding plugin heinzel-crm"}' in response.text
    assert 'event: result\ndata: {"data": {"action": "add", "done": true' in response.text
    assert kernel.runs[0][-1] == "heinzel-crm==1.1.0"
    receipt = client.get(
        "/api/system/plugin/operation", params={"operation_id": shown["operation_id"]}, headers=AUTH
    ).json()
    assert receipt["status"] == "completed"


def test_a_new_catalog_entry_installs_through_the_add_endpoint_without_a_name_registry(client, kernel):
    service = kernel.resolve(PluginService)
    repository = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins"
    service._github_repositories = lambda _text: [{"full_name": "example/catalog", "html_url": repository}]
    service._monorepo_plugins = lambda _repository: [("heinzel-unforeseen", "plugins/unforeseen", "A new provider.")]

    def offline(_path):
        msg = "offline fixture"
        raise OSError(msg)

    service.github_api = offline
    [entry] = service.search("unforeseen").plugins
    assert entry.source == "first-party"
    shown = client.post("/api/system/plugin/add", params={"spec": entry.install_spec}, headers=AUTH).json()
    assert not shown["done"]
    assert shown["candidate"]["first_party"]
    assert kernel.runs == []
    params = {"spec": shown["candidate"]["spec"], "confirm": True, "operation_id": shown["operation_id"]}
    answer = client.post("/api/system/plugin/add", params=params, headers=AUTH)
    assert answer.status_code == 200, answer.text
    assert answer.json()["done"]
    assert any("heinzel-unforeseen" in part for part in kernel.runs[0])


@pytest.mark.parametrize(
    "spec",
    [
        "/home/ada/heinzel-crm",
        "git+ssh://git@github.com/acme-example/heinzel-crm",
        "http://example.org/heinzel-crm",
        "https://ada:t0ken@github.com/acme-example/heinzel-crm",
        "https://example.org/heinzel_crm-1.0-py3-none-any.whl",
        "requests",
    ],
)
def test_sources_other_than_pypi_and_https_git_are_refused(client, kernel, spec):
    answer = client.post("/api/system/plugin/add", params={"spec": spec, "confirm": True}, headers=AUTH)
    assert answer.status_code == 400
    assert "takes a catalog short name" in answer.json()["error"]
    assert kernel.runs == []


def test_disabling_and_enabling_a_plugin_writes_your_user_file(client, kernel, make_kernel, tmp_path):
    change = client.post("/api/system/plugin/disable", params={"distribution": "heinzel-test"}, headers=AUTH).json()
    assert (change["done"], change["restart_required"], change["next"]) == (
        True,
        True,
        ["hz system plugin enable heinzel-test"],
    )
    user_file = user_config_dir() / "config.toml"
    assert tomllib.loads(user_file.read_text(encoding="utf-8"))["hz"]["disabled_modules"] == ["crm"]

    config = Config.load(tmp_path)
    restarted = make_kernel(builtins=True, config=config, settings_=config.settings(Settings, "hz"), crm=crm())
    [plugin] = [p for p in restarted.resolve(PluginService).installed() if p.distribution == "heinzel-test"]
    assert (plugin.enabled, plugin.modules) == (False, ["crm"])

    restarted.resolve(PluginService)._installation.hz = lambda *args: "[]"  # type: ignore[method-assign]
    restarted.resolve(PluginService).enable("heinzel-test")
    assert "disabled_modules" not in tomllib.loads(user_file.read_text(encoding="utf-8")).get("hz", {})


def test_a_plugin_disabled_elsewhere_says_where_to_look(make_kernel, tmp_path, monkeypatch):
    settings = Settings(disabled_modules=frozenset({"crm"}))
    kernel = make_kernel(builtins=True, config=Config.load(tmp_path), settings_=settings, crm=crm())
    with pytest.raises(Exception, match="hz system config show"):
        kernel.resolve(PluginService).enable("heinzel-test")


def test_restart_answers_202_and_restarts_only_behind_the_token(kernel):
    restarts = []
    client = TestClient(
        web.build(kernel, port=8000, token=TOKEN, static=None, restart=lambda: restarts.append(1)), base_url=BASE
    )
    assert client.post("/api/restart").status_code == 401
    answer = client.post("/api/restart", headers=AUTH)
    assert (answer.status_code, answer.json()["restarting"], restarts) == (202, True, [1])
    assert "/restart" in client.get("/api/openapi.json", headers=AUTH).json()["paths"]


def test_a_server_that_cannot_restart_says_so(client):
    answer = client.post("/api/restart", headers=AUTH)
    assert answer.status_code == 400
    assert "can't restart itself" in answer.json()["error"]


def test_a_restart_hands_the_port_and_the_token_to_the_process_it_becomes(make_kernel, monkeypatch):
    executed: list[tuple[str, list[str], dict[str, str]]] = []
    urls: list[str] = []
    started = threading.Event()
    servers: list[uvicorn.Server] = []

    class Recorded(uvicorn.Server):
        def __init__(self, config: uvicorn.Config) -> None:
            super().__init__(config)
            servers.append(self)

    def announce(url: str) -> None:
        urls.append(url)
        started.set()

    monkeypatch.setattr(uvicorn, "Server", Recorded)
    monkeypatch.setattr(web.sys, "argv", ["hz", "ui", "--port", "0"])
    kernel = make_kernel(builtins=True)
    first = threading.Thread(
        target=web.serve,
        args=(kernel,),
        kwargs={"announce": announce, "static": None, "browser": False, "execute": lambda *a: executed.append(a)},
    )
    first.start()
    assert started.wait(10)
    token = urls[0].rsplit("#token=", 1)[1]
    port = int(urls[0].split(":")[2].split("/")[0])
    auth = {"Authorization": f"Bearer {token}"}
    assert httpx.post(f"http://127.0.0.1:{port}/api/restart", headers=auth, timeout=10).status_code == 202
    first.join(10)
    [(executable, command, environment)] = executed
    assert command == [executable, "-m", "heinzel", "ui", "--port", "0"]
    fd, handed = environment[web.RESUME].split(":", 1)
    assert (fd.isdigit(), handed) == (True, token)

    # The process it becomes: the same port, the same token, no new browser tab.
    started.clear()
    monkeypatch.setenv(web.RESUME, environment[web.RESUME])
    opened = []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    second = threading.Thread(target=web.serve, args=(kernel,), kwargs={"announce": announce, "static": None})
    second.start()
    try:
        assert started.wait(10)
        assert urls[1] == urls[0]
        assert web.RESUME not in os.environ
        assert httpx.get(f"http://127.0.0.1:{port}/api/system/plugin/list", headers=auth, timeout=10).status_code == 200
        assert opened == []
    finally:
        servers[-1].should_exit = True
        second.join(10)


def test_the_cli_shows_what_it_would_install_and_how_to_confirm(kernel):
    from typer.testing import CliRunner

    from heinzel.heads.cli import build

    app = build(kernel)
    shown = CliRunner().invoke(app, ["system", "plugin", "add", "heinzel-crm"])
    assert shown.exit_code == 0, shown.output
    assert "Ada Example" in shown.output
    assert "hz system plugin add heinzel-crm==1.1.0 --confirm" in shown.output
    assert kernel.runs == []
    done = CliRunner().invoke(app, ["system", "plugin", "add", "heinzel-crm==1.1.0", "--confirm"])
    assert done.exit_code == 0, done.output
    assert "Installed heinzel-crm 1.1.0." in done.output
    listed = CliRunner().invoke(app, ["system", "plugin", "list"])
    assert "heinzel-test" in listed.output
    assert "enabled" in listed.output


def test_on_a_terminal_the_preview_asks_and_installs_on_yes_and_json_stays_two_step(kernel, monkeypatch):
    from typer.testing import CliRunner

    from heinzel.heads.cli import _command, build

    monkeypatch.setattr(_command, "has_terminal", lambda: True)
    app = build(kernel)
    declined = CliRunner().invoke(app, ["system", "plugin", "add", "heinzel-crm"], input="n\n")
    assert declined.exit_code == 0, declined.output
    assert "Install heinzel-crm 1.1.0 now? [y/N]" in declined.output
    assert "Not installed." in declined.output
    assert "hz system plugin add heinzel-crm==1.1.0 --confirm" in declined.output
    assert kernel.runs == []
    accepted = CliRunner().invoke(app, ["system", "plugin", "add", "heinzel-crm"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert "✓ Installed heinzel-crm 1.1.0." in accepted.output
    assert "Restart hz ui to load it (or click Restart in the app)." in accepted.output
    assert len(kernel.runs) == 1
    scripted = CliRunner().invoke(app, ["system", "plugin", "add", "heinzel-crm", "--json"], input="y\n")
    assert '"done": false' in scripted.output
    assert len(kernel.runs) == 1


def test_off_a_terminal_the_preview_says_it_installed_nothing(kernel):
    from typer.testing import CliRunner

    from heinzel.heads.cli import build

    shown = CliRunner().invoke(build(kernel), ["system", "plugin", "add", "heinzel-crm"])
    assert "Not installed yet: this was a preview." in shown.output
    assert (
        "To install: hz system plugin add heinzel-crm==1.1.0 --confirm"
        in shown.output.replace("\n", " ").replace("  ", " ")
        or "--confirm" in shown.output
    )


def test_mcp_over_stdio_manages_plugins_and_unauthenticated_http_mcp_cannot(kernel):
    from heinzel.heads import mcp

    stdio = {tool.name for tool in asyncio.run(mcp.build(kernel, "stdio").list_tools())}
    http = {tool.name for tool in asyncio.run(mcp.build(kernel, "streamable-http").list_tools())}
    assert stdio >= CHANGES
    assert not CHANGES & http
    assert {"system_plugin_list", "heinzel_wake"} <= http
    assert "system_plugin_search" not in http


def test_a_handed_over_socket_that_isnt_a_loopback_listener_starts_a_fresh_session(monkeypatch):
    import socket

    other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # bound nowhere, not listening
    try:
        sock, token, resumed = web._session(f"{other.fileno()}:{TOKEN}", 0)
        try:
            assert (resumed, token == TOKEN) == (False, False)
            assert sock.fileno() != other.fileno()
        finally:
            sock.close()
        assert other.fileno() != -1  # left to whoever handed it over
    finally:
        other.close()
    sock, token, resumed = web._session(f"999999:{TOKEN}", 0)
    sock.close()
    assert not resumed
