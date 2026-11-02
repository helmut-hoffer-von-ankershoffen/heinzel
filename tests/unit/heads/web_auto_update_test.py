"""Automatic updates: the server's schedule, postpone, idle wait, apply and failure (ADR 0116)."""

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from heinzel import Settings
from heinzel.heads import web
from heinzel.heads.web import _auto_update as auto
from heinzel.heads.web._auto_update import Activity, AutoUpdater, Plan
from heinzel.kernel import (
    CorePin,
    Installation,
    OperationLet,
    PluginRelease,
    PluginReleases,
    ReleaseFacts,
    ReleaseSource,
)

pytestmark = pytest.mark.unit
START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
TOKEN = "t" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# The plan alone -------------------------------------------------------------------------------------------------------


def pending(now=START):
    plan = Plan()
    auto.found(plan, "9.9.9", {"heinzel-example": "2.0.0"}, now)
    return plan


def test_news_opens_a_30_second_countdown_once_idle_and_applies_when_it_ends():
    plan = pending()
    assert plan.window_at == START
    assert auto.step(plan, START, busy=False) is False
    assert plan.apply_at == START + timedelta(seconds=30)
    assert auto.step(plan, START + timedelta(seconds=29), busy=False) is False
    assert auto.step(plan, START + timedelta(seconds=30), busy=False) is True


def test_no_countdown_starts_while_busy_and_work_during_one_cancels_it_until_idle():
    plan = pending()
    assert auto.step(plan, START, busy=True) is False
    assert plan.apply_at is None
    auto.step(plan, START + timedelta(seconds=5), busy=False)
    assert plan.apply_at == START + timedelta(seconds=35)
    assert auto.step(plan, START + timedelta(seconds=40), busy=True) is False  # a chat answer started: no update
    assert plan.apply_at is None
    assert auto.step(plan, START + timedelta(minutes=10), busy=True) is False
    auto.step(plan, START + timedelta(minutes=11), busy=False)  # idle again: a fresh countdown warns every window
    assert plan.apply_at == START + timedelta(minutes=11, seconds=30)


def test_postpone_moves_the_apply_time_an_hour_and_can_be_repeated():
    plan = pending()
    auto.step(plan, START, busy=False)
    due = plan.apply_at
    auto.postpone(plan, START + timedelta(seconds=10))
    assert plan.apply_at is None
    assert plan.postponements == 1
    assert plan.window_at == due + timedelta(hours=1) - timedelta(seconds=30)
    assert auto.step(plan, due + timedelta(minutes=59), busy=False) is False
    assert plan.apply_at is None
    auto.step(plan, plan.window_at, busy=False)  # the popup returns for the next window
    assert plan.apply_at == due + timedelta(hours=1)
    auto.postpone(plan, plan.window_at)
    assert plan.postponements == 2
    auto.step(plan, plan.window_at, busy=False)
    assert plan.apply_at == due + timedelta(hours=2)


def test_the_same_news_keeps_its_times_and_nothing_newer_clears_the_plan():
    plan = pending()
    auto.step(plan, START, busy=False)
    auto.postpone(plan, START)
    window = plan.window_at
    auto.found(plan, "9.9.9", {"heinzel-example": "2.0.0"}, START + timedelta(minutes=30))
    assert (plan.window_at, plan.postponements) == (window, 1)
    auto.found(plan, None, {}, START + timedelta(hours=2))
    assert not plan.pending
    assert (plan.window_at, plan.apply_at, plan.postponements) == (None, None, 0)
    assert auto.step(plan, START + timedelta(hours=3), busy=False) is False


def test_a_failure_waits_six_hours_before_the_next_countdown_and_says_so_once():
    plan = pending()
    auto.failed(plan, auto.failure_message("1.0.0"), START)
    assert "keeps running" in (plan.message or "")
    assert auto.step(plan, START + timedelta(hours=5, minutes=59), busy=False) is False
    assert plan.apply_at is None
    auto.step(plan, START + timedelta(hours=6), busy=False)
    assert plan.apply_at == START + timedelta(hours=6, seconds=30)


def test_news_after_a_failure_waits_for_the_retry_time():
    plan = Plan()
    auto.failed(plan, "no", START)
    auto.found(plan, "9.9.9", {}, START + timedelta(hours=1))
    assert plan.window_at == START + timedelta(hours=6)


