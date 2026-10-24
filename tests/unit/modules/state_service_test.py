"""The state module's service: what is noted, when the daemon pushes, how failures back off (`system.state`).

Git is faked here; `tests/integration/state_test.py` pushes to real bare repositories.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from heinzel import Config, HeinzelError, Status
from heinzel.heads._presence import mark_presence
from heinzel.kernel import CommandFailed, Presence
from heinzel.modules.system.state import LocalPresence, StateService, StateSettings
from heinzel.modules.system.state._service import _runner, os_family
from heinzel.modules.system.state._sink import scrub

pytestmark = pytest.mark.unit

URL = "https://github.com/mira-example/heinzel-state-mira"
NOW = datetime.fromisoformat("2026-10-24T09:00:00+02:00")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class Git:
    """A git that fails every command with `stderr`, recording what it was asked."""

    def __init__(self, stderr: str = "fatal: unable to access 'https://mira:hunter2@github.com/': timed out") -> None:
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, command, cwd):
        self.calls.append(list(command))
        raise CommandFailed(command, self.stderr)


def service(  # noqa: PLR0913 - the seams a test sets
    tmp_path: Path, *, url: str | None = URL, user: str | None = "mira", git=None, clock=None, sleeps=None
):
    waits = sleeps if sleeps is not None else []
    return StateService(
        StateSettings(personal=url),
        user,
        tmp_path / "state-dir",
        tmp_path / "data-dir",
        run=git or Git(),
        clock=clock or Clock(),
        sleep=waits.append,
        jitter=lambda: 42.0,
        version="0.4.0",
    )


def adoption(tmp_path: Path) -> list[dict]:
    segment = tmp_path / "state-dir" / "state" / "adoption" / "2026-10.jsonl"
    return [json.loads(line) for line in segment.read_text().splitlines()]


@pytest.mark.parametrize(("url", "user", "reason"), [(None, "mira", "no personal"), (URL, None, "no handle")])
def test_without_a_repository_or_a_handle_the_daemon_skips_but_still_notes_the_version(tmp_path, url, user, reason):
    git = Git()

    result = service(tmp_path, url=url, user=user, git=git).scheduled()

    assert reason in result["skipped"]
    assert git.calls == []
    assert [line["kind"] for line in adoption(tmp_path)] == ["installed"]


def test_a_scheduled_push_waits_a_random_moment_first(tmp_path):
    sleeps: list[float] = []

    with pytest.raises(HeinzelError):
        service(tmp_path, sleeps=sleeps).scheduled()

    assert sleeps == [42.0]


def test_a_failed_push_is_recorded_without_the_password_in_the_url(tmp_path):
    state = service(tmp_path)

    with pytest.raises(HeinzelError, match="Couldn't push") as caught:
        state.push()

    status = state.status()
    assert "hunter2" not in str(caught.value)
    assert "hunter2" not in (status.error or "")
    assert status.failures == 1
    assert status.last_push is None
    assert "hunter2" not in (tmp_path / "state-dir" / "state" / "push.json").read_text()


def test_failures_back_off_doubling_from_the_schedule(tmp_path):
    clock = Clock()
    git = Git()
    state = service(tmp_path, git=git, clock=clock)
    with pytest.raises(HeinzelError):
        state.scheduled()
    clock.now += timedelta(minutes=15)
    with pytest.raises(HeinzelError):
        state.scheduled()
    tried = len(git.calls)

    clock.now += timedelta(minutes=15)
    waiting = state.scheduled()
    clock.now += timedelta(minutes=15)
    with pytest.raises(HeinzelError):
        state.scheduled()

    assert "backing off after 2" in waiting["skipped"]
    assert len(git.calls) > tried


def test_a_rejected_push_stops_the_sink_until_reset(tmp_path):
    git = Git("! [rejected]        hz/mira/abcd1234 -> hz/mira/abcd1234 (fetch first)")
    state = service(tmp_path, git=git)

    with pytest.raises(HeinzelError, match="HZS001"):
        state.push()
    calls = len(git.calls)
    skipped = state.scheduled()
    with pytest.raises(HeinzelError, match="Pushing stopped"):
        state.push()
    status = state.status()
    health = state.health()[0]
    after = state.reset()

    assert skipped == {"skipped": "pushing stopped; see hz system state status"}
    assert len(git.calls) == calls
    assert status.refused
    assert "reset" in status.next[0]
    assert health.status is Status.DOWN
    assert not after.refused
    assert after.failures == 0


def test_a_damaged_local_line_is_never_pushed(tmp_path):
    state = service(tmp_path)
    segment = tmp_path / "state-dir" / "state" / "presence" / "2026-10.jsonl"
    segment.parent.mkdir(parents=True)
    segment.write_text('{"v":1,"date":"2026-10-24","hz":"0.4.0","kind":"active","path":"/Users/mira"}\n')
    git = Git()
    state = service(tmp_path, git=git)

    with pytest.raises(HeinzelError, match=r"Not pushed: presence/2026-10.jsonl:1"):
        state.push()
    assert git.calls == []


def test_presence_is_noted_for_a_person_never_for_the_daemons_own_ticks(tmp_path):
    state = service(tmp_path)

    noted = [state.note_presence("system.daemon"), state.note_presence("graph"), state.note_presence("team")]

    assert noted == [False, True, False]


def test_presence_never_costs_an_operation(tmp_path, make_kernel):
    class Broken(Presence):
        def mark(self, module: str) -> None:
            raise RuntimeError(module)

    kernel = make_kernel()
    kernel.bind(Presence, Broken())

    mark_presence(kernel, "graph")
    mark_presence(make_kernel(), "graph")  # nothing provides it
    blocked = tmp_path / "state-dir"
    blocked.write_text("a file where the directory should be")
    LocalPresence(service(tmp_path)).mark("graph")


def test_the_doctor_says_where_state_goes_and_when_it_went_silent(tmp_path):
    clock = Clock()
    state = service(tmp_path, clock=clock)
    nowhere = service(tmp_path, url=None).health()[0]
    nobody = service(tmp_path, user=None).health()[0]
    fresh = state.health()[0]
    with pytest.raises(HeinzelError):
        state.push()
    clock.now += timedelta(days=3)

    silent = state.health()[0]

    assert (nowhere.status, nowhere.detail) == (Status.UP, "none named; nothing leaves this machine")
    assert nobody.status is Status.DEGRADED
    assert fresh.status is Status.UP
    assert fresh.detail.startswith("hz/mira/")
    assert silent.status is Status.DEGRADED
    assert "no push since 2026-10-24" in silent.detail


def test_status_says_what_to_do_next(tmp_path):
    state = service(tmp_path)
    state.note_presence("graph")

    nowhere = service(tmp_path, url=None).status()
    nobody = service(tmp_path, user=None).status()
    pending = state.status()

    assert "system.state.personal" in nowhere.next[0]
    assert nobody.branch is None
    assert "report.user" in nobody.next[0]
    assert pending.branch == f"hz/mira/{pending.machine}"
    assert [(s.name, s.lines, s.pending) for s in pending.streams] == [("presence", 1, 1), ("adoption", 0, 0)]
    assert "hz system state push" in pending.next[0]


def test_pushing_without_a_repository_or_handle_says_how_to_name_one(tmp_path):
    with pytest.raises(HeinzelError, match=r"system\.state\.personal"):
        service(tmp_path, url=None).push()
    with pytest.raises(HeinzelError, match=r"report\.user"):
        service(tmp_path, user=None).push()


@pytest.mark.parametrize(
    "url", ["https://mira:ghp_secret@github.com/x/y", "ext::sh -c touch% /tmp/pwned", "-uhttps://github.com/x/y"]
)
def test_the_personal_repository_must_be_one_git_may_push_to_safely(url):
    with pytest.raises(ValidationError):
        StateSettings(personal=url)


def test_a_project_file_cant_name_where_your_machine_pushes(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text(f'[system.state]\npersonal = "{URL}"\n')
    monkeypatch.chdir(project)

    assert Config.load(project).settings(StateSettings, "system.state").personal is None


def test_commits_carry_the_handle_never_the_persons_git_identity():
    pinned = dict(_runner("mira").pinned)

    assert pinned["GIT_AUTHOR_NAME"] == pinned["GIT_COMMITTER_NAME"] == "mira"
    assert pinned["GIT_AUTHOR_EMAIL"] == pinned["GIT_COMMITTER_EMAIL"] == "mira@hz.invalid"


def test_errors_lose_the_user_info_of_every_url():
    assert (
        scrub("fatal: https://mira:tok@github.com/x and ssh://git@host/y")
        == "fatal: https://github.com/x and ssh://host/y"
    )


@pytest.mark.parametrize(("platform", "family"), [("darwin", "macos"), ("linux", "linux"), ("win32", "other")])
def test_an_adoption_line_names_only_the_operating_systems_family(platform, family):
    assert os_family(platform) == family
