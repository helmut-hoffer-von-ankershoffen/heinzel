"""NotifyService paths the main service test leaves out: failing stores, calendar validation, the native notifier's
maintenance, chat, the test pop-up, the installer and doctor's checks. Real stores in a temp directory; the native
notifier is a `NotifierApp` whose commands are recorded instead of run."""

import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heinzel import Delivery, HeinzelError, NoticeKind, ResolveWhen, Status
from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify._macos import NotifierApp
from heinzel.modules.system.notify._native import ALLOW, HEINZEL, Native
from heinzel.modules.system.notify._service import SETTINGS_URL, STRAY_EVERY, _open, _with_command, event_route

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
ORG = {"working_hours": "09-18", "timezone": "Europe/Berlin"}
CALENDAR = {"route": "/", "command": "hz org agenda", "key": "calendar:one"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=BERLIN)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def service_for(settings, clock, notifier=None, **values) -> NotifyService:
    notifier = notifier or Native("notify-send", "/usr/bin/notify-send", detail="through notify-send", runner=ok)
    return NotifyService(
        settings,
        NotifySettings.model_validate({"working_hours": False, **values}),
        ORG,
        clock=clock,
        notifier=notifier,
        ui_url=lambda route: f"http://127.0.0.1:8737{route}",
    )


def ok(command, timeout=None):
    return subprocess.CompletedProcess(command, 0, "", "")


def break_store(service: NotifyService) -> None:
    """Durable keys with no notice file: every strict read of the store refuses."""
    service.emit_once(NoticeKind.HEINZEL, "t", "b", **CALENDAR)
    service.store.path.unlink()


class FakeApp(NotifierApp):
    """A `NotifierApp` whose desktop side is scripted."""

    def __init__(self, home: Path, **answers) -> None:
        super().__init__(home, bundled=home / "none", runner=ok)
        self.answers = {
            "state": "current",
            "detail": "",
            "stale": False,
            "quarantined": False,
            "status": "authorized",
            "rebuild": None,
            "ended": 0,
            "strays": 0,
            **answers,
        }
        self.calls: list[str] = []

    def inspect(self):
        from heinzel.modules.system.notify._macos import Install

        self.calls.append("inspect")
        return Install(self.path, self.answers["state"], self.answers["detail"])

    def ensure(self):
        from heinzel.modules.system.notify._macos import Install

        self.calls.append("ensure")
        if (error := self.answers.get("ensure_error")) is not None:
            raise error
        self.path.mkdir(parents=True, exist_ok=True)
        return Install(self.path, self.answers.get("ensured", "installed"), self.answers["detail"])

    def rebuild(self):
        return self.answers["rebuild"]

    def clean(self, *, older_than: int = 0) -> int:
        self.calls.append("clean")
        if (error := self.answers.get("clean_error")) is not None:
            raise error
        return self.answers["ended"]

    def instances(self):
        from heinzel.modules.system.notify._macos import Instance

        return [Instance(pid=1, label="x", seconds=10_000)] if self.answers["stale"] else []

    def quarantined(self) -> bool:
        return self.answers["quarantined"]

    def status(self) -> str:
        return self.answers["status"]

    def unregister_strays(self) -> int:
        self.calls.append("strays")
        if (error := self.answers.get("strays_error")) is not None:
            raise error
        return self.answers["strays"]

    def post(self, title, message, url, *, asking=False, look=None) -> str:
        self.calls.append("post")
        return self.answers.get("posted", "posted")


def heinzel_service(settings, clock, tmp_path, **answers) -> NotifyService:
    app = FakeApp(tmp_path / "home", **answers)
    return service_for(settings, clock, Native(HEINZEL, str(app.path), clicks=True, app=app))


def test_unreadable_consent_fails_closed_and_a_readable_one_refreshes_the_policy(settings, clock):
    service = service_for(settings, clock)
    assert service.enabled(NoticeKind.DRAFT) is True
    service._refresh_settings = lambda: NotifySettings(enabled=False)
    assert service.enabled(NoticeKind.DRAFT) is False

    def unreadable() -> NotifySettings:
        msg = "hz.toml is gone"
        raise OSError(msg)

    service._refresh_settings = unreadable
    assert service.enabled(NoticeKind.DRAFT) is False


