"""`hz system claude` end to end: the CLI lists names, the web head lists them behind its token, and the MCP and REST
heads only count (ADR 0062). The home directory is a fake one; nothing reaches the network."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel.heads import api, mcp, web
from heinzel.heads.cli import build

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
