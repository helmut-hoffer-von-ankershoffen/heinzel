"""One backend per machine: apps attach to the owned web service, and leftover app-spawned servers are retired.

The machine below is the Mac mini of 2026-11-21: the launchd web job on 8737, an orphan an older app left (parent
pid 1), and a server whose app still runs. A dev checkout's server and another user's are bystanders.
"""

import signal
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.modules.ui._service import WebService, WebStatus
from heinzel.modules.ui._strays import retire_strays, strays

pytestmark = [pytest.mark.unit, pytest.mark.timeout(10)]

UID = 501
TOOL = Path("/Users/someone/.local/share/uv/tools/heinzel")


@dataclass
class Machine:
    """A minimal multi-server fixture: a process table that signals change."""

    processes: dict[int, tuple[int, int, str]] = field(default_factory=dict)
    signals: list[tuple[int, int]] = field(default_factory=list)
    ignores_sigint: set[int] = field(default_factory=set)

    def ps(self) -> str:
        return "".join(f"{pid} {ppid} {uid} {command}\n" for pid, (ppid, uid, command) in self.processes.items())

    def send(self, pid: int, number: int) -> None:
        if pid not in self.processes:
            raise ProcessLookupError(pid)
        self.signals.append((pid, number))
        if pid not in self.ignores_sigint:
            del self.processes[pid]

    def alive(self, pid: int) -> bool:
        return pid in self.processes


def mini() -> Machine:
    python = f"{TOOL}/bin/python"
    return Machine(
        {
            41850: (1, UID, f"{python} -m heinzel ui serve --no-browser --port 8737 --supervised {'c' * 32}"),
            25616: (1, UID, f"{python} -I -m heinzel ui serve --port 0 --no-browser"),
            66512: (66489, UID, f"{python} -I -m heinzel ui serve --port 0 --no-browser"),
            70001: (1, UID, "/Users/someone/Code/heinzel/.venv/bin/python -m heinzel ui serve --port 0 --no-browser"),
            70002: (1, 502, f"{python} -I -m heinzel ui serve --port 0 --no-browser"),
            70003: (1, UID, f"{python} -I -m heinzel ui serve --port 0 --no-browser --extra"),
            70004: (1, UID, f"{TOOL}/bin/hz ui serve --port 0 --no-browser"),
        }
    )


def retire(machine: Machine) -> tuple[list[int], list[int]]:
    ticks = iter(range(1000))
    return retire_strays(
        TOOL,
        uid=UID,
        listing=machine.ps,
        send=machine.send,
        alive=machine.alive,
        clock=lambda: float(next(ticks)),
        sleep=lambda _seconds: None,
    )


def test_only_this_users_app_spawned_servers_of_the_owned_tool_are_candidates():
    found = {stray.pid: stray.orphaned for stray in strays(TOOL, uid=UID, own=1234, listing=mini().ps)}
    assert found == {25616: True, 66512: False, 70004: True}


def test_orphans_get_sigint_and_the_supervised_server_and_bystanders_are_untouched():
    machine = mini()
    stopped, survivors = retire(machine)
    assert (sorted(stopped), survivors) == ([25616, 70004], [])
    assert sorted(machine.signals) == [(25616, signal.SIGINT), (70004, signal.SIGINT)]
    assert set(machine.processes) == {41850, 66512, 70001, 70002, 70003}


def test_retiring_twice_is_safe_and_a_server_whose_app_quit_goes_next_time():
    machine = mini()
    retire(machine)
    assert retire(machine) == ([], [])
    machine.processes[66512] = (1, *machine.processes[66512][1:])  # the updater quit the old app
    assert retire(machine) == ([66512], [])
    assert set(machine.processes) == {41850, 70001, 70002, 70003}


def test_a_survivor_is_reported_never_killed():
    machine = mini()
    machine.ignores_sigint.add(25616)
    stopped, survivors = retire(machine)
    assert (stopped, survivors) == ([70004], [25616])
    assert all(number == signal.SIGINT for _pid, number in machine.signals)


def test_an_unreadable_process_list_retires_nothing():
    def broken() -> str:
        msg = "ps"
        raise OSError(msg)

    assert retire_strays(TOOL, uid=UID, listing=broken) == ([], [])


class _Attachable(WebService):
    """A WebService with its OS seams replaced: installing, loading and readiness are scripted."""

    def __init__(self, tmp_path: Path, statuses: list[WebStatus], *, installed: bool = True) -> None:
        super().__init__(
            tmp_path / "state",
            tmp_path / "home",
            tmp_path / "bin/hz",
            platform="darwin",
            profile={},
            prefix=tmp_path / "tool",
            clock=lambda: float(len(self.calls)),
            sleep=lambda _seconds: self.calls.append("sleep"),
        )
        self.statuses = statuses
        self.calls: list[str] = []
        if installed:
            self.marker.parent.mkdir(parents=True)
            self.marker.write_text("{}")

    def install(self, port: int, *, dry_run: bool = False):  # type: ignore[override]
        self.calls.append(f"install {port}")
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.marker.write_text("{}")

    def status(self) -> WebStatus:
        self.calls.append("status")
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def _load(self) -> None:
        self.calls.append("load")

    def open_local(self) -> str | None:
        self.calls.append("open")
        return "http://127.0.0.1:8737/#token=" + "t" * 43

    def _record(self):
        return {"port": 8737}

    def _control(self, _record, pid, command):
        self.calls.append(f"control {pid} {command.split()[0]}")
        return {"bridge": command == "bridge " + "b" * 40}


READY = WebStatus(installed=True, loaded=True, ready=True, port=8737, pid=41850)


def test_attach_opens_the_running_service_and_starts_nothing(tmp_path):
    web = _Attachable(tmp_path, [READY])
    assert web.attach(8737).startswith("http://127.0.0.1:8737/#token=")
    assert web.calls == ["status", "open"]


def test_attach_installs_the_service_on_first_launch_then_waits_for_it(tmp_path):
    starting = WebStatus(installed=True, loaded=True, ready=False, port=8737)
    web = _Attachable(tmp_path, [starting, READY], installed=False)
    web.attach(8737)
    assert web.calls == ["install 8737", "status", "sleep", "status", "open"]


def test_attach_loads_a_service_its_manager_lost_once(tmp_path):
    unloaded = WebStatus(installed=True, loaded=False, ready=False, port=8737)
    web = _Attachable(tmp_path, [unloaded, unloaded, READY])
    web.attach(8737)
    assert web.calls.count("load") == 1
    assert web.calls[-1] == "open"


def test_attach_fails_with_one_reason_and_never_serves(tmp_path):
    stuck = WebStatus(installed=True, loaded=True, ready=False, problem="The owned job has not reported ready.")
    web = _Attachable(tmp_path, [stuck])
    with pytest.raises(HeinzelError, match="not reported ready"):
        web.attach(8737)
    assert "open" not in web.calls


def test_attach_registers_the_bridge_token_over_the_control_socket(tmp_path):
    web = _Attachable(tmp_path, [READY])
    web.attach(8737, bridge="b" * 40)
    assert web.calls[-1] == "control 41850 bridge"
    with pytest.raises(HeinzelError, match="browser bridge"):
        _Attachable(tmp_path / "other", [READY]).attach(8737, bridge="x" * 40)