def test_the_plan_survives_the_state_file_but_a_countdown_does_not():
    plan = pending()
    auto.step(plan, START, busy=False)
    auto.postpone(plan, START)
    auto.step(plan, plan.window_at, busy=False)
    loaded = Plan.from_json(json.loads(json.dumps(plan.to_json())), fresh=True)  # as a server starts
    assert (loaded.core, loaded.plugins, loaded.postponements) == ("9.9.9", {"heinzel-example": "2.0.0"}, 1)
    assert loaded.window_at == plan.window_at
    assert loaded.apply_at is None
    assert Plan.from_json(plan.to_json()).apply_at == plan.apply_at  # another server's countdown is kept
    assert Plan.from_json("garbage") == Plan()
    assert Plan.from_json({"postponements": -3, "window_at": "not a time"}) == Plan()


def test_only_requests_that_change_state_count_as_work():
    assert auto.counts_as_work("POST", "/api/heinzel/answer-chat")
    assert not auto.counts_as_work("GET", "/api/heinzel/sessions")
    assert not auto.counts_as_work("POST", "/api/auto-update/postpone")
    activity = Activity()
    activity.enter()
    assert activity.busy
    activity.leave()
    activity.leave()
    assert not activity.busy


# The server's updater -------------------------------------------------------------------------------------------------


COMMIT = "c" * 40


class Releases(ReleaseSource):
    def __init__(self, latest="9.9.9", published=START - timedelta(days=7), prerelease=False):
        self.latest, self.published, self.prerelease = latest, published, prerelease

    def latest_version(self, timeout):
        return self.latest

    def release_facts(self, version, timeout):
        return ReleaseFacts(version, self.published, COMMIT, prerelease=self.prerelease)


class Plugins(PluginReleases):
    def __init__(self, uploaded=START - timedelta(days=7), core_uploaded=START - timedelta(days=7)):
        self.uploaded, self.core_uploaded = uploaded, core_uploaded

    def newer_releases(self, timeout):
        return {"heinzel-example": PluginRelease("2.0.0", self.uploaded)}

    def release(self, name, version, timeout):
        return PluginRelease(version, self.core_uploaded) if name == "heinzel" else None


class Receipt(Installation):
    def __init__(self, pin, prefix):
        self.pin = pin
        self.prefix = prefix

    def core_pin(self):
        return self.pin


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


class Result:
    """What `system.apply_announced` answers."""

    def __init__(self, updated, after, *, refused=False, message=None):
        self.updated, self.after, self.refused, self.message = updated, after, refused, message


def updater(  # noqa: PLR0913 - one option per condition
    make_kernel,
    tmp_path,
    *,
    on=True,
    channel="stable",
    outcome=None,
    restart=True,
    pin="release",
    checks=True,
):
    settings = Settings(state_dir=tmp_path / "state", cache_dir=tmp_path / "cache")
    kernel = make_kernel(builtins=True, settings_=settings)
    kernel.bind(
        Installation, Receipt(CorePin(pin, "heinzel", ref="v0.1.0", git="https://github.com/org/heinzel"), tmp_path)
    )
    kernel.bind(ReleaseSource, Releases())
    kernel.bind(PluginReleases, Plugins())
    clock, calls = Clock(), {"restart": 0, "update": 0}
    applied: list[dict] = []

    def update(news):
        calls["update"] += 1
        applied.append(news)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome or Result(updated=True, after="9.9.9")

    def restarted():
        calls["restart"] += 1

    made = AutoUpdater(
        kernel,
        restart=restarted if restart else None,
        clock=clock,
        rng=random.Random(1),  # noqa: S311 - jitter in a test, not a secret
        version="1.0.0",
        settings=lambda: settings.model_copy(
            update={"auto_update": on, "update_channel": channel, "update_check": checks}
        ),
        update=update,
        development=None,  # an installed hz; the development facts have their own tests
    )
    made.applied = applied
    return made, clock, calls, kernel


def advance(made, clock, seconds):
    clock.now += timedelta(seconds=seconds)
    made.tick()


def test_the_server_checks_within_minutes_of_start_then_counts_down_and_applies(make_kernel, tmp_path):
    made, clock, calls, _ = updater(make_kernel, tmp_path)
    assert made.status().phase == "idle"
    advance(made, clock, 5 * 60)  # the first check: one to five minutes after start
    status = made.status()
    assert (status.phase, status.core, status.plugins, status.seconds_left) == (
        "countdown",
        "9.9.9",
        {"heinzel-example": "2.0.0"},
        30,
    )
    advance(made, clock, 29)
    assert calls == {"restart": 0, "update": 0}
    advance(made, clock, 1)
    assert calls == {"restart": 1, "update": 1}
    assert made.status().phase == "applying"
    saved = json.loads((tmp_path / "state" / "web" / "auto-update.json").read_text())
    assert saved["restarting_to"] == "9.9.9"