def test_a_failing_store_is_reported_not_raised_by_every_writer(settings, clock, caplog):
    service = service_for(settings, clock)
    break_store(service)
    with caplog.at_level(logging.WARNING):
        assert service.withdraw("calendar:one") is False
        assert service.forget_completed({"calendar:one"}) is False
        assert service.forget_once("calendar:one") is False
        assert service.has_once("calendar:one") is None
        assert service.resolve() == 0
        assert service.emit_once(NoticeKind.HEINZEL, "t", "b", **{**CALENDAR, "key": "calendar:two"}) is False
        assert service.emit(NoticeKind.DRAFT, "t", route="/", command="c", key="k") is None
    events = " ".join(record.getMessage() for record in caplog.records)
    for event in ("withdraw_failed", "compact_failed", "inspect_failed", "record_once_failed", "record_failed"):
        assert event in events


def test_emit_once_refuses_an_expiry_without_a_zone(settings, clock):
    service = service_for(settings, clock)
    naive = datetime(2026, 10, 21, 13, 0)  # noqa: DTZ001 - naive on purpose
    assert service.emit_once(NoticeKind.HEINZEL, "t", "b", expires=naive, **CALENDAR) is False
    assert service.recent() == []


def test_a_route_that_is_not_a_path_of_the_web_app_becomes_the_root(settings, clock):
    service = service_for(settings, clock)
    notice = service.emit(NoticeKind.DRAFT, "t", route="//evil.example", command="c", key="k")
    assert notice is not None
    assert notice.route == "/"
    assert service.emit_once(NoticeKind.HEINZEL, "t", "b", **{**CALENDAR, "route": "http://x"})
    assert {n.route for n in service.recent()} == {"/"}


def test_look_runs_the_watcher_once_an_hour_and_logs_its_failure(settings, clock, caplog):
    service = service_for(settings, clock)
    assert service.look() == 0  # no watcher
    runs: list[int] = []
    service.watcher = lambda: runs.append(1) or 2
    assert service.look() == 2
    assert service.look() == 0  # looked a moment ago
    assert runs == [1]
    clock.now += timedelta(hours=2)

    def failing() -> int:
        msg = "doctor crashed"
        raise RuntimeError(msg)

    service.watcher = failing
    with caplog.at_level(logging.ERROR):
        assert service.look() == 0
    assert "looking for doctor's findings failed" in caplog.text


def test_notifier_is_looked_up_once_for_this_machine(settings, clock):
    service = NotifyService(settings, NotifySettings(), ORG, clock=clock)
    assert service.notifier is service.notifier
    assert service.notifier.name in {None, "osascript", "notify-send", HEINZEL}


class Calendar:
    def __init__(self, keys: set[str]) -> None:
        self.keys = keys

    def __call__(self, now: datetime) -> set[str]:
        return self.keys


def test_a_calendar_notice_pops_up_only_while_the_provider_still_validates_it(settings, clock):
    service = service_for(settings, clock, calendar=True)
    service.calendar_valid = Calendar({"calendar:one"})
    assert service.emit_once(NoticeKind.CALENDAR, "Meeting", "Soon", **CALENDAR)
    assert [n.key for n in service.due()] == ["calendar:one"]
    service.calendar_valid = Calendar(set())
    assert service.due() == []

    def crashing(now: datetime) -> set[str]:
        msg = "provider down"
        raise RuntimeError(msg)

    service.calendar_valid = crashing
    assert service.due() == []


def test_calendar_notices_are_held_back_by_quiet_mode_and_the_daemon_switch(settings, clock):
    service = service_for(settings, clock, calendar=True)
    service.calendar_valid = Calendar({"calendar:one"})
    assert service.emit_once(NoticeKind.CALENDAR, "Meeting", "Soon", **CALENDAR)
    [pending] = service.due()
    service.settings = NotifySettings(working_hours=False, native=False, calendar=True)
    assert service.deliver([pending.id], Delivery.DAEMON) == []
    assert [n.id for n in service.deliver([pending.id], Delivery.WEB)] == [pending.id]


