"""`hz system claude` end to end: the CLI lists names, the web head lists them behind its token, and the MCP and REST
heads only count (ADR 0062). The CLI and the web head switch a server off and on; an agent's heads cannot
(ADR 0146). The home directory is a fake one; nothing reaches the network."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel import Config, Settings
from heinzel.heads import api, mcp, web
from heinzel.heads.cli import build
from heinzel.modules.system.claude._switch import switch_file

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests"
SECRET = "sk-live-NEVER-SHOWN"


@pytest.fixture
def kernel(make_kernel, tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude/skills/review").mkdir(parents=True)
    (home / ".claude/skills/review/SKILL.md").write_text("---\nname: review\ndescription: Reviews code.\n---\n")
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"tickets": {"command": "npx", "env": {"API_KEY": SECRET}}}})
    )
    monkeypatch.setenv("HOME", str(home))
    return make_kernel(builtins=True)


def hz(kernel, *args: str, plain: bool = False) -> tuple[int, str]:
    result = CliRunner().invoke(build(kernel), list(args) if plain else [*args, "--json"])
    return result.exit_code, result.output


def test_the_cli_lists_names_and_never_a_value(kernel):
    code, output = hz(kernel, "system", "claude", "list")
    assert code == 0, output
    listed = json.loads(output)
    assert [(s["name"], s["env_keys"], s["auth"]) for s in listed["servers"]] == [("tickets", ["API_KEY"], "env")]
    assert [k["name"] for k in listed["skills"]] == ["review"]
    assert SECRET not in output
    code, text = hz(kernel, "system", "claude", "list", plain=True)
    assert code == 0
    assert "tickets" in text
    assert "API_KEY" in text
    assert SECRET not in text


def test_names_that_look_like_markup_cannot_break_or_restyle_the_list(kernel, tmp_path):
    home = tmp_path / "home"
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"[/x]": {"command": "npx", "env": {"[bold]K": "v"}}}})
    )
    code, text = hz(kernel, "system", "claude", "list", plain=True)
    assert code == 0, text
    assert "[/x]" in text
    assert "[bold]K" in text


def test_the_web_head_lists_behind_its_token(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    assert client.get("/api/system/claude/list").status_code == 401
    listed = client.get("/api/system/claude/list", headers={"Authorization": f"Bearer {TOKEN}"})
    assert listed.status_code == 200
    assert listed.json()["servers"][0]["name"] == "tickets"
    assert SECRET not in listed.text


def test_the_rest_head_counts_and_names_nothing(kernel):
    client = TestClient(api.build(kernel, port=8000), base_url="http://localhost:8000")
    assert client.get("/system/claude/list").status_code == 404
    summary = client.get("/system/claude/summary")
    assert summary.status_code == 200
    assert summary.json()["servers"] == {"code-user": 1}
    assert "tickets" not in summary.text


def test_the_mcp_head_counts_and_names_nothing(kernel):
    async def scenario() -> None:
        async with Client(mcp.build(kernel)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert "system_claude_summary" in names
            assert "system_claude_list" not in names
            result = await client.call_tool("system_claude_summary", {})
            assert result.structured_content is not None
            assert result.structured_content["skills"] == {"code-user": 1}
            assert "tickets" not in json.dumps(result.structured_content)

    import anyio

    anyio.run(scenario)


def test_the_cli_switches_a_server_off_and_on_and_shows_the_notice_until_acknowledged(kernel):
    code, text = hz(kernel, "system", "claude", "list", plain=True)
    assert code == 0, text
    assert "1 MCP server found in Claude is switched on" in " ".join(text.split())
    code, output = hz(kernel, "system", "claude", "disable", "code-user:tickets")
    assert code == 0, output
    assert json.loads(output) == {
        "id": "code-user:tickets",
        "name": "tickets",
        "scope": "user",
        "enabled": False,
        "changed": True,
    }
    listed = json.loads(hz(kernel, "system", "claude", "list")[1])
    assert [(s["name"], s["enabled"], s["scope"]) for s in listed["servers"]] == [("tickets", False, "user")]
    assert listed["notice"] is None
    code, text = hz(kernel, "system", "claude", "enable", "code-user:tickets", plain=True)
    assert code == 0, text
    assert "tickets is switched on" in text
    assert json.loads(hz(kernel, "system", "claude", "list")[1])["notice"]["servers"] == ["code-user:tickets"]
    assert json.loads(hz(kernel, "system", "claude", "acknowledge")[1]) == {"servers": 1}
    assert json.loads(hz(kernel, "system", "claude", "list")[1])["notice"] is None
    assert SECRET not in switch_file().read_text()


def test_the_cli_refuses_an_unknown_server(kernel):
    code, output = hz(kernel, "system", "claude", "disable", "code-user:nope", plain=True)
    assert code != 0
    assert "No MCP server has that id" in " ".join(output.split())


def test_the_web_head_switches_behind_its_token(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    body = {"server": "code-user:tickets"}
    assert client.post("/api/system/claude/disable", json=body).status_code == 401
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/api/system/claude/disable", params=body, headers=auth).status_code == 422  # body only
    switched = client.post("/api/system/claude/disable", json=body, headers=auth)
    assert switched.status_code == 200, switched.text
    assert switched.json()["enabled"] is False
    assert client.get("/api/system/claude/list", headers=auth).json()["servers"][0]["enabled"] is False
    assert client.post("/api/system/claude/enable", json=body, headers=auth).json()["enabled"] is True
    assert client.post("/api/system/claude/acknowledge", json={"servers": []}, headers=auth).json() == {"servers": 0}
    shown = {"servers": ["code-user:tickets"]}
    assert client.post("/api/system/claude/acknowledge", json=shown, headers=auth).json() == {"servers": 1}


def test_an_agent_cannot_switch_a_server_over_rest_or_mcp(kernel):
    client = TestClient(api.build(kernel, port=8000), base_url="http://localhost:8000")
    for operation in ("enable", "disable", "acknowledge"):
        assert client.post(f"/system/claude/{operation}", params={"server": "code-user:tickets"}).status_code == 404
    assert client.get("/system/claude/summary").json()["enabled"] == 1

    async def scenario() -> None:
        async with Client(mcp.build(kernel)) as mcp_client:
            names = {tool.name for tool in (await mcp_client.list_tools()).tools}
            assert not {"system_claude_enable", "system_claude_disable", "system_claude_acknowledge"} & names

    import anyio

    anyio.run(scenario)


@pytest.mark.parametrize("route", ["project-file", "config-set-over-mcp"])
def test_a_redirected_state_dir_does_not_switch_a_server_back_on(
    kernel, make_kernel, tmp_path: Path, monkeypatch, route: str
):
    assert hz(kernel, "system", "claude", "disable", "code-user:tickets")[0] == 0
    kept = switch_file()
    assert kept.is_file()
    elsewhere = tmp_path / "elsewhere"
    project = tmp_path / "cloned"
    project.mkdir()
    if route == "project-file":
        (project / "hz.toml").write_text(f'[hz]\nstate_dir = "{elsewhere}"\n')
    else:

        async def scenario() -> None:
            async with Client(mcp.build(kernel)) as client:
                arguments = {"key": "hz.state_dir", "value": str(elsewhere)}
                await client.call_tool("system_config_set", arguments)

        import anyio

        anyio.run(scenario)
    monkeypatch.delenv("HZ_STATE_DIR")  # the environment would override either route
    config = Config.load(project)
    redirected = make_kernel(builtins=True, config=config, settings_=config.settings(Settings, "hz"))
    assert redirected.settings.state_dir == elsewhere  # the setting did move: the route is live
    monkeypatch.setenv("HZ_STATE_DIR", str(elsewhere))  # what a daemon installed after the change is started with
    listed = json.loads(hz(redirected, "system", "claude", "list")[1])
    assert [(s["name"], s["enabled"]) for s in listed["servers"]] == [("tickets", False)]
    assert not list(elsewhere.rglob("servers.json"))
