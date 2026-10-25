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
