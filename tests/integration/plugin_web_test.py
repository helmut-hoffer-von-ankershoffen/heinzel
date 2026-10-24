"""Managing plugins from the web app (ADR 0047): behind the session token, never on the REST head, confirmed first."""

import asyncio
import os
import subprocess
import threading
import tomllib

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from heinzel import Config, Module, Progress, Settings
from heinzel.heads import api, web
from heinzel.heads._exposure import exposed
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

    def runner(command, environment=None):
        runs.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    service._installation = Installation(Progress(), prefix=tmp_path, uv="uv", runner=runner)
    monkeypatch.setattr(service._installation, "hz", lambda *args: "[]")
    monkeypatch.setattr(_sources.shutil, "which", lambda name: None)
    service.pypi = lambda url: {
        "info": {"version": "1.1.0", "summary": "CRM notes.", "author": "Ada Example"},
        "releases": {"1.1.0": [{}]},
    }
    kernel.runs = runs  # type: ignore[attr-defined] - for the assertions
    return kernel


@pytest.fixture
def client(kernel):
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)


def test_the_web_head_manages_plugins_and_the_rest_head_only_lists_and_searches(kernel):
    on_the_web = {item.name for item in exposed(kernel, web.HEAD)}
    on_rest = {item.name for item in exposed(kernel, api.HEAD)}
    assert {"system_plugin_list", "system_plugin_search", *CHANGES} <= on_the_web
    assert not on_rest & CHANGES
    assert {"system_plugin_list", "system_plugin_search"} <= on_rest
    assert not {"system_plugin_develop", "system_plugin_new"} & (on_the_web | on_rest)  # paths, terminals: CLI only
    rest = TestClient(api.build(kernel, port=8000), base_url=BASE)
    assert rest.post("/system/plugin/add", params={"spec": "heinzel-crm", "confirm": True}).status_code == 404
    assert kernel.runs == []


def test_adding_needs_the_token(client, kernel):
    assert client.post("/api/system/plugin/add", params={"spec": "heinzel-crm", "confirm": True}).status_code == 401
    assert kernel.runs == []


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
    params = {"spec": shown["candidate"]["requirement"], "confirm": True}
    change = client.post("/api/system/plugin/add", params=params, headers=AUTH).json()
    assert (change["done"], change["restart_required"]) == (True, True)
    assert kernel.runs[0][-1] == "heinzel-crm==1.1.0"
    history = client.get("/api/system/plugin/history", headers=AUTH).json()
    assert [(e["action"], e["requirement"]) for e in history] == [("add", "heinzel-crm==1.1.0")]


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
    assert "takes a first-party name" in answer.json()["error"]
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
    assert "Added heinzel-crm." in done.output
    listed = CliRunner().invoke(app, ["system", "plugin", "list"])
    assert "heinzel-test" in listed.output
    assert "enabled" in listed.output


def test_mcp_over_stdio_manages_plugins_and_unauthenticated_http_mcp_cannot(kernel):
    from heinzel.heads import mcp

    stdio = {tool.name for tool in asyncio.run(mcp.build(kernel, "stdio").list_tools())}
    http = {tool.name for tool in asyncio.run(mcp.build(kernel, "streamable-http").list_tools())}
    assert stdio >= CHANGES
    assert not CHANGES & http
    assert {"system_plugin_list", "system_plugin_search", "heinzel_wake"} <= http  # the rest stays as it was


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
