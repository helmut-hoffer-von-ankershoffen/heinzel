"""NotifyService (ADR 0041) with a fake clock and a fake notifier: recording, showing once, the daemon's pop-ups."""

import stat
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heinzel import Check, Delivery, Module, NoticeKind, Notifications, Status
from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify._macos import NotifierApp
from heinzel.modules.system.notify._native import HEINZEL, Native
from heinzel.modules.system.notify._store import MAX_NOTICES

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
ORG = {"working_hours": "09-18", "timezone": "Europe/Berlin"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=BERLIN)  # a Wednesday, working

    def __call__(self) -> datetime:
        return self.now


class Runner:
    def __init__(self) -> None:
        self.code = 0
        self.commands: list[list[str]] = []

    def __call__(self, command, timeout=None):
        self.commands.append(list(command))
        return subprocess.CompletedProcess(command, self.code, "", "refused")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def runner() -> Runner:
    return Runner()


@pytest.fixture
def make(settings, clock, runner):
    def build(**values: object) -> NotifyService:
        return NotifyService(
            settings,
            NotifySettings.model_validate(values),
            ORG,
            clock=clock,
            notifier=Native("notify-send", "/usr/bin/notify-send", detail="through notify-send", runner=runner),
            ui_url=lambda route: f"http://127.0.0.1:8737{route}",
        )

    return build


def draft(service: NotifyService, key: str = "draft:me:weekly", **values: object):
    return service.emit(
        NoticeKind.DRAFT,
        "Draft ready: Weekly",
        "Read it.",
        route="/contracts/me%3Aweekly",
        command="hz workflow drafts --name me:weekly --latest",
        key=key,
        **values,
    )


def test_a_notice_is_recorded_privately_with_what_the_policy_decided(make, settings):
    service = make()
    notice = draft(service)
    assert notice is not None
    assert (notice.popup, notice.held, notice.due) == (True, None, notice.at)
    assert service.recent() == [notice]
    store = settings.state_dir / "notify"
    assert stat.S_IMODE(store.stat().st_mode) == 0o700
    assert stat.S_IMODE((store / "notices.json").stat().st_mode) == 0o600


def test_a_repeat_returns_the_earlier_notice_and_records_nothing(make, clock):
    service = make()
    first = draft(service)
    clock.now += timedelta(hours=1)
    assert draft(service) == first
    assert len(service.recent()) == 1
    clock.now += timedelta(hours=24)
    assert draft(service) != first


def test_a_kind_that_is_off_records_nothing(make):
    assert draft(make(draft=False)) is None
    assert make(draft=False).recent() == []


@pytest.mark.parametrize(
    "route", ["//evil.example.test/x", "/\t/evil.example.test", "/\\evil", "https://evil.example.test", "/a b", ""]
)
def test_a_route_must_be_a_path_of_the_app(make, route):
    notice = make().emit(NoticeKind.TEST, "t", route=route, command="hz", key="a")
    assert notice is not None
    assert notice.route == "/"


def test_routes_of_the_app_are_kept(make):
    for route in ("/", "/doctor", "/contracts/me%3Aweekly", "/?settings=&q=x"):
        notice = make(repeat_hours=0).emit(NoticeKind.TEST, "t", route=route, command="hz", key=route)
        assert notice is not None
        assert notice.route == route


def test_notices_outside_working_hours_are_due_at_their_start(make, clock):
    clock.now = clock.now.replace(hour=22)
    service = make()
    notice = draft(service)
    assert notice is not None
    assert service.due() == []
    assert service.due(datetime(2026, 10, 22, 9, tzinfo=BERLIN)) == [notice]
    assert draft(service, key="urgent", urgent=True) in service.due()


def test_shown_once_by_whoever_shows_it_first(make):
    service = make()
    notice = draft(service)
    assert notice is not None
    assert [n.id for n in service.deliver([notice.id], Delivery.WEB)] == [notice.id]
    assert service.deliver([notice.id], Delivery.DAEMON) == []
    assert service.recent()[0].delivered_by is Delivery.WEB
    assert service.due() == []


def test_the_daemon_pops_up_due_notices_natively_and_marks_them(make, runner):
    service = make()
    draft(service)
    assert service.post_native() == 1
    title, body = runner.commands[0][-2:]
    assert title == "Draft ready: Weekly"
    assert body == "Read it. Run: hz workflow drafts --name me:weekly --latest"  # notify-send can't open a link
    assert service.recent()[0].delivered_by is Delivery.DAEMON
    assert service.post_native() == 0


