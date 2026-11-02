"""Busy UI ports get a visible bounded wait without taking another server's socket."""

import errno
import socket

import pytest

from heinzel import HeinzelError
from heinzel.heads.web._ports import wait_for_port

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def pause(self, seconds):
        self.now += seconds


def test_recently_busy_port_is_retried_with_one_explanation():
    clock, messages, attempts = Clock(), [], []
    with socket.socket() as reserved:

        def bind():
            attempts.append(clock())
            if len(attempts) < 3:
                raise OSError(errno.EADDRINUSE, "busy")
            return reserved

        assert wait_for_port(bind, 8737, messages.append, clock=clock, pause=clock.pause) is reserved
    assert attempts == [0, 1, 2]
    assert len(messages) == 1
    assert "30 seconds" in messages[0]
    assert "Ctrl-C" in messages[0]


def test_wait_is_bounded_and_explains_a_still_running_instance():
    clock, messages = Clock(), []

    def bind():
        raise OSError(errno.EADDRINUSE, "busy")

    with pytest.raises(HeinzelError, match=r"still in use after 3 seconds.*--port 0"):
        wait_for_port(bind, 8737, messages.append, clock=clock, pause=clock.pause, timeout=3)
    assert clock() == 3
    assert len(messages) == 1


@pytest.mark.parametrize(("port", "code"), [(0, errno.EADDRINUSE), (8737, errno.EACCES)])
def test_other_bind_failures_do_not_wait(port, code):
    clock, messages = Clock(), []

    def bind():
        raise OSError(code, "cannot bind")

    with pytest.raises(OSError, match="cannot bind") as caught:
        wait_for_port(bind, port, messages.append, clock=clock, pause=clock.pause)
    assert caught.value.errno == code
    assert clock() == 0
    assert messages == []


def test_ctrl_c_cancels_the_wait():
    def bind():
        raise OSError(errno.EADDRINUSE, "busy")

    def interrupt(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        wait_for_port(bind, 8737, lambda _text: None, pause=interrupt)


def test_a_port_another_server_listens_on_stays_busy():
    from heinzel.heads.web import free_socket

    with free_socket() as other:
        port = other.getsockname()[1]
        with pytest.raises(OSError, match="in use") as raised:
            free_socket("127.0.0.1", port)
        assert raised.value.errno == errno.EADDRINUSE


def test_a_port_whose_last_server_closed_its_connections_binds_again():
    from heinzel.heads.web import free_socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # A fixed port, as the daemon binds it: on Linux a lingering connection only lets the port be rebound when the
    # socket it came from also had SO_REUSEADDR.
    server = free_socket("127.0.0.1", port)
    with socket.create_connection(("127.0.0.1", port)) as client:
        served, _ = server.accept()
        served.close()  # the closing side keeps the port in TIME_WAIT
        client.recv(1)
    server.close()
    with free_socket("127.0.0.1", port) as again:
        assert again.getsockname()[1] == port


def test_windows_never_gets_the_reuse_option(monkeypatch):
    from heinzel.heads import web

    options = []
    real = socket.socket.setsockopt
    monkeypatch.setattr(web.sys, "platform", "win32")
    monkeypatch.setattr(socket.socket, "setsockopt", lambda self, *a: options.append(a) or real(self, *a))
    with web.free_socket("127.0.0.1", 0) as _, socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with web.free_socket("127.0.0.1", port):
        pass
    assert not [o for o in options if o[1] == socket.SO_REUSEADDR]


def test_a_second_bind_to_a_listening_port_says_the_port_is_in_use():
    from heinzel.heads.web import free_socket

    with free_socket() as other:
        port = other.getsockname()[1]
        with pytest.raises(HeinzelError, match="still in use"):
            wait_for_port(lambda: free_socket("127.0.0.1", port), port, lambda _: None, timeout=0)