def test_a_postpone_from_any_window_holds_for_every_window_and_a_reload(make_kernel, tmp_path):
    made, clock, calls, kernel = updater(make_kernel, tmp_path)
    advance(made, clock, 5 * 60)
    due = made.status().apply_at
    status = made.postpone()
    assert (status.phase, status.postponements, status.seconds_left) == ("waiting", 1, None)
    assert status.next_window_at == due + timedelta(minutes=59, seconds=30)
    advance(made, clock, 60)
    assert calls["update"] == 0
    # A second server object, as after a reload of the plan from the state file, sees the same postponement.
    again = AutoUpdater(
        kernel, restart=lambda: None, development=None, clock=clock, version="1.0.0", settings=made._read_settings
    )
    assert again.plan.postponements == 1
    assert again.plan.window_at == status.next_window_at


def test_nothing_happens_while_off_or_on_another_channel_or_pinned(make_kernel, tmp_path):
    for options, words in [
        ({"on": False}, "off"),
        ({"channel": "edge"}, "stable channel only"),
        ({"restart": False}, "can't restart"),
        ({"pin": "commit"}, "pinned"),
    ]:
        made, clock, calls, _ = updater(make_kernel, tmp_path / str(len(words)), **options)
        advance(made, clock, 10 * 60)
        status = made.status()
        assert status.phase == "off"
        assert words in (status.reason or "")
        assert calls["update"] == 0


def test_a_daemon_job_or_a_running_request_holds_off_the_countdown(make_kernel, tmp_path):
    made, clock, calls, _ = updater(make_kernel, tmp_path)
    made._activity.enter()
    advance(made, clock, 5 * 60)
    assert made.status().phase == "waiting"
    advance(made, clock, 120)
    assert calls["update"] == 0
    made._activity.leave()
    advance(made, clock, 1)
    assert made.status().phase == "countdown"


def test_a_failed_update_keeps_the_version_says_so_once_and_retries_after_six_hours(make_kernel, tmp_path):
    made, clock, calls, _ = updater(make_kernel, tmp_path, outcome=RuntimeError("uv failed"))
    advance(made, clock, 5 * 60)
    advance(made, clock, 30)
    status = made.status()
    assert calls == {"restart": 0, "update": 1}
    assert status.phase == "waiting"
    assert status.message == auto.failure_message("1.0.0")
    assert made.dismiss().message is None
    for _ in range(11):  # checks keep coming every half hour; none opens a countdown before six hours
        advance(made, clock, 30 * 60)
        assert made.status().phase == "waiting"
    advance(made, clock, 30 * 60)
    assert made.status().phase == "countdown"


def test_an_update_that_changes_nothing_is_reported_and_not_repeated_at_once(make_kernel, tmp_path):
    made, clock, calls, _ = updater(make_kernel, tmp_path, outcome=Result(updated=False, after="1.0.0"))
    advance(made, clock, 5 * 60)
    advance(made, clock, 30)
    assert calls == {"restart": 0, "update": 1}
    assert made.status().message == auto.failure_message("1.0.0")


def test_after_the_restart_the_new_version_reports_success_or_the_old_one_reports_failure(make_kernel, tmp_path):
    made, clock, _, kernel = updater(make_kernel, tmp_path)
    advance(made, clock, 5 * 60)
    advance(made, clock, 30)
    settings = made._read_settings
    after = AutoUpdater(kernel, restart=lambda: None, development=None, clock=clock, version="9.9.9", settings=settings)
    assert after.status().message == "hz is up to date: 9.9.9, updated automatically."
    assert not after.plan.pending
    made.plan.restarting_to = "9.9.9"
    made._save()
    still_old = AutoUpdater(
        kernel, restart=lambda: None, development=None, clock=clock, version="1.0.0", settings=settings
    )
    assert still_old.status().message == auto.failure_message("1.0.0")
    assert still_old.plan.retry_at == clock.now + timedelta(hours=6)