def test_an_expired_calendar_notice_is_released_not_posted(settings, clock):
    service = service_for(settings, clock, calendar=True)
    service.calendar_valid = Calendar({"calendar:one"})
    assert service.emit_once(
        NoticeKind.CALENDAR, "Meeting", "Soon", expires=clock.now + timedelta(minutes=5), **CALENDAR
    )
    [pending] = service.due()
    claimed = service.deliver([pending.id], Delivery.DAEMON)
    assert claimed
    clock.now += timedelta(minutes=10)
    service._release(claimed)
    assert service.store.read()[0].delivered_by is None
    assert service.post_native() == 0


def test_quiet_mode_stops_a_calendar_notice_at_the_presentation_boundary(settings, clock):
    service = service_for(settings, clock, calendar=True)
    service.calendar_valid = Calendar({"calendar:one"})
    assert service.emit_once(NoticeKind.CALENDAR, "Meeting", "Soon", **CALENDAR)
    service.settings = NotifySettings(working_hours=False, quiet=True, calendar=True)
    assert service._calendar_permitted() is False
    service.settings = NotifySettings(working_hours=True, calendar=True)
    service.hours = service.hours.__class__(start=0, end=0, zone=service.hours.zone)
    assert service._calendar_permitted() is False


def test_a_refused_pop_up_releases_the_claimed_notices_and_is_logged(settings, clock, caplog):
    refusing = Native(
        "notify-send",
        "/usr/bin/notify-send",
        detail="d",
        runner=lambda command, timeout=None: subprocess.CompletedProcess(command, 1, "", "no \x1b[31mdaemon\n"),
    )
    service = service_for(settings, clock, refusing)
    for key in ("a", "b"):
        service.emit(NoticeKind.DRAFT, "t", route="/", command="c", key=key)
    with caplog.at_level(logging.WARNING):
        assert service.post_native() == 0
    assert "notify.native.post" in caplog.text
    assert all(notice.delivered_by is None for notice in service.store.read())


def test_the_daemon_tick_looks_after_the_notifier_app_and_forgets_strays_once_a_day(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, state="missing", detail="not there", ended=2, strays=3)
    assert service.post_native() == 0
    app = service.notifier.app
    assert app.calls.count("strays") == 1
    assert service.post_native() == 0
    assert app.calls.count("strays") == 1  # at most once a day
    clock.now += STRAY_EVERY + timedelta(hours=1)
    service.post_native()
    assert app.calls.count("strays") == 2


def test_a_notifier_app_that_cannot_be_looked_after_never_stops_the_tick(settings, clock, tmp_path, caplog):
    service = heinzel_service(settings, clock, tmp_path, clean_error=RuntimeError("launchctl hung"))
    with caplog.at_level(logging.ERROR):
        assert service.post_native() == 0
    assert "looking after Heinzel Notifier failed" in caplog.text
    service = heinzel_service(settings, clock, tmp_path, strays_error=RuntimeError("lsregister hung"))
    with caplog.at_level(logging.ERROR):
        assert service._forget_stray_copies(service.notifier.app) == 0
    assert "stray Heinzel Notifier copies failed" in caplog.text


def test_a_marker_that_is_not_a_time_does_not_stop_the_strays_being_forgotten(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, strays=2)
    service.store.directory.mkdir(parents=True, exist_ok=True)
    (service.store.directory / "strays-forgotten-at").write_text("yesterday-ish", encoding="utf-8")
    assert service._forget_stray_copies(service.notifier.app) == 2


def test_clean_reports_what_ended_what_runs_and_what_was_unregistered(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, ended=1, stale=True, strays=4)
    cleaned = service.clean()
    assert (cleaned.ended, cleaned.running, cleaned.unregistered) == (1, 1, 4)
    other = service_for(settings, clock)
    cleaned = other.clean()
    assert (cleaned.ended, cleaned.running, cleaned.unregistered) == (0, 0, 0)


