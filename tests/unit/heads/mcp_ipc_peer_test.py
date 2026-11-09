"""Managed MCP peer verification exercises the real Unix socket credential boundary."""

import logging
import os
import socket

import pytest

from heinzel import HeinzelError
from heinzel.heads.mcp._ipc_peer import require_owner_peer

pytestmark = pytest.mark.unit


def test_verifies_the_owner_on_both_ends_of_a_connected_unix_stream(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)  # A relative path fits macOS's Unix socket path limit.
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bridge,
        caplog.at_level(logging.INFO, logger="heinzel.mcp"),
    ):
        listener.bind("registration.sock")
        listener.listen()
        bridge.connect("registration.sock")
        accepted, _ = listener.accept()
        with accepted:
            require_owner_peer(accepted, owner_uid=os.geteuid())
            require_owner_peer(bridge, owner_uid=os.geteuid())

    records = [record for record in caplog.records if record.message == "mcp.ipc.verify_peer"]
    assert [record.phase for record in records] == ["start", "end", "start", "end"]
    assert [record.outcome for record in records[1::2]] == ["ok", "ok"]


def test_wrong_owner_uid_refuses_without_logging_credentials(caplog):
    accepted, bridge = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with (
        accepted,
        bridge,
        caplog.at_level(logging.INFO, logger="heinzel.mcp"),
        pytest.raises(HeinzelError, match="verified owner peer"),
    ):
        require_owner_peer(accepted, owner_uid=os.geteuid() + 1)

    records = [record for record in caplog.records if record.message == "mcp.ipc.verify_peer"]
    assert [record.phase for record in records] == ["start", "end"]
    assert records[-1].outcome == "error"
    assert not any("uid" in key for record in records for key in vars(record))


@pytest.mark.parametrize("family", [socket.AF_INET, socket.AF_UNIX])
def test_unconnected_stream_refuses(family):
    with (
        socket.socket(family, socket.SOCK_STREAM) as connection,
        pytest.raises(HeinzelError, match="connected Unix stream"),
    ):
        require_owner_peer(connection, owner_uid=os.geteuid())


def test_connected_unix_datagram_refuses():
    accepted, bridge = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    with accepted, bridge, pytest.raises(HeinzelError, match="connected Unix stream"):
        require_owner_peer(accepted, owner_uid=os.geteuid())
