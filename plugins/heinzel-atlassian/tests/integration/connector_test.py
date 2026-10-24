"""The Atlassian connector's sign-in, tools and live test through the CLI, with the fake `acli` on PATH. Offline."""

import json

import pytest
from typer.testing import CliRunner

from heinzel import Config, Kernel
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

SITE = "https://acme.atlassian.net"
BOUND = Config(
    files=(),
    data={"capabilities": {"tracker": "atlassian", "documents": "atlassian"}, "atlassian": {"site_url": SITE}},
)


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    return result.exit_code, json.loads(result.output) if result.output.startswith("{") else {"output": result.output}


def atlassian(report: dict) -> dict:
    return next(c for c in report["connectors"] if c["name"] == "atlassian")


def test_offline_it_is_not_tested_yet_and_lists_its_tools(atlassian_kernel, acli_on_path):
    code, report = hz(atlassian_kernel(BOUND), "system", "connector", "list")
    assert code == 0, report
    connector = atlassian(report)
    assert connector["state"] == "unknown"
    assert connector["login"] == {
        "signed_in": None,
        "account": "",
        "site": "acme.atlassian.net",
        "command": "acli jira auth login",
    }
    assert {(t["capability"], t["method"], t["mode"]) for t in connector["tools"]} == {
        ("tracker", "issues_updated", "read"),
        ("documents", "read", "read"),
    }
    assert acli_on_path.calls == []  # only `acli --version`, which reaches nothing


def test_the_live_test_reads_one_item_per_product_and_is_connected(atlassian_kernel, acli_on_path):
    acli_on_path.search(acli_on_path.sample("jira_workitem_search.json"))
    acli_on_path.answer("confluence space list", [{"key": "WID", "name": "Widgets"}])
    code, result = hz(atlassian_kernel(BOUND), "system", "connector", "test", "atlassian")
    assert code == 0, result
    assert result["passed"] is True
    assert [c["name"] for c in result["checks"]] == ["Jira read", "Confluence read"]
    assert result["login"]["signed_in"] is True
    assert [call.args[:3] for call in acli_on_path.calls] == [
        ["jira", "workitem", "search"],
        ["confluence", "space", "list"],
    ]


def test_logged_out_the_live_test_fails_with_the_login_command(atlassian_kernel, acli_on_path):
    acli_on_path.fail(acli_on_path.SEARCH, acli_on_path.UNAUTHORIZED)
    acli_on_path.fail("confluence space list", acli_on_path.UNAUTHORIZED)
    code, result = hz(atlassian_kernel(BOUND), "system", "connector", "test", "atlassian")
    assert code == 0, result
    assert result["passed"] is False
    assert [c["fix"] for c in result["checks"]] == ["acli jira auth login", "acli confluence auth login"]
    assert result["login"]["signed_in"] is False


def test_without_acli_the_connect_command_is_still_the_login(atlassian_kernel, no_acli):
    connector = atlassian(hz(atlassian_kernel(BOUND), "system", "connector", "list")[1])
    assert connector["state"] == "attention"
    assert connector["connect"] == ["acli jira auth login"]
