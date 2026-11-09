"""Composed private CLI commands retain separate host capabilities."""

import sys
from unittest.mock import Mock

import pytest

from heinzel.heads import cli
from heinzel.heads.cli import _heinzel_tools, _mcp_lifecycle, _mcp_review

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("command", ["tools", "review", "review-install", "start", "check", "stop"])
def test_composed_routing_never_substitutes_another_host_handle(monkeypatch, command):
    handles = {
        name: object()
        for name in ("private_heinzel_tools", "host_mcp_supervisor", "host_mcp_install_review", "host_mcp_lifecycle")
    }
    tools, review, install, lifecycle = (Mock(return_value=0) for _ in range(4))
    monkeypatch.setattr(_heinzel_tools, "main", tools)
    monkeypatch.setattr(_mcp_review, "main", review)
    monkeypatch.setattr(_mcp_lifecycle, "review_install", install)
    monkeypatch.setattr(_mcp_lifecycle, "main", lifecycle)
    monkeypatch.setattr(cli, "boot", Mock(side_effect=AssertionError("Private routing must precede boot")))
    arguments = ["fixture", "--handoff", "synthetic-commitment"]
    monkeypatch.setattr(sys, "argv", ["hz", "system", "mcp", command, *arguments])
    with pytest.raises(SystemExit) as result:
        cli.main(**handles)
    assert result.value.code == 0
    if command == "tools":
        tools.assert_called_once_with(arguments, handles["private_heinzel_tools"])
    elif command == "review":
        review.assert_called_once_with(arguments, supervisor=handles["host_mcp_supervisor"])
    elif command == "review-install":
        install.assert_called_once_with(arguments, host=handles["host_mcp_install_review"])
    else:
        lifecycle.assert_called_once_with(command, arguments, host=handles["host_mcp_lifecycle"])
    assert sum(mock.call_count for mock in (tools, review, install, lifecycle)) == 1