def test_the_endpoints_are_behind_the_token_and_share_one_plan(make_kernel, tmp_path):
    settings = Settings(state_dir=tmp_path / "state", cache_dir=tmp_path / "cache")
    kernel = make_kernel(builtins=True, settings_=settings)
    app = web.build(kernel, port=8000, token=TOKEN, static=None, restart=lambda: None)
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    assert client.get("/api/auto-update").status_code == 401
    answer = client.get("/api/auto-update", headers=AUTH)
    assert answer.status_code == 200
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.json()["phase"] == "off"
    assert answer.json()["development"] == auto.SOURCE_CHECKOUT  # these tests run from a checkout
    made: AutoUpdater = app.state.auto_update
    auto.found(made.plan, "9.9.9", {}, datetime.now(UTC))
    auto.step(made.plan, datetime.now(UTC), busy=False)
    postponed = client.post("/api/auto-update/postpone", headers=AUTH).json()
    assert postponed["postponements"] == 1
    assert client.get("/api/auto-update", headers=AUTH).json()["postponements"] == 1


def test_automatic_updates_are_on_by_default_for_new_and_existing_installations():
    assert Settings().auto_update is True
    assert Settings.model_fields["auto_update"].title == "Automatic updates"


# Development: judged by facts, never by a setting ---------------------------------------------------------------------

INSTALLED = {
    "package": Path("/tools/heinzel/lib/python3.13/site-packages/heinzel"),
    "prefix": Path("/tools/heinzel"),
    "editable": lambda: False,
}


def installed_state(tmp_path, monkeypatch):
    """A state folder that is the user's default, as for an installed hz started by its person."""
    state = tmp_path / "home-state"
    monkeypatch.setattr(auto, "_default_state_dir", lambda: state)
    return state


def test_an_installed_hz_with_the_users_state_folder_is_not_development(tmp_path, monkeypatch):
    state = installed_state(tmp_path, monkeypatch)
    assert auto.development_reason(state, environ={"HOME": "/home/x"}, **INSTALLED) is None


@pytest.mark.parametrize(
    "variable",
    ["HZ_DEV", "HZ_TEST", "CI", "PYTEST_CURRENT_TEST", "TEST_WORKER_INDEX", "MAKELEVEL", "npm_lifecycle_event"],
)
def test_a_development_test_ci_or_dev_server_variable_means_development(tmp_path, monkeypatch, variable):
    state = installed_state(tmp_path, monkeypatch)
    assert auto.development_reason(state, environ={variable: "1"}, **INSTALLED) == auto.DEVELOPMENT


def test_a_state_folder_other_than_the_users_default_means_development(tmp_path, monkeypatch):
    installed_state(tmp_path, monkeypatch)
    assert auto.development_reason(tmp_path / "other", environ={}, **INSTALLED) == auto.DEVELOPMENT


def test_a_source_tree_an_editable_install_or_a_checkouts_environment_means_development(tmp_path, monkeypatch):
    state = installed_state(tmp_path, monkeypatch)
    source = {**INSTALLED, "package": tmp_path / "src" / "heinzel"}
    assert auto.development_reason(state, environ={}, **source) == auto.SOURCE_CHECKOUT
    editable = {**INSTALLED, "editable": lambda: True}
    assert auto.development_reason(state, environ={}, **editable) == auto.SOURCE_CHECKOUT
    (tmp_path / "checkout" / ".git").mkdir(parents=True)
    in_checkout = {**INSTALLED, "prefix": tmp_path / "checkout" / ".venv"}
    assert auto.development_reason(state, environ={}, **in_checkout) == auto.SOURCE_CHECKOUT


def test_the_test_override_counts_none_of_the_facts(tmp_path):
    environ = {"HEINZEL_AUTO_UPDATE_TEST": "1", "CI": "true", "PYTEST_CURRENT_TEST": "x"}
    assert auto.development_reason(tmp_path, environ=environ, package=tmp_path / "src" / "heinzel") is None


def test_this_test_run_counts_as_development():
    assert auto.development_reason(auto._default_state_dir()) is not None


def test_while_developing_nothing_is_checked_scheduled_or_shown_and_the_switches_say_why(
    make_kernel, tmp_path, monkeypatch
):
    made, clock, calls, _ = updater(make_kernel, tmp_path)
    made.development = auto.SOURCE_CHECKOUT
    made.plan.message = "an earlier message"
    advance(made, clock, 10 * 60)
    advance(made, clock, 60)
    status = made.status()
    assert calls == {"restart": 0, "update": 0}
    assert not made.plan.pending  # never checked
    assert (status.phase, status.enabled, status.message) == ("off", False, None)
    assert status.development == status.reason == "Updates are managed by your source checkout."
    started = []
    monkeypatch.setattr("threading.Thread", lambda *_, **named: started.append(named))
    made.start()
    assert started == []  # no background loop at all


