"""Exercise the supervised MCP handoff with real, private Unix sockets."""

import logging
import socket
from contextlib import contextmanager

import pytest

from heinzel import HeinzelError
from heinzel.heads.mcp._ipc_admission import McpGeneration, OwnedMcpEndpoint, PublishedMcpLease

pytestmark = pytest.mark.unit
GENERATION = McpGeneration("reg-one", 3)


def bind(listener, path):
    listener.bind(str(path))
    path.chmod(0o600)
    listener.listen()
    listener.settimeout(1)


@pytest.fixture
def supervised(tmp_path_factory):
    directory = tmp_path_factory.mktemp("ipc").resolve()
    directory.chmod(0o700)
    path = directory / "m"
    with socket.socket(socket.AF_UNIX) as listener, socket.socket(socket.AF_UNIX) as bridge:
        bind(listener, path)
        endpoint = OwnedMcpEndpoint(listener, path, GENERATION)
        bridge.settimeout(1)
        bridge.connect(str(path))
        yield endpoint, path, bridge


def read_locator(connection):
    connection.settimeout(1)
    assert connection.recv(1) == b"L"
    return GENERATION


def no_publish(_):
    pytest.fail("the unverified connection reached the lease publisher")


def test_protocol_only_sees_original_socket_after_publication(supervised, caplog):
    endpoint, _, bridge = supervised
    active = []
    caplog.set_level(logging.INFO, logger="heinzel.mcp")

    @contextmanager
    def publish(peer):
        lease = PublishedMcpLease("active-1", GENERATION, peer)
        active.append(lease)
        try:
            yield lease
        finally:
            assert peer.connection.fileno() == -1
            active.remove(lease)

    bridge.sendall(b"L")
    with endpoint.admit_next(read_locator, publish) as lease:
        assert active == [lease]
        bridge.sendall(b"initialize")
        assert lease.peer.connection.recv(10) == b"initialize"

    assert active == []
    assert bridge.recv(1) == b""
    records = [record for record in caplog.records if record.message == "mcp.ipc.admit_peer"]
    assert [record.phase for record in records] == ["start", "end"]
    assert records[-1].outcome == "ok"


@pytest.mark.parametrize("locator", [McpGeneration("reg-one", 2), McpGeneration("reg-two", 3)])
def test_stale_or_swapped_locator_never_reaches_publisher(supervised, locator):
    endpoint, _, bridge = supervised
    with (
        pytest.raises(HeinzelError, match="locator does not match"),
        endpoint.admit_next(lambda _: locator, no_publish),
    ):
        pytest.fail("MCP received a connection for the wrong registration generation")
    assert bridge.recv(1) == b""


def test_peer_and_locator_cannot_replace_reviewed_lease_publication(supervised, caplog):
    endpoint, _, bridge = supervised
    caplog.set_level(logging.INFO, logger="heinzel.mcp")

    @contextmanager
    def refuse(_):
        msg = "Internal review details"
        raise HeinzelError(msg)
        yield  # pragma: no cover - publication refuses before handing off a lease

    with (
        pytest.raises(HeinzelError, match="lease was not published"),
        endpoint.admit_next(lambda _: GENERATION, refuse),
    ):
        pytest.fail("MCP received an owner peer without an authorized lease")
    assert bridge.recv(1) == b""
    records = [record for record in caplog.records if record.message == "mcp.ipc.admit_peer"]
    assert [record.phase for record in records] == ["start", "end"]
    assert records[-1].outcome == "error"
    assert records[-1].error == "Local MCP admission lease was not published"


@pytest.mark.parametrize("disconnect", [False, True], ids=["early-initialize", "disconnect"])
def test_refuses_peer_that_does_not_wait_for_admission(supervised, disconnect):
    endpoint, _, bridge = supervised
    if not disconnect:
        bridge.sendall(b"initialize")

    def locator(_):
        if disconnect:
            bridge.close()  # The disconnect happens after peer verification and before publication.
        return GENERATION

    with (
        pytest.raises(HeinzelError, match="disconnected or sent data before admission"),
        endpoint.admit_next(locator, no_publish),
    ):
        pytest.fail("MCP received a connection before the admission exchange finished")


def test_refuses_a_lease_from_another_connection(supervised):
    endpoint, path, bridge = supervised

    @contextmanager
    def publish(peer):
        yield PublishedMcpLease("first", GENERATION, peer)

    with endpoint.admit_next(lambda _: GENERATION, publish) as first:
        assert first.peer.connection.fileno() >= 0
    assert bridge.recv(1) == b""

    @contextmanager
    def replay(_):
        yield first

    with socket.socket(socket.AF_UNIX) as next_bridge:
        next_bridge.connect(str(path))
        with (
            pytest.raises(HeinzelError, match="lease does not match the current generation and peer"),
            endpoint.admit_next(lambda _: GENERATION, replay),
        ):
            pytest.fail("MCP accepted a previous connection's lease")


def test_publisher_must_bind_current_generation_and_live_peer(supervised):
    endpoint, _, bridge = supervised

    @contextmanager
    def generation_changed(peer):
        try:
            yield PublishedMcpLease("wrong-generation", McpGeneration("reg-one", 4), peer)
        finally:
            assert peer.connection.fileno() == -1

    with (
        pytest.raises(HeinzelError, match="lease does not match the current generation and peer"),
        endpoint.admit_next(lambda _: GENERATION, generation_changed),
    ):
        pytest.fail("MCP initialized after the generation changed")
    assert bridge.recv(1) == b""


@pytest.mark.parametrize("revoke", [False, True], ids=["client-disconnected", "server-revoked"])
def test_closed_stream_during_publication_releases_lease_without_handoff(supervised, revoke):
    endpoint, _, bridge = supervised
    released = []

    @contextmanager
    def disconnect(peer):
        if revoke:
            peer.connection.close()
        else:
            bridge.close()
        try:
            yield PublishedMcpLease("disconnected", GENERATION, peer)
        finally:
            assert peer.connection.fileno() == -1
            released.append(True)

    with (
        pytest.raises(HeinzelError, match="disconnected or sent data before admission"),
        endpoint.admit_next(lambda _: GENERATION, disconnect),
    ):
        pytest.fail("MCP initialized after disconnect")
    assert released == [True]


@pytest.mark.parametrize("change", ["node", "directory", "symlink", "mode"])
def test_rechecks_endpoint_ownership_after_lease_publication(supervised, change):
    endpoint, path, bridge = supervised
    with socket.socket(socket.AF_UNIX) as replacement:

        @contextmanager
        def publish(peer):
            if change == "node":
                path.rename(path.with_name("old"))
                bind(replacement, path)
            elif change == "directory":
                path.parent.rename(path.parent.with_name("old-ipc"))
                path.parent.mkdir(mode=0o700)
                bind(replacement, path)
            elif change == "symlink":
                original = path.rename(path.with_name("old"))
                path.symlink_to(original)
            else:
                path.chmod(0o660)
            yield PublishedMcpLease("changed-endpoint", GENERATION, peer)

        with (
            pytest.raises(HeinzelError, match="Local MCP endpoint"),
            endpoint.admit_next(lambda _: GENERATION, publish),
        ):
            pytest.fail("MCP initialized using a changed endpoint")
    assert bridge.recv(1) == b""
