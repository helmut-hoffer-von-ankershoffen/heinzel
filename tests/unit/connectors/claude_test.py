"""`claude_mcp_servers`: Claude's MCP servers as a public, read-only API (ADR 0159, 0062, 0146). Fake homes."""

import json
from pathlib import Path

import pytest

from heinzel.connectors import claude_mcp_servers
from heinzel.modules.system.claude import ClaudeService
from heinzel.modules.system.claude._switch import SwitchStore, switch_file

pytestmark = pytest.mark.unit

SECRET = "sk-live-NEVER-SHOWN"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    project = root / "Code" / "demo"
    project.mkdir(parents=True)
    servers = {
        "gateway-mail": {"command": "/opt/acme/bin/acme-mcp", "args": ["launch", "mail", SECRET], "env": {"T": SECRET}},
        "mine": {"command": "hz", "args": ["system", "serve", "mcp"]},
    }
    (root / ".claude.json").write_text(json.dumps({"mcpServers": servers, "projects": {str(project): {}}}))
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"tickets": {"command": "npx", "args": ["t"]}}}))
    return root


def test_lists_servers_by_name_and_shape_never_a_secret_nor_hz_itself(home: Path):
    servers = claude_mcp_servers(home)
    assert [(s.name, s.source, s.command) for s in servers] == [
        ("tickets", "code-project", "npx"),
        ("gateway-mail", "code-user", "acme-mcp"),
    ]
    assert servers[1].env_keys == ["T"]
    assert SECRET not in json.dumps([s.model_dump() for s in servers])


def test_filters_by_source_and_leaves_switched_off_servers_out_unless_asked(home: Path):
    assert [s.name for s in claude_mcp_servers(home, sources=["code-user"])] == ["gateway-mail"]
    off = next(s for s in claude_mcp_servers(home) if s.name == "tickets")
    ClaudeService(home, SwitchStore(switch_file())).switch(off.id, on=False)
    assert [s.name for s in claude_mcp_servers(home)] == ["gateway-mail"]
    everything = claude_mcp_servers(home, include_disabled=True)
    assert [(s.name, s.enabled) for s in everything] == [("tickets", False), ("gateway-mail", True)]


def test_an_empty_home_has_none(tmp_path: Path):
    assert claude_mcp_servers(tmp_path) == []