def test_recover_on_a_healthy_store_resolves_what_no_longer_applies(settings, clock):
    service = service_for(settings, clock)
    service.installed = lambda: "9.9.9"
    service.emit(
        NoticeKind.DRAFT,
        "Update",
        route="/",
        command="c",
        key="update",
        resolve_when=ResolveWhen(installed_version_at_least="1.0.0"),
    )
    assert service.store.read() == []  # already met: never recorded
    service.installed = lambda: "0.1.0"
    service.emit(
        NoticeKind.DRAFT,
        "Update",
        route="/",
        command="c",
        key="update2",
        resolve_when=ResolveWhen(installed_version_at_least="1.0.0"),
    )
    service.installed = lambda: "9.9.9"
    assert service.recent() == []  # hidden at read already
    preview = service.recover(apply=False)
    assert preview.resolved == 1
    assert "--apply" in preview.detail
    applied = service.recover(apply=True)
    assert applied.resolved == 1
    assert service.store.read()[0].resolved_at is not None


def test_the_test_notice_refuses_when_notifications_or_the_policy_forbid_it(settings, clock):
    service = service_for(settings, clock, enabled=False)
    with pytest.raises(HeinzelError, match="Notifications are off"):
        service.test(native=False)
    quiet = service_for(settings, clock, quiet=True)
    with pytest.raises(HeinzelError, match="won't pop up"):
        quiet.test(native=False)


def test_the_test_notice_reports_the_channels_and_pops_up_natively_on_request(settings, clock, tmp_path):
    service = service_for(settings, clock)
    tested = service.test(native=True)
    assert tested.native == "shown"
    assert tested.channels
    assert service.store.read()[0].delivered_by is Delivery.DAEMON
    assert service_for(settings, clock).test(native=False).native is None


def test_the_test_notice_without_a_native_notifier_says_why(settings, clock):
    none = Native(None, detail="no notifier on this machine")
    tested = service_for(settings, clock, none).test(native=True)
    assert tested.native == "no notifier on this machine"


def test_the_test_notice_with_a_refusing_notifier_returns_the_problem(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, posted="failed: not allowed")
    tested = service.test(native=True)
    assert tested.native == "Heinzel Notifier: failed: not allowed"
    assert service.store.read()[0].delivered_by is None


def test_settings_open_only_on_macos(settings, clock):
    service = service_for(settings, clock)
    opened: list[list[str]] = []
    service.opener = opened.append
    service.platform = "linux"
    assert service.open_settings().opened is False
    service.platform = "darwin"
    assert service.open_settings().opened is True
    assert opened == [["open", SETTINGS_URL]]


def test_the_opener_refuses_a_program_that_is_not_installed():
    with pytest.raises(HeinzelError, match="isn't on this machine's PATH"):
        _open(["no-such-program-hz"])


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")  # fire and forget: no Popen to wait on
def test_the_opener_starts_a_program_without_waiting(tmp_path):
    marker = tmp_path / "ran"
    _open(["touch", str(marker)])
    for _ in range(100):
        if marker.exists():
            break
        import time

        time.sleep(0.05)
    assert marker.exists()


def test_install_without_a_notifier_names_the_fix(settings, clock):
    none = Native(None, detail="no notifier", fix="install libnotify")
    installed = service_for(settings, clock, none).install()
    assert (installed.notifier, installed.shown, installed.problem, installed.next) == (
        None,
        False,
        "no notifier",
        ["install libnotify"],
    )
    assert service_for(settings, clock, Native(None, detail="none")).install().next == []


def test_install_puts_the_app_in_place_and_sends_a_test(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, state="outdated", ensured="installed")
    installed = service.install()
    assert (installed.notifier, installed.state, installed.previous, installed.shown) == (
        HEINZEL,
        "installed",
        "outdated",
        True,
    )
    assert installed.next  # the icon cache hint after replacing an older build


def test_install_reports_a_refused_pop_up_with_the_way_to_allow_it(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, state="current", ensured="current", posted="failed: no")
    installed = service.install()
    assert installed.shown is False
    assert ALLOW in installed.next
    assert service.store.native_problem() == "Heinzel Notifier: failed: no"


