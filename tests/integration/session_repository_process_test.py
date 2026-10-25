"""Separate processes reserve one identity and accept short controls while a main turn owns its writer."""

import multiprocessing
import traceback
from pathlib import Path

import pytest

from heinzel import CurrentUser
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.integration


def service(root):
    current = CurrentUser(Path(root) / "identity")
    repository = PersonalSessionRepository(current)
    crud = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
    return current, repository, crud


def create(root, ready, start, results):
    current, _repository, crud = service(root)
    ready.put("ready")
    assert start.wait(10)
    try:
        with current.local("cli"):
            results.put(("created", crud.new_chat("same-request", "Shared helper").session_id))
    except Exception as error:  # noqa: BLE001 - report a child-process failure instead of hiding it behind a queue timeout
        results.put(("error", type(error).__name__, traceback.format_exc()))


def hold_main(root, session_id, ready, release):
    current, repository, _crud = service(root)
    with current.local("daemon"), repository.main_writer(session_id):
        ready.set()
        assert release.wait(10)


def accept_control(root, session_id, results):
    current, _repository, crud = service(root)
    with current.local("cli"):
        result = crud.profile(session_id, "edit", 1, name="Changed while running")
        crud.accept(session_id, MessageRequest(request_id="follow-up", text="Next input"))
        results.put((result.name, crud.snapshot(session_id).messages[0].id))


def test_simultaneous_retries_reserve_and_initialize_one_durable_identity(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    processes = [context.Process(target=create, args=(str(tmp_path), ready, start, results)) for _ in range(3)]
    for process in processes:
        process.start()
    try:
        for _ in processes:
            assert ready.get(timeout=10)
        start.set()
        records = [results.get(timeout=10) for _ in processes]
        failures = [record[2] for record in records if record[0] != "created"]
        if failures:
            pytest.fail("\n".join(failures))
        assert len({record[1] for record in records}) == 1
    finally:
        start.set()
        for process in processes:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(10)
    assert all(process.exitcode == 0 for process in processes)
    current, _repository, crud = service(tmp_path)
    with current.local("cli"):
        assert len(crud.listing().sessions) == 1


def test_main_turn_in_another_process_does_not_block_acknowledged_profile_or_inbox(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    current, _repository, crud = service(tmp_path)
    with current.local("cli"):
        session_id = crud.new_chat("new").session_id
    context = multiprocessing.get_context("spawn")
    ready, release, results = context.Event(), context.Event(), context.Queue()
    turn = context.Process(target=hold_main, args=(str(tmp_path), session_id, ready, release))
    control = context.Process(target=accept_control, args=(str(tmp_path), session_id, results))
    turn.start()
    try:
        assert ready.wait(10)
        control.start()
        assert results.get(timeout=10) == ("Changed while running", "follow-up")
        assert turn.is_alive()
        with current.local("cli"):
            assert crud.snapshot(session_id).name == "Changed while running"
    finally:
        release.set()
        for process in (turn, control):
            if process.pid is not None:
                process.join(10)
                if process.is_alive():
                    process.terminate()
                    process.join(10)
    assert turn.exitcode == control.exitcode == 0
