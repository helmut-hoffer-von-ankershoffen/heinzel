"""The Zoom connector's state, tools and live test through the CLI, reading a test-only mailbox. Offline."""

import json

import pytest
from typer.testing import CliRunner

from heinzel import Config, Kernel
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

BOUND = Config(files=(), data={"capabilities": {"mail": "inbox", "meetings": "zoom"}})


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    return result.exit_code, json.loads(result.output) if result.output.startswith("{") else {"output": result.output}


def zoom(report: dict) -> dict:
    return next(c for c in report["connectors"] if c["name"] == "zoom")


def test_it_has_no_sign_in_of_its_own_so_it_is_untested_until_its_live_test(zoom_kernel, mailbox):
    code, report = hz(zoom_kernel(BOUND), "system", "connector", "list")
    assert code == 0, report
    connector = zoom(report)
    assert (connector["state"], connector["login"], connector["connect"]) == ("unknown", None, [])
    assert {t["method"] for t in connector["tools"]} == {"ended", "transcripts", "transcript"}
    assert mailbox.queries == []


def test_the_live_test_lists_summary_emails_through_the_bound_mail_adapter(zoom_kernel, mailbox, emails):
    mailbox.holding(emails("sync"))
    code, result = hz(zoom_kernel(BOUND), "system", "connector", "test", "zoom")
    assert code == 0, result
    assert result["passed"] is True
    assert len(mailbox.queries) == 1
    assert mailbox.reads == []  # listed, never opened


def test_without_a_mail_adapter_the_live_test_fails_and_says_to_bind_mail(zoom_kernel, mailbox):
    off = Config(files=(), data={"capabilities": {"mail": "none", "meetings": "zoom"}})
    code, result = hz(zoom_kernel(off), "system", "connector", "test", "zoom")
    assert code == 0, result
    assert result["passed"] is False
    assert "capabilities.mail" in json.dumps(result["checks"])
    assert mailbox.queries == []
