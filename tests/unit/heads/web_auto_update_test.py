"""Automatic updates: the server's schedule, postpone, idle wait, apply and failure (ADR 0116)."""

import json
import random
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from heinzel import Settings
from heinzel.heads import web
from heinzel.heads.web import _auto_update as auto
from heinzel.heads.web._auto_update import Activity, AutoUpdater, Plan
from heinzel.kernel import CorePin, Installation, PluginReleases, ReleaseSource

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
    loaded = Plan.from_json(json.loads(json.dumps(plan.to_json())))
    assert (loaded.core, loaded.plugins, loaded.postponements) == ("9.9.9", {"heinzel-example": "2.0.0"}, 1)
    assert loaded.window_at == plan.window_at
    assert loaded.apply_at is None
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


class Releases(ReleaseSource):
    def __init__(self, latest="9.9.9"):
        self.latest = latest

    def latest_version(self, timeout):
        return self.latest


class Plugins(PluginReleases):
    def newer(self, timeout):
        return {"heinzel-example": "2.0.0"}


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
    def __init__(self, updated, after):
        self.updated, self.after = updated, after


def updater(make_kernel, tmp_path, *, on=True, channel="stable", outcome=None, restart=True, pin="release"):  # noqa: PLR0913 - one option per condition
    settings = Settings(state_dir=tmp_path / "state", cache_dir=tmp_path / "cache")
    kernel = make_kernel(builtins=True, settings_=settings)
    kernel.bind(Installation, Receipt(CorePin(pin, "heinzel", ref="v0.1.0"), tmp_path))
    kernel.bind(ReleaseSource, Releases())
    kernel.bind(PluginReleases, Plugins())
    clock, calls = Clock(), {"restart": 0, "update": 0}

    def update():
        calls["update"] += 1
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
        settings=lambda: settings.model_copy(update={"auto_update": on, "update_channel": channel}),
        update=update,
    )
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
    again = AutoUpdater(kernel, restart=lambda: None, clock=clock, version="1.0.0", settings=made._read_settings)
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
    after = AutoUpdater(kernel, restart=lambda: None, clock=clock, version="9.9.9", settings=settings)
    assert after.status().message == "hz is up to date: 9.9.9, updated automatically."
    assert not after.plan.pending
    made.plan.restarting_to = "9.9.9"
    made._save()
    still_old = AutoUpdater(kernel, restart=lambda: None, clock=clock, version="1.0.0", settings=settings)
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
    made: AutoUpdater = app.state.auto_update
    auto.found(made.plan, "9.9.9", {}, datetime.now(UTC))
    auto.step(made.plan, datetime.now(UTC), busy=False)
    postponed = client.post("/api/auto-update/postpone", headers=AUTH).json()
    assert postponed["postponements"] == 1
    assert client.get("/api/auto-update", headers=AUTH).json()["postponements"] == 1