def test_the_daemon_holds_back_while_the_web_app_shows_notices(make, clock, runner):
    service = make()
    draft(service)
    service.watched()
    assert service.post_native() == 0
    clock.now += timedelta(seconds=31)
    assert service.post_native() == 1
    assert len(runner.commands) == 1


def test_a_refused_pop_up_stays_due_until_it_is_stale(make, clock, runner):
    service = make()
    draft(service)
    runner.code = 1
    assert service.post_native() == 0
    assert len(service.due()) == 1
    clock.now += timedelta(hours=13)
    assert service.due() == []


def test_the_setting_turns_native_pop_ups_off(make, runner):
    service = make(native=False)
    draft(service)
    assert service.post_native() == 0
    assert runner.commands == []


def test_quiet_mode_lists_but_never_pops_up(make, runner):
    service = make(quiet=True)
    notice = draft(service, urgent=True)
    assert notice is not None
    assert notice.popup is False
    assert service.post_native() == 0
    assert service.listing(10).waiting == 0


def test_the_store_is_bounded_and_survives_damage(make, settings, clock):
    service = make(repeat_hours=0, per_hour=60)
    for index in range(MAX_NOTICES + 5):
        draft(service, key=str(index))
        clock.now += timedelta(seconds=1)
    assert len(service.recent(500)) == MAX_NOTICES
    assert service.recent(1)[0].key == str(MAX_NOTICES + 4)
    clock.now += timedelta(days=31)
    draft(service, key="new")
    assert [n.key for n in service.recent(500)] == ["new"]
    (settings.state_dir / "notify" / "notices.json").write_text("{not json", encoding="utf-8")
    assert service.recent() == []
    assert draft(service, key="after") is not None


def test_ack_marks_what_the_web_app_showed(make):
    service = make()
    notice = draft(service)
    assert notice is not None
    assert service.ack([notice.id, "unknown"]).delivered == [notice.id]


def test_doctor_says_how_notices_reach_you(make):
    check = make().health()[0]
    assert check.status is Status.UP
    assert check.detail == (
        "the web app while it is open; otherwise through notify-send; "
        "outside 09-18 Monday to Friday (Europe/Berlin) only urgent ones"
    )
    assert "off" in make(enabled=False).health()[0].detail
    assert "web app only" in make(native=False).health()[0].detail
    assert "quiet mode" in make(quiet=True).health()[0].detail


def test_the_kernel_injects_it_as_notifications(make_kernel):
    kernel = make_kernel(builtins=True)
    assert isinstance(kernel.resolve(Notifications), NotifyService)
    assert kernel.resolve(Notifications) is kernel.resolve(NotifyService)


def test_the_base_class_only_declares():
    base = Notifications()
    for call in (
        lambda: base.emit(NoticeKind.TEST, "t", route="/", command="hz", key="k"),
        base.recent,
        base.due,
        lambda: base.deliver([], Delivery.WEB),
        base.watched,
        base.post_native,
    ):
        with pytest.raises(NotImplementedError):
            call()


def test_once_an_hour_the_tick_reports_what_doctor_finds(make_kernel, monkeypatch):
    monkeypatch.setenv("HZ_SYSTEM_NOTIFY__DOCTOR", "true")  # tests turn it off (heinzel.testing)
    sick = Module("sick", "A module whose check fails.")

    @sick.health
    def broken() -> list[Check]:
        """Always degraded."""
        return [Check(name="thing", status=Status.DEGRADED, detail="broken", fix="fix it")]

    kernel = make_kernel(builtins=True, sick=sick)
    service = kernel.resolve(NotifyService)
    monkeypatch.setattr(service, "_notifier", Native(None, detail="none in tests"))
    service.post_native()
    assert len(service.recent()) == 1
    assert service.look() == 0  # not again within the hour
    service.store.looked(service.recent()[0].at - timedelta(hours=2))
    assert service.look() == 1  # the same notice again, not a new one
    assert len(service.recent()) == 1
    notice = service.recent()[0]
    assert notice.kind is NoticeKind.DOCTOR
    assert "sick: thing" in notice.body
    assert (notice.route, notice.command) == ("/doctor", "hz system doctor")


def test_doctor_reports_a_refused_pop_up_until_one_works(make, runner):
    service = make()
    draft(service)
    draft(service, key="second")
    runner.code = 1
    assert service.post_native() == 0
    assert len(runner.commands) == 1  # the second would fail the same way
    refused = service.health()[1]
    assert refused.status is Status.DEGRADED
    assert "notify-send failed" in refused.detail
    assert "hz system notify install" in refused.fix
    runner.code = 0
    assert service.post_native() == 2
    assert len(service.health()) == 1


