"""The ordinary HTTP check proves MCP initialization without claiming process ownership."""

import importlib
import logging
import subprocess

import pytest
from typer.testing import CliRunner

from heinzel import HeinzelError
from heinzel.heads import mcp
from heinzel.heads.cli import build
from heinzel.heads.mcp._http_check import check_http

pytestmark = pytest.mark.unit


def test_probe_rejects_missing_endpoint(monkeypatch):
    monkeypatch.setattr(logging, "disable", lambda *_: None)
    probe = importlib.import_module("heinzel.heads.mcp._http_probe")
    monkeypatch.setattr(probe.sys, "argv", ["probe"])
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2


@pytest.mark.parametrize(
    "url",
    ["http://example.com:123/mcp", "http://127.0.0.1:123/mcp?token=x", "http://user@127.0.0.1:123/mcp"],
)
def test_check_refuses_nonlocal_or_ambiguous_urls(url):
    with pytest.raises(HeinzelError, match="loopback"):
        check_http(url, "hzmcp_fictional")


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:bad/mcp", "http://127.0.0.1:65536/mcp", "http://127.0.0.1:123/mcp\n"],
)
def test_check_refuses_invalid_port_or_control_characters_before_launch(monkeypatch, url):
    def unexpected_launch(*args, **kwargs):
        pytest.fail("invalid endpoint must not launch the probe")

    monkeypatch.setattr(subprocess, "run", unexpected_launch)
    with pytest.raises(HeinzelError, match="loopback"):
        check_http(url, "hzmcp_fictional")


@pytest.mark.parametrize("output", ["", "-1", "\N{ARABIC-INDIC DIGIT ONE}", "1234567890", "1\n"])
def test_probe_output_must_be_a_bounded_ascii_count(monkeypatch, output):
    def fake_probe(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=output)

    monkeypatch.setattr(subprocess, "run", fake_probe)
    with pytest.raises(HeinzelError, match="could not be authenticated"):
        check_http("http://127.0.0.1:1234/mcp", "hzmcp_fictional")


def test_chat_session_is_never_served_over_http(make_kernel, monkeypatch):
    def unexpected_serve(*args, **kwargs):
        pytest.fail("a chat session must not start an HTTP server")

    monkeypatch.setattr(mcp, "serve", unexpected_serve)
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["system", "serve", "mcp", "--transport", "streamable-http", "--chat", "fictional"],
    )
    assert result.exit_code == 2
    assert "stdio only" in result.output


@pytest.mark.parametrize("failure", [OSError("unavailable"), subprocess.TimeoutExpired("probe", 10)])
def test_probe_launch_failure_is_reported_without_child_diagnostics(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(HeinzelError, match="could not be authenticated"):
        check_http("http://127.0.0.1:1234/mcp", "hzmcp_fictional")
