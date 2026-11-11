"""Owner-only local control refuses unsafe files and unbounded or ambiguous messages."""

import errno
import json
import socket

import pytest

from heinzel import HeinzelError
from heinzel.kernel import PrivateControl, private_directory, private_read, private_socket_directory, private_write

pytestmark = pytest.mark.integration


@pytest.fixture
def endpoint(tmp_path):
    directory = private_socket_directory(tmp_path)
    private_directory(directory)
    yield directory / "fixture.sock"
    directory.rmdir()


def test_private_files_reject_links_shared_inodes_and_oversized_content(tmp_path):
    directory = tmp_path / "owned"
    private_directory(directory)
    target = directory / "target"
    private_write(target, "original")
    linked = directory / "linked"
    linked.symlink_to(target)
    with pytest.raises(OSError, match=r"Too many levels|symbolic"):
        private_write(linked, "changed")
    assert target.read_text() == "original"
    linked.unlink()
    linked.hardlink_to(target)
    with pytest.raises(HeinzelError, match="owned private regular file"):
        private_read(target)
    linked.unlink()
    private_write(target, "x" * (16 * 1024 + 1))
    with pytest.raises(HeinzelError, match="owned private regular file"):
        private_read(target)


@pytest.mark.parametrize(
    "message",
    [b'{"command":"status"}\n{"command":"open"}\n', b"x" * (16 * 1024 + 1) + b"\n"],
    ids=["two-messages", "oversized"],
)
def test_control_rejects_ambiguous_or_oversized_messages_before_calling_handler(endpoint, message):
    calls = []
    control = PrivateControl(endpoint, lambda command: calls.append(command) or {"ok": True})
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(2)
            client.connect(str(endpoint))
            try:
                client.sendall(message)
                client.shutdown(socket.SHUT_WR)
                response = client.recv(1)
            except (BrokenPipeError, ConnectionResetError):
                response = b""
            except OSError as exc:
                if exc.errno != errno.ENOTCONN:
                    raise
                response = b""
            assert response == b""
        assert calls == []
    finally:
        control.close()


def test_control_never_returns_an_oversized_response_or_its_contents_in_an_error(endpoint):
    secret = "private-fragment-token"
    control = PrivateControl(endpoint, lambda _: {"value": secret * 1024})
    try:
        with pytest.raises(ValueError, match="Invalid local message") as failure:
            PrivateControl.request(endpoint, "status")
        assert secret not in str(failure.value)
    finally:
        control.close()


def test_control_request_schema_refuses_unknown_fields_without_calling_handler(endpoint):
    calls = []
    control = PrivateControl(endpoint, lambda command: calls.append(command) or {"ok": True})
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(2)
            client.connect(str(endpoint))
            client.sendall(json.dumps({"command": "open", "unexpected": True}).encode() + b"\n")
            assert client.recv(1) == b""
        assert calls == []
    finally:
        control.close()
