"""After an update the owned web server restarts into the installed code, and only its successor's report counts."""

import os

import pytest

from heinzel import HeinzelError
from heinzel.modules.ui._service import READY_SECONDS, WebRestart, WebService, WebStatus

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def ready(instance: str, version: str, pid: int = 4242) -> WebStatus:
    return WebStatus(installed=True, loaded=True, ready=True, pid=pid, port=8737, instance=instance, version=version)


def service(tmp_path, statuses, *, restart=None):
    clock = Clock()
    web = WebService(
        tmp_path / "state",
        tmp_path / "home",
        tmp_path / "bin/hz",
        platform="darwin",
        profile={},
        clock=clock,
        sleep=clock.sleep,
    )
    seen = iter(statuses)
    last: list[WebStatus] = []

    def status() -> WebStatus:
        last[:] = [next(seen, last[0] if last else statuses[-1])]
        return last[0]

    restarts: list[int] = []

    def restarted() -> WebRestart:
        restarts.append(1)
        if restart is not None:
            raise restart
        return WebRestart(requested=True, retiring_instance="a" * 32, message="")

    web.status = status  # type: ignore[method-assign]
    web.restart = restarted  # type: ignore[method-assign]
    return web, restarts, clock


def test_a_server_already_on_the_target_core_still_restarts_so_new_plugins_load(tmp_path):
    web, restarts, _ = service(tmp_path, [ready("a" * 32, "0.9.26"), ready("b" * 32, "0.9.26")])
    result = web.refresh("0.9.26")
    assert restarts == [1]
    assert (result.status, result.version) == ("verified", "0.9.26")


def test_the_old_server_that_kept_running_old_code_is_restarted_and_its_successor_verified(tmp_path):
    old, waiting, new = ready("a" * 32, "0.9.22"), ready("a" * 32, "0.9.22"), ready("b" * 32, "0.9.26")
    web, restarts, clock = service(tmp_path, [old, waiting, waiting, new])
    result = web.refresh("0.9.26")
    assert (restarts, result.status, result.version) == ([1], "verified", "0.9.26")
    assert 0 < clock.now < READY_SECONDS


def test_a_successor_on_another_version_fails_and_names_the_fix(tmp_path):
    web, _, _ = service(tmp_path, [ready("a" * 32, "0.9.22"), ready("b" * 32, "0.9.22")])
    result = web.refresh("0.9.26")
    assert (result.status, result.version) == ("failed", "0.9.22")
    assert "hz ui restart" in result.detail


def test_no_successor_within_the_readiness_bound_stays_pending(tmp_path):
    web, _, clock = service(tmp_path, [ready("a" * 32, "0.9.22")])
    result = web.refresh("0.9.26")
    assert result.status == "pending"
    assert clock.now == pytest.approx(READY_SECONDS)
    assert "hz ui status" in result.detail


def test_a_refused_restart_is_a_failure_not_an_exception(tmp_path):
    web, _, _ = service(tmp_path, [ready("a" * 32, "0.9.22")], restart=HeinzelError("mutation pending"))
    result = web.refresh("0.9.26")
    assert result.status == "failed"
    assert "mutation pending" in result.detail


def test_the_server_running_the_update_is_never_restarted_from_inside(tmp_path):
    web, restarts, _ = service(tmp_path, [ready("a" * 32, "0.9.22", pid=os.getpid())])
    assert web.refresh("0.9.26").status == "self"
    assert restarts == []


def test_nothing_installed_is_nothing_to_refresh(tmp_path):
    web, restarts, _ = service(tmp_path, [WebStatus(installed=False, loaded=False, ready=False)])
    assert web.refresh("0.9.26").status == "not-installed"
    assert restarts == []


def _restartable(tmp_path, repair=None):
    prefix = tmp_path / "tool"
    prefix.mkdir()
    web = WebService(
        tmp_path / "state",
        tmp_path / "home",
        tmp_path / "bin/hz",
        platform="darwin",
        profile={},
        prefix=prefix,
        repair=repair,
    )
    controls: list[str] = []
    web._transition_needed = lambda: False  # type: ignore[method-assign]
    web._record = lambda: {"port": 8737}  # type: ignore[method-assign]
    web._manager = lambda _record: (True, 4242)  # type: ignore[method-assign]

    def control(_record, _pid, verb):
        controls.append(verb)
        return {"instance": "a" * 32}

    web._control = control  # type: ignore[method-assign]
    return web, prefix / ".heinzel-startup-pending", controls


def test_restart_repairs_a_marker_a_put_back_left_and_restarts(tmp_path):
    """The Mini, 0.9.28: CLI and daemon ran the new version, web kept the old one, `hz ui restart` refused."""
    removed: list[int] = []

    def repair() -> bool:
        removed.append(1)
        marker.unlink()
        return True

    web, marker, controls = _restartable(tmp_path, repair)
    marker.touch()
    assert web.restart().requested
    assert (removed, controls) == ([1], ["restart"])


def test_restart_names_the_way_out_when_the_marker_must_stay(tmp_path):
    web, marker, controls = _restartable(tmp_path, lambda: False)
    marker.touch()
    with pytest.raises(HeinzelError, match="hz system update"):
        web.restart()
    assert controls == []
    assert marker.exists()


def test_restart_never_repairs_past_another_unfinished_change(tmp_path):
    repairs: list[int] = []
    web, marker, controls = _restartable(tmp_path, lambda: bool(repairs.append(1)))
    marker.touch()
    gate = tmp_path / "state/daemon/mutation-pending.json"
    gate.parent.mkdir(parents=True)
    gate.write_text("{}")
    with pytest.raises(HeinzelError, match="requires completion or recovery"):
        web.restart()
    assert (repairs, controls) == ([], [])
