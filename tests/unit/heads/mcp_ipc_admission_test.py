"""Exercise the supervised MCP handoff with real, private Unix sockets."""

import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from threading import Barrier, Event

import pytest

from heinzel import HeinzelError
from heinzel.heads.mcp import _ipc_approval
from heinzel.heads.mcp._ipc_admission import McpGeneration, OwnedMcpEndpoint, PublishedMcpLease
from heinzel.heads.mcp._ipc_approval import HostMcpConnectionApprovals, PrivateMcpPlan

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


def test_locator_failure_shuts_down_socket_with_open_reader(supervised):
    endpoint, _, bridge = supervised
    with ExitStack() as retained:

        def refuse(connection):
            retained.enter_context(connection.makefile("rb"))
            msg = "Invalid locator"
            raise ValueError(msg)

        with (
            pytest.raises(HeinzelError, match="could not read the bootstrap locator"),
            endpoint.admit_next(refuse, no_publish),
        ):
            pytest.fail("MCP received a connection after locator failure")
        assert bridge.recv(1) == b""


def test_peer_and_locator_cannot_replace_reviewed_lease_publication(supervised, caplog):
    endpoint, _, bridge = supervised
    caplog.set_level(logging.INFO, logger="heinzel.mcp")
    with ExitStack() as retained:

        @contextmanager
        def refuse(peer):
            retained.enter_context(peer.connection.makefile("rb"))
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


def private_plan(deadline):
    return PrivateMcpPlan.capture(
        GENERATION, "grant-one", 2, deadline, {"targets": ["heinzel-one"], "usd_limit": 0, "result_read": True}
    )


def start_private_connection(executor, endpoint, authority):
    ready = Event()

    def initialize():
        with endpoint.admit_next(lambda _: GENERATION, authority.publish) as lease:
            connection = lease.peer.connection
            connection.settimeout(2)
            ready.set()
            try:
                return connection.recv(16)
            except OSError:
                return b""  # Revocation may close the server's descriptor before its reader observes EOF.

    return executor.submit(initialize), ready


def pending_reviews(authority, count):
    with authority._changed:
        assert authority._changed.wait_for(lambda: len(authority.pending_reviews()) == count, timeout=2)
        return authority.pending_reviews()


def test_host_proof_binds_exact_review_and_is_consumed_once(supervised):
    endpoint, path, first_bridge = supervised
    authority = HostMcpConnectionApprovals(private_plan(time.monotonic() + 60))
    with ThreadPoolExecutor(max_workers=4) as executor, socket.socket(socket.AF_UNIX) as second_bridge:
        try:
            first, first_ready = start_private_connection(executor, endpoint, authority)
            (review,) = pending_reviews(authority, 1)
            second_bridge.connect(str(path))
            second, second_ready = start_private_connection(executor, endpoint, authority)
            other = next(item for item in pending_reviews(authority, 2) if item != review)
            assert not first_ready.is_set()
            assert not second_ready.is_set()

            altered = (
                replace(review, instance_id=other.instance_id),
                replace(review, challenge=other.challenge),
                replace(review, peer_uid=review.peer_uid + 1),
                replace(review, plan=replace(review.plan, grant_id="grant-two")),
                replace(review, plan=replace(review.plan, grant_revision=3)),
                replace(review, plan=replace(review.plan, generation=McpGeneration("reg-one", 4))),
                replace(review, plan=replace(review.plan, terms_json='{"targets":["heinzel-two"]}')),
            )
            for forged in altered:
                with pytest.raises(HeinzelError, match="approval is invalid or unavailable"):
                    authority.approve(forged)

            race = Barrier(2, timeout=2)

            def try_approval():
                race.wait()
                try:
                    authority.approve(review)
                except HeinzelError:
                    return False
                return True

            issuers = [executor.submit(try_approval) for _ in range(2)]
            assert sorted(issuer.result(timeout=2) for issuer in issuers) == [False, True]
            assert first_ready.wait(2)
            first_bridge.sendall(b"initialize")
            assert first.result(timeout=2) == b"initialize"
            assert not second_ready.is_set()
            with pytest.raises(HeinzelError, match="approval is invalid or unavailable"):
                authority.approve(review)

            authority.revoke()
            with pytest.raises(HeinzelError, match="lease was not published"):
                second.result(timeout=2)
        finally:
            authority.revoke()


@pytest.mark.parametrize("invalidate", ["revoke", "expire", "disconnect"])
def test_published_proof_cannot_survive_loss_of_pending_authority(supervised, invalidate):
    endpoint, _, bridge = supervised
    now = 100.0
    authority = HostMcpConnectionApprovals(private_plan(now + 60), clock=lambda: now)
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            result, ready = start_private_connection(executor, endpoint, authority)
            (review,) = pending_reviews(authority, 1)
            # Issue the proof and invalidate the live binding before the competing publisher can acquire the lock.
            with authority._changed:
                authority.approve(review)
                if invalidate == "revoke":
                    authority.revoke()
                elif invalidate == "expire":
                    now = review.expires_at
                else:
                    bridge.close()
            with pytest.raises(HeinzelError, match="lease was not published"):
                result.result(timeout=2)
            assert not ready.is_set()
            assert not authority.pending_reviews()
            with pytest.raises(HeinzelError, match="approval is invalid or unavailable"):
                authority.approve(review)
        finally:
            authority.revoke()


def test_revoke_shuts_down_an_admitted_socket_and_fences_late_connections(supervised):
    endpoint, path, bridge = supervised
    authority = HostMcpConnectionApprovals(private_plan(time.monotonic() + 60))
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            result, ready = start_private_connection(executor, endpoint, authority)
            (review,) = pending_reviews(authority, 1)
            authority.approve(review)
            assert ready.wait(2)
            authority.revoke()
            assert bridge.recv(1) == b""
            assert result.result(timeout=2) == b""
            with socket.socket(socket.AF_UNIX) as late:
                late.settimeout(1)
                late.connect(str(path))
                with (
                    pytest.raises(HeinzelError, match="lease was not published"),
                    endpoint.admit_next(lambda _: GENERATION, authority.publish),
                ):
                    pytest.fail("A new connection reopened the revoked grant")
                assert late.recv(1) == b""
        finally:
            authority.revoke()


@pytest.mark.parametrize(("grant_deadline", "expected_delay"), [(150.0, 50.0), (2000.0, 900.0)])
def test_active_lease_expiry_obeys_the_earlier_deadline(supervised, monkeypatch, grant_deadline, expected_delay):
    endpoint, _, bridge = supervised
    authority = HostMcpConnectionApprovals(private_plan(grant_deadline), clock=lambda: 100.0)
    scheduled = []

    class Timer:
        def __init__(self, delay, callback, *, args):
            self.event = (delay, callback, args)

        def start(self):
            scheduled.append(self.event)

        def cancel(self):
            pass

    monkeypatch.setattr(_ipc_approval, "Timer", Timer)
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            result, ready = start_private_connection(executor, endpoint, authority)
            (review,) = pending_reviews(authority, 1)
            authority.approve(review)
            assert ready.wait(2)
            ((delay, expire, args),) = scheduled
            assert delay == expected_delay
            expire(*args)
            assert bridge.recv(1) == b""
            assert result.result(timeout=2) == b""
        finally:
            authority.revoke()