def heinzel_service(settings, clock, tmp_path, said: str, *, quarantined: bool = False) -> NotifyService:
    bundle = tmp_path / "HeinzelNotifier.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "notifier").write_bytes(b"app")

    def run(command, timeout=None):
        if "--stdout" in command:
            answer = said if "--status" in command else "posted"
            Path(command[command.index("--stdout") + 1]).write_text(answer, encoding="utf-8")
        code = 0 if command[0] != "/usr/bin/xattr" or quarantined else 1
        return subprocess.CompletedProcess(command, code, "", "")

    app = NotifierApp(tmp_path / "home", bundled=bundle, runner=run)
    notifier = Native(HEINZEL, str(app.path), clicks=True, app=app)
    return NotifyService(settings, NotifySettings(), ORG, clock=clock, notifier=notifier)


def test_doctor_checks_heinzel_notifier_without_installing_it(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "not-determined")
    checks = {check.name: check for check in service.health()}
    assert checks["heinzel notifier"].status is Status.UP
    assert "first notification installs it" in checks["heinzel notifier"].detail
    assert not (tmp_path / "home" / "Applications").exists()  # doctor reads, never installs
    draft(service)
    assert service.post_native() == 0  # installs it, but never asks macOS's question from the daemon
    assert len(service.due()) == 1  # handed back: due again once someone allowed it
    checks = {check.name: check for check in service.health()}
    assert checks["heinzel notifier"].detail.endswith("(current)")
    assert "hasn't asked yet" in checks["heinzel notifier permission"].detail
    assert "hz system notify install" in checks["native notifications"].detail


def test_once_allowed_the_daemon_pops_up_through_heinzel_notifier(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "authorized")
    draft(service)
    assert service.post_native() == 1
    assert service.recent()[0].delivered_by is Delivery.DAEMON


def test_doctor_names_a_denial_and_a_quarantine(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "denied", quarantined=True)
    assert service.notifier.app is not None
    service.notifier.app.ensure()
    checks = {check.name: check for check in service.health()}
    assert checks["heinzel notifier permission"].status is Status.DEGRADED
    assert "System Settings > Notifications > Heinzel Notifier" in checks["heinzel notifier permission"].fix
    assert checks["heinzel notifier quarantine"].fix.startswith("xattr -d com.apple.quarantine")


def test_install_puts_heinzel_notifier_in_place_and_pops_up_a_test(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "authorized")
    installed = service.install()
    assert (installed.notifier, installed.state, installed.shown) == (HEINZEL, "installed", True)
    assert installed.path == str(tmp_path / "home" / "Applications" / "Heinzel Notifier.app")
    assert any("choose Allow" in step for step in installed.next)
    assert service.recent() == []  # a test of the notifier, not a notice


def test_install_without_a_notifier_says_why(make, settings, clock):
    service = NotifyService(settings, NotifySettings(), ORG, clock=clock, notifier=Native(None, detail="headless"))
    installed = service.install()
    assert (installed.notifier, installed.shown, installed.problem) == (None, False, "headless")


def test_a_failing_watcher_never_stops_the_pop_ups(make):
    service = make()

    def broken() -> int:
        msg = "doctor exploded"
        raise RuntimeError(msg)

    service.watcher = broken
    draft(service)
    assert service.post_native() == 1


def test_the_rate_limit_holds_back_what_waited_overnight(make, clock, runner):
    clock.now = clock.now.replace(hour=20)
    service = make(per_hour=2)
    for index in range(4):  # one every two hours: none is over the limit when it is recorded
        draft(service, key=str(index))
        clock.now += timedelta(hours=2)
    clock.now = datetime(2026, 10, 22, 9, 1, tzinfo=BERLIN)
    assert service.post_native() == 2
    assert service.post_native() == 0
    clock.now += timedelta(hours=1, minutes=1)
    assert service.post_native() == 2


def test_the_list_pages_back_through_everything_kept(make, clock):
    service = make(repeat_hours=0, per_hour=60)
    for index in range(5):
        draft(service, key=str(index))
        clock.now += timedelta(seconds=1)
    first = service.listing(2)
    assert [n.key for n in first.notices] == ["4", "3"]
    second = service.listing(2, before=first.notices[-1].id)
    assert [n.key for n in second.notices] == ["2", "1"]
    assert [n.key for n in service.listing(2, before=second.notices[-1].id).notices] == ["0"]