def test_with_update_checks_off_nothing_is_asked_or_installed_and_the_switches_say_why(make_kernel, tmp_path):
    made, clock, calls, kernel = updater(make_kernel, tmp_path, checks=False)
    asked = []
    kernel.bind(
        ReleaseSource, type("Asked", (Releases,), {"latest_version": lambda _, t: asked.append(t) or "9.9.9"})()
    )
    advance(made, clock, 10 * 60)
    status = made.status()
    assert (asked, calls["update"], status.phase) == ([], 0, "off")
    assert status.held == status.reason == auto.CHECKS_OFF
    assert status.development is None


# Only what was announced, once old enough -----------------------------------------------------------------------------


def test_a_release_counts_only_once_old_enough_hz_after_6_hours_plugins_after_24(make_kernel, tmp_path):
    assert (Settings().auto_update_min_age_hours, Settings().auto_update_plugin_min_age_hours) == (6, 24)
    made, clock, calls, kernel = updater(make_kernel, tmp_path)
    kernel.bind(PluginReleases, Plugins(uploaded=START, core_uploaded=START))
    advance(made, clock, 5 * 60)
    assert made.status().phase == "idle"  # both brand new
    clock.now = START + timedelta(hours=6, minutes=1)
    made.check(clock.now)
    assert (made.plan.core, made.plan.plugins) == ("9.9.9", {})  # hz is old enough, the plugin is not
    clock.now = START + timedelta(hours=24, minutes=1)
    made.check(clock.now)
    assert made.plan.plugins == {"heinzel-example": "2.0.0"}
    assert calls["update"] == 0


@pytest.mark.parametrize("releases", [Releases(prerelease=True), Releases(latest="10.0.0rc1")])
def test_a_pre_release_or_one_of_unknown_age_is_never_announced(make_kernel, tmp_path, releases):
    made, clock, _, kernel = updater(make_kernel, tmp_path)
    kernel.bind(ReleaseSource, releases)
    advance(made, clock, 5 * 60)
    assert made.plan.core is None


# Two servers on one state folder --------------------------------------------------------------------------------------


def twin(made, kernel, clock, calls):
    """A second `hz ui` on the same state folder, as after starting it twice."""

    def update(news):
        calls["update"] += 1
        return Result(updated=True, after="9.9.9")

    return AutoUpdater(
        kernel,
        restart=lambda: None,
        clock=clock,
        version="1.0.0",
        settings=made._read_settings,
        update=update,
        development=None,
    )


def test_two_servers_change_the_same_plan_and_neither_undoes_the_other(make_kernel, tmp_path):
    made, clock, _, kernel = updater(make_kernel, tmp_path)
    advance(made, clock, 5 * 60)  # news, and a countdown, on disk
    with made._editing() as plan:
        plan.message = "hz is up to date: 1.0.0, updated automatically."
    other = twin(made, kernel, clock, {"update": 0})
    stale = other.plan.to_json()
    made.postpone()  # in one window of the first server
    assert other.plan.to_json() == stale  # the second still holds the plan from before
    other.dismiss()  # in a window of the second
    saved = Plan.from_json(json.loads((tmp_path / "state" / "web" / "auto-update.json").read_text()))
    assert (saved.postponements, saved.message) == (1, None)  # both changes, neither lost
    assert made.status().postponements == other.status().postponements == 1


def test_only_one_server_applies_while_the_other_waits(make_kernel, tmp_path):
    made, clock, calls, kernel = updater(make_kernel, tmp_path)
    other_calls = {"update": 0}
    other = twin(made, kernel, clock, other_calls)
    holder = auto._try_lock(tmp_path / "state" / "web" / "auto-update-apply.lock")  # the first server applies now
    assert holder is not None
    try:
        other._next_check = clock.now
        advance(other, clock, 1)
        advance(other, clock, 30)
        assert other_calls["update"] == 0
        assert other.plan.window_at == clock.now + timedelta(hours=1)  # it looks again later, after the restart
    finally:
        auto._unlock(holder)
    assert calls["update"] == 0


# What the server asks the system module to install --------------------------------------------------------------------


