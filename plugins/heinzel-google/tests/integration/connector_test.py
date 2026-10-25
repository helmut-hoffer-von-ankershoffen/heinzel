"""The Google connector's sign-in, tools and live test through the CLI, with the fake `gws`. Offline."""

import json

import pytest
from heinzel_google import Process
from typer.testing import CliRunner

from heinzel import Config, Kernel
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

BOUND = Config(files=(), data={"capabilities": {"documents": "google", "calendar": "google", "mail": "google"}})


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    return result.exit_code, json.loads(result.output) if result.output.startswith("{") else {"output": result.output}


def google(report: dict) -> dict:
    return next(c for c in report["connectors"] if c["name"] == "google")


def test_signed_in_and_bound_it_is_connected_and_lists_its_tools(google_kernel, fake_gws):
    code, report = hz(google_kernel(BOUND), "system", "connector", "list")
    assert code == 0, report
    connector = google(report)
    assert connector["state"] == "connected"
    assert connector["login"]["signed_in"] is True
    assert connector["connect"] == ["hz setup google"]
    assert {(t["capability"], t["method"]) for t in connector["tools"]} == {
        ("documents", "read"),
        ("calendar", "events"),
        ("calendar", "free_busy"),
        ("calendar", "create_event"),
        ("calendar", "update_event"),
        ("calendar", "cancel_event"),
        ("calendar", "respond"),
        ("mail", "messages"),
        ("mail", "read"),
        ("mail", "thread"),
        ("mail", "draft"),
        ("mail", "create_draft"),
        ("mail", "send_draft"),
        ("mail", "relabel"),
    }
    assert fake_gws.api_runs == []


def test_signed_out_it_is_not_signed_in(google_kernel, fake_gws):
    fake_gws.sign_out()
    connector = google(hz(google_kernel(BOUND), "system", "connector", "list")[1])
    assert connector["state"] == "not-signed-in"
    assert connector["connect"] == ["hz setup google"]


def test_the_live_test_names_the_signed_in_account(google_kernel, fake_gws):
    fake_gws.json(("drive", "about", "get"), {"user": {"emailAddress": "ada@example.com"}})
    code, result = hz(google_kernel(BOUND), "system", "connector", "test", "google")
    assert code == 0, result
    assert result["passed"] is True
    assert result["login"]["account"] == "ada@example.com"
    assert [run.command for run in fake_gws.api_runs] == [("drive", "about", "get")]


def test_an_expired_sign_in_fails_the_live_test_with_the_setup(google_kernel, fake_gws):
    fake_gws.on(
        ("drive", "about", "get"), Process(1, json.dumps({"error": {"code": 401, "message": "expired"}}).encode())
    )
    result = hz(google_kernel(BOUND), "system", "connector", "test", "google")[1]
    assert result["passed"] is False
    assert result["login"]["signed_in"] is False
    assert result["checks"][0]["fix"] == "hz setup google"
