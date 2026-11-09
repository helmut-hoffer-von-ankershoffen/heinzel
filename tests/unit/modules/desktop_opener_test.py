import errno
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from heinzel.modules.system import _desktop_opener

pytestmark = pytest.mark.unit

COMMAND = ["/fixture/opener"]


class Child:
    def __init__(self):
        self.exited = Event()
        self.reaped = Event()

    def poll(self):
        if self.exited.is_set():
            self.reaped.set()
            return 0
        return None


@pytest.fixture
def pool(monkeypatch):
    reaper = _desktop_opener._OpenerReaper(capacity=2)
    children = [Child() for _ in range(3)]
    popen = Mock(side_effect=children)
    workers = []

    def worker(**kwargs):
        thread = Thread(**kwargs)
        workers.append(thread)
        return thread

    monkeypatch.setattr(_desktop_opener, "Thread", worker)
    monkeypatch.setattr(subprocess, "Popen", popen)
    yield reaper, children, popen, workers
    for child in children:
        child.exited.set()
    for thread in workers:
        thread.join(timeout=1)
        assert not thread.is_alive()


def test_one_worker_refuses_saturation_before_spawn(pool):
    reaper, _, popen, workers = pool
    reaper.launch(COMMAND, detached=False)
    reaper.launch(COMMAND, detached=True)

    with pytest.raises(OSError, match="Too many desktop openers") as error:
        reaper.launch(COMMAND, detached=False)

    assert error.value.errno == errno.EAGAIN
    assert popen.call_count == 2
    assert len(workers) == 1
    assert workers[0].is_alive()
    assert popen.call_args_list[0].kwargs["stdin"] is None
    assert popen.call_args_list[0].kwargs["start_new_session"] is False
    assert popen.call_args_list[1].kwargs["stdin"] == subprocess.DEVNULL
    assert popen.call_args_list[1].kwargs["start_new_session"] is True


def test_a_later_exit_is_reaped_while_the_first_child_stays_open(pool):
    reaper, children, popen, workers = pool
    reaper.launch(COMMAND, detached=False)
    reaper.launch(COMMAND, detached=False)

    children[1].exited.set()

    assert children[1].reaped.wait(1)
    assert not children[0].reaped.is_set()
    reaper.launch(COMMAND, detached=False)
    assert popen.call_count == 3
    assert len(workers) == 1


def test_worker_start_failure_refuses_spawn_and_allows_retry(pool, monkeypatch):
    reaper, _, popen, workers = pool
    with monkeypatch.context() as patch:
        worker = Mock()
        worker.start.side_effect = RuntimeError("no thread capacity")
        patch.setattr(_desktop_opener, "Thread", Mock(return_value=worker))
        with pytest.raises(OSError, match="Unable to start desktop opener cleanup") as error:
            reaper.launch(COMMAND, detached=False)
        assert error.value.errno == errno.EAGAIN
        popen.assert_not_called()

    reaper.launch(COMMAND, detached=False)
    popen.assert_called_once()
    assert len(workers) == 1


def test_spawn_failure_returns_the_reserved_slot(pool):
    reaper, children, popen, _ = pool
    error = FileNotFoundError(errno.ENOENT, "fixture missing")
    popen.side_effect = [error, *children]
    with pytest.raises(FileNotFoundError) as failure:
        reaper.launch(COMMAND, detached=False)
    assert failure.value is error

    reaper.launch(COMMAND, detached=False)
    reaper.launch(COMMAND, detached=False)
    assert popen.call_count == 3


def test_in_flight_spawn_also_reserves_capacity(pool):
    reaper, children, popen, _ = pool
    entered = Event()
    finish = Event()

    def spawn(command, **_):
        if command == COMMAND:
            return children[0]
        entered.set()
        assert finish.wait(2)
        return children[1]

    popen.side_effect = spawn
    reaper.launch(COMMAND, detached=False)
    with ThreadPoolExecutor(max_workers=1) as callers:
        pending = callers.submit(reaper.launch, ["/fixture/in-flight"], detached=False)
        try:
            assert entered.wait(1)
            with pytest.raises(OSError, match="Too many desktop openers"):
                reaper.launch(COMMAND, detached=False)
            assert popen.call_count == 2
        finally:
            finish.set()
        pending.result(timeout=1)