def test_the_server_asks_for_exactly_the_announced_versions_and_the_commit(make_kernel, tmp_path):
    made, clock, calls, _ = updater(make_kernel, tmp_path)
    advance(made, clock, 5 * 60)
    advance(made, clock, 30)
    assert made.applied == [{"core": "9.9.9", "plugins": {"heinzel-example": "2.0.0"}, "commit": COMMIT}]
    assert calls["restart"] == 1


def test_an_update_that_did_not_start_is_put_back_and_never_tried_again(make_kernel, tmp_path):
    message = "hz 9.9.9 was installed but didn't start, so hz 1.0.0 and its plugins were put back."
    outcome = Result(updated=False, after="1.0.0", refused=True, message=message)
    made, clock, calls, _ = updater(make_kernel, tmp_path, outcome=outcome)
    advance(made, clock, 5 * 60)
    advance(made, clock, 30)
    assert (calls, made.status().message) == ({"restart": 0, "update": 1}, message)
    for _ in range(20):  # the same news is never applied again, however often it is announced
        advance(made, clock, 30 * 60)
    assert calls["update"] == 1
    assert made.status().phase == "idle"


def test_a_tag_moved_since_it_was_announced_installs_nothing(make_kernel, tmp_path):
    made, clock, calls, kernel = updater(make_kernel, tmp_path)
    advance(made, clock, 5 * 60)
    moved = Releases()
    moved.release_facts = lambda version, timeout: ReleaseFacts(version, START, "d" * 40)
    kernel.bind(ReleaseSource, moved)
    advance(made, clock, 30)
    assert calls == {"restart": 0, "update": 0}
    assert "names another commit than when it was announced" in (made.status().message or "")


def test_the_age_of_hz_is_its_last_upload_to_pypi_not_its_github_release(make_kernel, tmp_path):
    made, clock, _, kernel = updater(make_kernel, tmp_path)
    kernel.bind(PluginReleases, Plugins(core_uploaded=START))  # on GitHub a week ago, on PyPI just now
    advance(made, clock, 5 * 60)
    assert made.plan.core is None


def test_hz_missing_or_yanked_on_pypi_is_never_announced(make_kernel, tmp_path):
    made, clock, _, kernel = updater(make_kernel, tmp_path)
    kernel.bind(PluginReleases, Plugins(core_uploaded=None))
    advance(made, clock, 5 * 60)
    assert made.plan.core is None


def test_the_server_finds_the_system_operations_it_calls(make_kernel):
    found = {(record.qualname, operation.name) for record, operation in make_kernel(builtins=True).lets(OperationLet)}
    assert (auto.UPDATE_MODULE, auto.UPDATE_OPERATION) in found
    assert (auto.UPDATE_MODULE, auto.RECOVER_OPERATION) in found


def test_a_plugin_from_git_or_a_folder_is_named_to_update_by_hand_while_the_rest_updates(make_kernel, tmp_path):
    made, clock, calls, kernel = updater(make_kernel, tmp_path)
    receipt = Receipt(CorePin("release", "heinzel", ref="v0.1.0", git="https://github.com/org/heinzel"), tmp_path)
    receipt.plugin_pins = lambda: [
        CorePin("index", "heinzel-clock"),
        CorePin("ref", "heinzel-crm @ git+https://example.test/crm@main", ref="main", name="heinzel-crm"),
    ]
    kernel.bind(Installation, receipt)
    advance(made, clock, 5 * 60)
    assert made.status().by_hand == ["heinzel-crm"]
    advance(made, clock, 30)
    assert calls == {"restart": 1, "update": 1}  # hz and the plugins from PyPI update all the same
    assert made.applied[0]["plugins"] == {"heinzel-example": "2.0.0"}


def test_a_left_copy_is_put_back_at_start_only_while_no_other_server_applies(make_kernel, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(AutoUpdater, "_recover_with_lock", lambda self: asked.append(True))
    settings = Settings(state_dir=tmp_path / "state", cache_dir=tmp_path / "cache")
    kernel = make_kernel(builtins=True, settings_=settings)
    AutoUpdater(kernel, restart=lambda: None, development=None)
    assert asked == [True]
    holder = auto._try_lock(tmp_path / "state" / "web" / "auto-update-apply.lock")
    try:
        AutoUpdater(kernel, restart=lambda: None, development=None)
    finally:
        auto._unlock(holder)
    assert asked == [True]  # the other server's copy is in use