def test_install_never_copies_a_bundle_the_rebuild_could_not_bring_up_to_date(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, state="missing", rebuild="swiftc failed")
    installed = service.install()
    assert installed.state == "missing"
    assert "building it failed (swiftc failed)" in (installed.detail or "")
    service = heinzel_service(settings, clock, tmp_path, state="outdated", rebuild="swiftc failed")
    assert service.install().state == "outdated"


def test_install_explains_a_home_the_app_cannot_be_copied_into(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, ensure_error=PermissionError(13, "Permission denied"))
    with pytest.raises(HeinzelError, match="can't be installed in"):
        service.install()


def test_listing_pages_by_id_and_keeps_resolved_notices_on_request(settings, clock):
    service = service_for(settings, clock)
    notices = []
    for key in ("a", "b", "c"):
        clock.now += timedelta(minutes=1)
        notices.append(service.emit(NoticeKind.DRAFT, "t", route="/", command="c", key=key))
    page = service.listing(10, before=notices[2].id)
    assert [n.key for n in page.notices] == ["b", "a"]
    assert page.waiting == 2
    assert [n.key for n in service.listing(1).notices] == ["c"]
    assert len(service.listing(10, resolved=True).notices) == 3
    assert service.ack([notices[0].id]).delivered == [notices[0].id]


def test_doctor_reports_a_damaged_store_with_the_way_back(settings, clock):
    service = service_for(settings, clock)
    break_store(service)
    [check] = service.health()
    assert (check.name, check.status) == ("notification store", Status.DOWN)
    assert "Preserve the store" in check.fix


def test_doctor_names_a_legacy_overwritten_store_and_the_recover_command(settings, clock):
    service = service_for(settings, clock)
    service.emit_once(NoticeKind.HEINZEL, "t", "b", **CALENDAR)
    service.store.path.write_text("[]", encoding="utf-8")  # a legacy writer overwrote the durable shape
    [check] = service.health()
    assert check.status is Status.DOWN
    assert "recover --apply" in check.fix


def test_doctor_describes_how_notices_reach_you(settings, clock):
    off = service_for(settings, clock, enabled=False)
    assert off.health()[0].detail.startswith("off")
    web = service_for(settings, clock, native=False, quiet=True)
    assert "the web app only" in web.health()[0].detail
    assert "quiet mode is on" in web.health()[0].detail
    hours = service_for(settings, clock, working_hours=True)
    assert "only urgent ones" in hours.health()[0].detail
    failed = service_for(settings, clock)
    failed.store.native_result(clock.now, "no daemon")
    checks = {check.name: check for check in failed.health()}
    assert checks["native notifications"].status is Status.DEGRADED


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ({"state": "missing", "detail": "absent"}, {"heinzel notifier": Status.UP}),
        (
            {"state": "outdated", "detail": "old", "stale": True, "quarantined": True, "status": "denied"},
            {
                "heinzel notifier": Status.DEGRADED,
                "heinzel notifier processes": Status.DEGRADED,
                "heinzel notifier quarantine": Status.DEGRADED,
                "heinzel notifier permission": Status.DEGRADED,
            },
        ),
        (
            {"status": "not-determined"},
            {
                "heinzel notifier": Status.UP,
                "heinzel notifier processes": Status.UP,
                "heinzel notifier permission": Status.UP,
            },
        ),
        ({"status": "authorized"}, {"heinzel notifier permission": Status.UP}),
    ],
)
def test_doctor_checks_the_installed_notifier_app(settings, clock, tmp_path, answers, expected):
    service = heinzel_service(settings, clock, tmp_path, **answers)
    checks = {check.name: check.status for check in service.health()}
    for name, status in expected.items():
        assert checks[name] is status


def test_a_route_gets_the_event_id_and_a_command_is_appended_where_clicks_open_nothing(settings, clock):
    service = service_for(settings, clock)
    plain = service.emit(NoticeKind.DRAFT, "t", route="/a", command="c", key="a")
    query = service.emit(NoticeKind.DRAFT, "t", route="/a?x=1", command="c", key="b")
    assert event_route(plain) == f"/a?event={plain.id}"
    assert event_route(query) == f"/a?x=1&event={query.id}"
    assert _with_command("Body.", "hz x") == "Body. Run: hz x"
    assert _with_command("", "hz x") == "Run: hz x"
