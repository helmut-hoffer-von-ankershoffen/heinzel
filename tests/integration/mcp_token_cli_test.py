"""`hz system serve mcp-token` and the HTTP MCP head's start checks through the CLI (ADR 0142)."""

import json

import pytest
from typer.testing import CliRunner

from heinzel.heads._mcp_tokens import tokens_for_user
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration


def hz(kernel, *args: str):
    return CliRunner().invoke(build(kernel), list(args))


@pytest.fixture
def kernel(make_kernel):
    return make_kernel(builtins=True)


def test_a_token_is_printed_once_listed_without_its_value_and_revoked(kernel):
    made = hz(kernel, "system", "serve", "mcp-token", "create", "--actor", "codex-desktop", "--scope", "write")
    assert made.exit_code == 0, made.output
    token = made.stdout.strip().splitlines()[-1]
    assert token.startswith("hzmcp_")
    [record] = tokens_for_user().tokens()
    assert (record.actor, record.scope) == ("codex-desktop", "write")
    assert tokens_for_user().check(token) is not None

    listed = hz(kernel, "system", "serve", "mcp-token", "list")
    assert record.id in listed.output
    assert "codex-desktop" in listed.output
    assert token not in listed.output

    revoked = hz(kernel, "system", "serve", "mcp-token", "revoke", record.id)
    assert revoked.exit_code == 0, revoked.output
    assert tokens_for_user().check(token) is None
    assert hz(kernel, "system", "serve", "mcp-token", "revoke", record.id).exit_code == 1


def test_an_invalid_actor_makes_no_token(kernel):
    result = hz(kernel, "system", "serve", "mcp-token", "create", "--actor", "Not Plain")
    assert result.exit_code == 1
    assert tokens_for_user().tokens() == []


def test_serving_http_without_a_token_does_not_start(kernel):
    result = hz(kernel, "system", "serve", "mcp", "--transport", "streamable-http")
    assert result.exit_code != 0
    assert "needs a token" in result.output


def test_serving_http_on_a_shared_interface_needs_expose(kernel):
    hz(kernel, "system", "serve", "mcp-token", "create", "--actor", "codex")
    result = hz(kernel, "system", "serve", "mcp", "--transport", "streamable-http", "--host", "0.0.0.0")  # noqa: S104
    assert result.exit_code != 0
    assert "--expose" in result.output


def test_token_commands_print_json_without_the_digest(kernel):
    made = hz(kernel, "system", "serve", "mcp-token", "create", "--actor", "codex", "--json")
    assert made.exit_code == 0, made.output
    created = json.loads(made.stdout)
    assert created["token"].startswith("hzmcp_")
    assert (created["actor"], created["scope"]) == ("codex", "read")
    assert "sha256" not in created

    listed = json.loads(hz(kernel, "system", "serve", "mcp-token", "list", "--json").stdout)
    assert [entry["id"] for entry in listed] == [created["id"]]
    assert created["token"] not in json.dumps(listed)
    assert "sha256" not in listed[0]

    revoked = hz(kernel, "system", "serve", "mcp-token", "revoke", created["id"], "--json")
    assert revoked.exit_code == 0, revoked.output
    assert [entry["id"] for entry in json.loads(revoked.stdout)["revoked"]] == [created["id"]]
    assert json.loads(hz(kernel, "system", "serve", "mcp-token", "list", "--json").stdout) == []
