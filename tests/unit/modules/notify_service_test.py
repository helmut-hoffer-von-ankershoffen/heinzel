"""NotifyService (ADR 0041) with a fake clock and a fake notifier: recording, showing once, the daemon's pop-ups."""

import json
import stat
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from heinzel import Check, Delivery, Module, Notice, NoticeHelper, NoticeKind, Notifications, Status
from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify._macos import NotifierApp
from heinzel.modules.system.notify._native import ALLOW, DENIED, HEINZEL, Native
from heinzel.modules.system.notify._render import render_installed
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


def test_open_settings_reserves_cleanup_for_the_resolved_command(make, monkeypatch):
    service = make()
    service.platform = "darwin"
    opener = Mock()
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("heinzel.modules.system.notify._service.launch_desktop_opener", opener)

    assert service.open_settings().opened

    assert opener.call_args.args[0][0] == "/usr/bin/open"


def draft(service: NotifyService, key: str = "draft:me:weekly", **values: object):
    return service.emit(
        NoticeKind.DRAFT,
        "Draft ready: Weekly",
        "Read it.",
        route="/contracts/me%3Aweekly",
        command="hz heinzel drafts --name me:weekly --latest",
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


def test_helper_metadata_survives_reload_and_durable_deduplication_without_changing_delivery_policy(make, runner):
    helper = NoticeHelper(name="Mira", hat="blue", reason="scheduled check-in")
    service = make(working_hours=False)
    arguments = {"route": "/heinzel?chat=example", "command": "hz heinzel sessions", "key": "helper-notice"}
    assert service.emit_once(NoticeKind.HEINZEL, "Check in", "Ready", helper=helper, **arguments)
    replacement = helper.model_copy(update={"name": "Renamed", "hat": "green"})
    assert service.emit_once(NoticeKind.HEINZEL, "Changed", "Changed", helper=replacement, **arguments)
    restarted = make(working_hours=False)
    notice = restarted.recent()[0]
    assert notice.helper == helper
    assert notice.title == "Check in"
    assert restarted.has_once("helper-notice") is True
    assert restarted.post_native() == 1
    assert runner.commands[-1][-2] == "Mira: Check in"
    assert restarted.post_native() == 0
    legacy = notice.model_dump()
    legacy.pop("helper")
    assert Notice.model_validate(legacy).helper is None


def test_expiry_and_withdrawal_stop_a_stale_claim_and_keep_the_replay_key(make, clock):
    service = make(working_hours=False)
    arguments = {"route": "/", "command": "hz org agenda", "key": "calendar:fixture"}
    assert service.emit_once(
        NoticeKind.HEINZEL, "Meeting", "Soon", expires=clock.now + timedelta(minutes=5), **arguments
    )
    pending = service.due()[0]
    restarted = make(working_hours=False)
    assert restarted.withdraw(arguments["key"])
    assert restarted.deliver([pending.id], Delivery.WEB) == []
    assert restarted.emit_once(NoticeKind.HEINZEL, "Meeting", "Soon", **arguments)
    assert restarted.due() == []
    assert len(restarted.recent()) == 1

    arguments["key"] = "calendar:another"
    assert restarted.emit_once(
        NoticeKind.HEINZEL, "Meeting", "Soon", expires=clock.now + timedelta(minutes=5), **arguments
    )
    pending = restarted.due()[0]
    clock.now += timedelta(minutes=5)
    assert restarted.due() == []
    assert restarted.deliver([pending.id], Delivery.DAEMON) == []


def test_expiring_receipts_compact_without_replaying_on_clock_rollback(make, settings, clock):
    service = make(calendar=True, working_hours=False)
    start = clock.now
    args = {"route": "/", "command": "hz org agenda"}
    assert service.emit_once(
        NoticeKind.CALENDAR, "Meeting", "Soon", key="old", expires=start + timedelta(minutes=5), **args
    )
    clock.now += timedelta(minutes=6)
    assert service.emit_once(
        NoticeKind.CALENDAR, "Meeting", "Next", key="new", expires=clock.now + timedelta(minutes=5), **args
    )
    state = json.loads((settings.state_dir / "notify" / "notices.json").read_text())
    assert len(state["expiring_keys"]) == 1
    clock.now = start
    assert make(calendar=True, working_hours=False).emit_once(
        NoticeKind.CALENDAR, "Meeting", "Soon", key="old", expires=start + timedelta(minutes=5), **args
    )
    assert len(service.recent()) == 2
    assert next(notice for notice in service.recent() if notice.key == "old").held == "expired"


def test_missing_expiring_receipts_fail_closed(make, settings, clock):
    service = make(calendar=True, working_hours=False)
    args = {"route": "/", "command": "hz org agenda", "key": "calendar"}
    assert service.emit_once(NoticeKind.CALENDAR, "Meeting", "Soon", expires=clock.now + timedelta(minutes=5), **args)
    path = settings.state_dir / "notify" / "notices.json"
    state = json.loads(path.read_text())
    del state["expiring_keys"]
    path.write_text(json.dumps(state))
    assert not service.emit_once(
        NoticeKind.CALENDAR, "Meeting", "Soon", expires=clock.now + timedelta(minutes=5), **args
    )
    assert service.store.integrity_problem() is not None


def test_expiring_receipt_capacity_fails_closed(make, settings, clock):
    from heinzel.modules.system.notify._store import MAX_EXPIRING_KEYS

    service = make(calendar=True, working_hours=False)
    args = {"route": "/", "command": "hz org agenda", "key": "first"}
    expiry = clock.now + timedelta(minutes=5)
    assert service.emit_once(NoticeKind.CALENDAR, "Meeting", "Soon", expires=expiry, **args)
    path = settings.state_dir / "notify" / "notices.json"
    state = json.loads(path.read_text())
    state["expiring_keys"] = {f"{number:064x}": expiry.isoformat() for number in range(MAX_EXPIRING_KEYS)}
    path.write_text(json.dumps(state))
    args["key"] = "another"
    assert not service.emit_once(NoticeKind.CALENDAR, "Meeting", "Soon", expires=expiry, **args)
    assert len(service.recent()) == 1


@pytest.mark.parametrize("sound", [False, True])
def test_native_delivery_forwards_the_recorded_identity_and_selected_sound_setting(make, monkeypatch, sound):
    service = make(working_hours=False, sound=sound)
    notifier = Mock(spec=Native)
    notifier.name = "fixture"
    notifier.clicks = True
    notifier.post.return_value = None
    monkeypatch.setattr(service, "_notifier", notifier)
    helper = NoticeHelper(name="Mira", hat="blue", reason="scheduled check-in")
    notice = service.emit(
        NoticeKind.HEINZEL,
        "Check in",
        "Ready",
        route="/heinzel?chat=example",
        command="hz heinzel sessions",
        key="native-helper",
        helper=helper,
    )
    assert notice is not None
    assert service.post_native() == 1
    assert notifier.post.call_args.kwargs == {
        "url": f"http://127.0.0.1:8737/heinzel?chat=example&event={notice.id}",
        "helper": helper,
        "sound": sound,
        "ident": notice.id,
        "thread": notice.route,
    }


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
    assert body == "Read it. Run: hz heinzel drafts --name me:weekly --latest"  # notify-send can't open a link
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


@pytest.mark.timeout(30)  # runs every built-in module's real doctor checks: ~3 s idle, more under load
def test_once_an_hour_the_tick_reports_what_doctor_finds(make_kernel, monkeypatch, clock):
    monkeypatch.setenv("HZ_SYSTEM_NOTIFY__DOCTOR", "true")  # tests turn it off (heinzel.testing)
    sick = Module("sick", "A module whose check fails.")

    @sick.health
    def broken() -> list[Check]:
        """Always degraded."""
        return [Check(name="thing", status=Status.DEGRADED, detail="broken", fix="fix it")]

    kernel = make_kernel(builtins=True, sick=sick)
    service = kernel.resolve(NotifyService)
    monkeypatch.setattr(service, "_notifier", Native(None, detail="none in tests"))
    monkeypatch.setattr(
        service, "_clock", clock
    )  # a pinned clock: the hour passes when the test says so, not under load
    service.post_native()
    assert len(service.recent()) == 1
    assert service.look() == 0  # not again within the hour
    clock.now += timedelta(hours=2)
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


def heinzel_service(  # noqa: PLR0913 - a test double with switches
    settings,
    clock,
    tmp_path,
    said: str,
    *,
    quarantined: bool = False,
    posted: str = "posted",
    stale: bool = False,
    wheel: bool = False,
    build_error: str | None = None,
) -> NotifyService:
    bundle = tmp_path / "HeinzelNotifier.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "notifier").write_bytes(b"app")

    def run(command, timeout=None):
        if "--stdout" in command:
            answer = said if "--status" in command else posted
            Path(command[command.index("--stdout") + 1]).write_text(answer, encoding="utf-8")
        code = 0 if command[0] != "/usr/bin/xattr" or quarantined else 1
        return subprocess.CompletedProcess(command, code, "", "")

    script = None
    if build_error is not None:  # a source checkout whose rebuild fails
        script = tmp_path / "notifier.py"
        script.write_text("", encoding="utf-8")
        inner = run

        def run(command, timeout=None):
            if str(script) in command:
                return subprocess.CompletedProcess(command, 1, "", build_error)
            return inner(command, timeout)

    app = NotifierApp(
        tmp_path / "home", bundled=tmp_path / "none" if wheel else bundle, runner=run, build_script=script
    )
    if stale:  # an older app is installed already
        (app.path / "Contents" / "MacOS").mkdir(parents=True)
        (app.path / "Contents" / "MacOS" / "notifier").write_bytes(b"old app")
    notifier = Native(HEINZEL, str(app.path), clicks=True, app=app)
    return NotifyService(settings, NotifySettings(), ORG, clock=clock, notifier=notifier)


@pytest.mark.macos_specific
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


@pytest.mark.macos_specific
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


@pytest.mark.macos_specific
def test_install_puts_heinzel_notifier_in_place_and_pops_up_a_test(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "authorized")
    installed = service.install()
    assert (installed.notifier, installed.state, installed.shown) == (HEINZEL, "installed", True)
    assert installed.path == str(tmp_path / "home" / "Applications" / "Heinzel Notifier.app")
    assert installed.next == []  # sent and nothing to fix
    assert service.recent() == []  # a test of the notifier, not a notice


def shown(service: NotifyService) -> list[str]:
    return [line.plain for line in render_installed(service.install()).renderables]


@pytest.mark.macos_specific
def test_install_replaces_an_outdated_notifier_and_says_so(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "authorized", stale=True)
    assert shown(service) == [
        "Updated Heinzel Notifier (new icon).",
        "Sent a test notification \u2014 you should see it now.",
        "macOS may show the old icon until you remove Heinzel Notifier there once and run this again.",
    ]
    assert service.notifier.app.inspect().state == "current"


@pytest.mark.macos_specific
def test_install_says_so_when_a_wheel_ships_no_notifier(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "authorized", stale=True, wheel=True)
    lines = shown(service)
    assert lines[0].startswith("Couldn't update Heinzel Notifier: this installation carries no Heinzel Notifier")
    assert "xcode-select --install" in lines[0]
    assert not any("new icon" in line for line in lines)


def test_install_does_not_claim_an_update_when_the_rebuild_fails(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "authorized", stale=True, build_error="swiftc not found")
    lines = shown(service)
    assert lines[0].startswith("Couldn't update Heinzel Notifier: building it failed (swiftc not found)")


@pytest.mark.macos_specific
def test_install_reads_the_notifiers_success_output_as_success(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "authorized", posted="posted error=nil")
    installed = service.install()
    assert (installed.shown, installed.problem) == (True, None)


def test_install_golden_output_when_up_to_date_and_when_refused(settings, clock, tmp_path):
    service = heinzel_service(settings, clock, tmp_path, "authorized")
    service.install()
    assert shown(service) == [
        "Heinzel Notifier is installed and up to date.",
        "Sent a test notification \u2014 you should see it now.",
    ]
    refused = heinzel_service(settings, clock, tmp_path / "other", "denied", posted="denied")
    assert shown(refused) == [
        "Installed Heinzel Notifier.",
        "The test notification failed: " + DENIED,
        ALLOW,
    ]


@pytest.mark.macos_specific
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


def test_durable_keys_survive_repeat_disabled_display_pruning_and_service_restart(make):
    service = make(repeat_hours=0)

    def once(target, key):
        return target.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key=key)

    assert once(service, "owner-a:slot")
    assert once(service, "owner-a:slot")
    assert len(service.recent()) == 1
    for index in range(MAX_NOTICES + 1):
        assert draft(service, key=f"other-{index}") is not None
    assert all(notice.key != "owner-a:slot" for notice in service.recent(500))
    restarted = make(repeat_hours=0)
    assert once(restarted, "owner-a:slot")
    assert all(notice.key != "owner-a:slot" for notice in restarted.recent(500))
    assert once(restarted, "owner-b:slot")
    assert restarted.recent(1)[0].key == "owner-b:slot"


def test_completed_receipt_can_be_forgotten_without_removing_the_display_notice(make):
    service = make(repeat_hours=0)
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    assert service.has_once("slot") is True

    assert service.forget_once("slot")
    assert service.has_once("slot") is True  # The bounded notice list still proves that the effect occurred.
    assert [notice.key for notice in service.recent()] == ["slot"]
    assert service.store._read_state(strict=True).durable_keys == set()


def test_uncertain_notice_lookup_fails_closed_on_damaged_store(make, settings):
    service = make()
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    (settings.state_dir / "notify" / "notices.json").write_text("{damaged", encoding="utf-8")
    assert service.has_once("slot") is None


def test_durable_store_damage_fails_closed_instead_of_forgetting_keys(make, settings):
    service = make()
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    path = settings.state_dir / "notify" / "notices.json"
    path.write_text("{damaged", encoding="utf-8")
    assert not service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    assert draft(service, key="other") is None
    assert path.read_text(encoding="utf-8") == "{damaged"


def test_legacy_writer_overwrite_fails_closed_instead_of_replaying_a_durable_key(make, settings):
    import subprocess
    import sys

    service = make(repeat_hours=0)
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    path = settings.state_dir / "notify" / "notices.json"
    legacy_writer = """
import fcntl
import os
import secrets
import sys
from pathlib import Path
from pydantic import TypeAdapter, ValidationError
from heinzel import Notice

path = Path(sys.argv[1])
adapter = TypeAdapter(list[Notice])
with (path.parent / "notices.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        notices = adapter.validate_json(path.read_bytes())
    except ValidationError:
        notices = []
    temporary = path.with_name(f".notices.{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(adapter.dump_json(notices, indent=1) + b"\\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
"""
    subprocess.run(
        [sys.executable, "-c", legacy_writer, str(path)], check=True, capture_output=True, text=True, timeout=10
    )

    assert not service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    assert path.read_text(encoding="utf-8").strip() == "[]"


def test_interrupted_atomic_notice_write_retries_without_losing_or_duplicating(make, monkeypatch):
    from heinzel.modules.system.notify import _store

    service = make(repeat_hours=0)
    real_write = _store._write_private
    failed = False

    def interrupted(path, text):
        nonlocal failed
        if path == service.store.path and service.store._durable.exists() and not failed:
            failed = True
            msg = "interrupted before atomic replacement"
            raise OSError(msg)
        real_write(path, text)

    monkeypatch.setattr(_store, "_write_private", interrupted)
    args = (NoticeKind.HEINZEL, "Check-in", "Due")
    kwargs = {"route": "/heinzel", "command": "hz", "key": "owner:slot"}
    assert not service.emit_once(*args, **kwargs)
    assert service.recent() == []
    assert service.emit_once(*args, **kwargs)
    assert service.emit_once(*args, **kwargs)
    assert len(service.recent()) == 1


def test_unknown_acknowledgement_after_atomic_commit_replays_without_duplicate(make, monkeypatch):
    from heinzel.modules.system.notify import _store

    service = make(repeat_hours=0)
    real_write = _store._write_private
    failed = False

    def interrupted(path, text):
        nonlocal failed
        real_write(path, text)
        if path == service.store.path and service.store._durable.exists() and not failed:
            failed = True
            msg = "interrupted after atomic replacement"
            raise OSError(msg)

    monkeypatch.setattr(_store, "_write_private", interrupted)
    args = (NoticeKind.HEINZEL, "Check-in", "Due")
    kwargs = {"route": "/heinzel", "command": "hz", "key": "owner:slot"}
    assert not service.emit_once(*args, **kwargs)
    assert len(service.recent()) == 1
    assert make(repeat_hours=0).emit_once(*args, **kwargs)
    assert len(service.recent()) == 1


def leftover_service(settings, clock, tmp_path, ended: list[int]) -> NotifyService:
    bundle = tmp_path / "HeinzelNotifier.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "notifier").write_bytes(b"app")
    table = (
        "PID\tStatus\tLabel\n"
        "7001\t0\tapplication.dev.heinzel.notifier.1.2.AAAA\n"
        "7002\t0\tapplication.dev.heinzel.notifier.1.3.BBBB\n"
        "7003\t0\tcom.example.stranger\n"
    )

    def run(command, timeout=None):
        if command[0].endswith("launchctl"):
            return subprocess.CompletedProcess(command, 0, table, "")
        if command[0].endswith("/ps"):
            return subprocess.CompletedProcess(command, 0, {"7001": "2-00:00:00", "7002": "00:04"}[command[-1]], "")
        if "--stdout" in command:
            Path(command[command.index("--stdout") + 1]).write_text("authorized", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0 if command[0] != "/usr/bin/xattr" else 1, "", "")

    app = NotifierApp(tmp_path / "home", bundled=bundle, runner=run, kill=lambda pid, sig: ended.append(pid))
    return NotifyService(
        settings, NotifySettings(), ORG, clock=clock, notifier=Native(HEINZEL, str(app.path), clicks=True, app=app)
    )


@pytest.mark.macos_specific
def test_doctor_counts_leftover_notifier_processes_and_names_the_fix(settings, clock, tmp_path):
    ended: list[int] = []
    service = leftover_service(settings, clock, tmp_path, ended)
    assert service.notifier.app is not None
    service.notifier.app.ensure()
    checks = {check.name: check for check in service.health()}
    check = checks["heinzel notifier processes"]
    assert check.status is Status.DEGRADED
    assert check.detail.startswith("1 Heinzel Notifier process(es) have run for over 5 minutes")
    assert check.fix == "hz system notify clean"
    assert ended == []  # doctor reads, it never kills


def test_every_daemon_tick_ends_the_leftovers_and_leaves_a_notification_being_posted(settings, clock, tmp_path):
    ended: list[int] = []
    service = leftover_service(settings, clock, tmp_path, ended)
    assert service.post_native() == 0
    assert ended == [7001]  # 7002 is four seconds old, 7003 is a stranger
    assert service.clean().ended == 1
    assert ended == [7001, 7001]


def test_disabled_native_notifications_never_maintain_the_desktop_app(settings, clock, tmp_path, monkeypatch):
    service = leftover_service(settings, clock, tmp_path, [])
    service.settings = NotifySettings(native=False)
    app = service.notifier.app
    assert app is not None

    def reject_maintenance():
        pytest.fail("disabled native notifications reached desktop maintenance")

    for method in ("ensure", "clean", "unregister_strays"):
        monkeypatch.setattr(app, method, reject_maintenance)
    watched = []
    service.watcher = lambda: watched.append(True) or 0
    assert service.post_native() == 0
    assert watched == [True]


def test_disabled_native_notifications_never_discover_the_host_notifier(make, monkeypatch):
    service = make(native=False)
    service._notifier = None

    def reject_discovery(_self):
        pytest.fail("disabled native notifications discovered the desktop notifier")

    monkeypatch.setattr(type(service), "notifier", property(reject_discovery))
    assert service.post_native() == 0


@pytest.mark.macos_specific
def test_the_tick_makes_macos_forget_stray_copies_once_a_day(settings, clock, tmp_path):
    forgotten: list[str] = []
    ended: list[int] = []
    service = leftover_service(settings, clock, tmp_path, ended)
    assert service.notifier.app is not None
    app = service.notifier.app
    app.unregister_strays = lambda: forgotten.append("x") or 3  # type: ignore[method-assign]
    service.post_native()
    service.post_native()
    assert forgotten == ["x"]  # the second tick, a minute later, doesn't ask macOS again
    clock.now += timedelta(days=1, minutes=1)
    service.post_native()
    assert forgotten == ["x", "x"]


@pytest.mark.macos_specific
def test_the_tick_installs_a_missing_notifier_without_any_command(settings, clock, tmp_path):
    service = leftover_service(settings, clock, tmp_path, [])
    assert service.notifier.app is not None
    assert not service.notifier.app.path.exists()
    service.post_native()
    assert service.notifier.app.path.is_dir()  # an update, or a daemon installed first, heals on its next tick


def _store_files(directory):

    try:
        root = directory.lstat()
    except FileNotFoundError:
        return None
    result = {".": stat.S_IMODE(root.st_mode)}
    if stat.S_ISLNK(root.st_mode):
        result["link"] = directory.readlink()
        return result
    for path in directory.iterdir():
        info = path.lstat()
        content = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
        target = path.readlink() if stat.S_ISLNK(info.st_mode) else None
        result[path.name] = (content, stat.S_IMODE(info.st_mode), info.st_ino, target)
    return result


@pytest.mark.parametrize("damage", ["legacy", "missing", "malformed", "invalid-keys", "deep"])
def test_health_reports_durable_store_damage_without_writes_or_disclosure(make, monkeypatch, caplog, damage):
    import json

    from heinzel.modules.system.notify import _store

    service = make(native=False)
    private_content = "fixture-private-notice-content"
    assert service.emit_once(
        NoticeKind.HEINZEL, private_content, private_content, route="/heinzel", command="hz", key="private-slot"
    )
    path = service.store.path
    notices = [notice.model_dump(mode="json") for notice in service.recent()]
    if damage == "legacy":
        path.write_text(json.dumps(notices), encoding="utf-8")
    elif damage == "missing":
        path.unlink()
    elif damage == "malformed":
        path.write_text('{"notices": "' + private_content, encoding="utf-8")
    elif damage == "deep":
        path.write_text("[" * 1500 + "]" * 1500, encoding="utf-8")
    else:
        path.write_text(json.dumps({"notices": notices, "durable_keys": 42}), encoding="utf-8")
    before = _store_files(service.store.directory)

    def no_write(*args, **kwargs):
        pytest.fail("doctor must not write or acquire the notification writer lock")

    monkeypatch.setattr(_store, "_write_private", no_write)
    monkeypatch.setattr(service.store, "_locked", no_write)
    caplog.clear()
    checks = service.health()
    assert len(checks) == 1
    assert checks[0].name == "notification store"
    assert checks[0].status is Status.DOWN
    assert "cannot be verified" in checks[0].detail
    assert "coherent trusted backup" in checks[0].fix
    assert "Do not delete" in checks[0].fix
    assert private_content not in repr(checks)
    assert private_content not in caplog.text
    assert caplog.text == ""
    assert _store_files(service.store.directory) == before


def test_health_does_not_hide_durable_integrity_failure_when_notifications_are_off(make):
    service = make()
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    service.store.path.write_text("[]", encoding="utf-8")
    disabled = make(enabled=False)
    assert disabled.health()[0].status is Status.DOWN


def test_health_accepts_valid_durable_store_without_changes(make):
    service = make(native=False)
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    before = _store_files(service.store.directory)
    assert all(check.status is not Status.DOWN for check in service.health())
    assert _store_files(service.store.directory) == before


def test_health_leaves_uninitialized_and_valid_legacy_stores_unchanged(make):
    service = make(native=False)
    assert _store_files(service.store.directory) is None
    assert all(check.status is not Status.DOWN for check in service.health())
    assert _store_files(service.store.directory) is None
    assert draft(service) is not None
    assert not service.store._durable.exists()
    before = _store_files(service.store.directory)
    assert all(check.status is not Status.DOWN for check in service.health())
    assert _store_files(service.store.directory) == before


@pytest.mark.parametrize("unreadable", ["durable-notices", "notices.json"])
def test_health_reports_unreadable_files_without_disclosing_error(make, monkeypatch, unreadable):
    import os

    from heinzel.modules.system.notify import _store

    service = make(native=False)
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    private_content = "fixture-private-path"
    before = _store_files(service.store.directory)
    original = os.open

    def denied(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError(private_content)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(_store.os, "open", denied)
    checks = service.health()
    assert checks[0].status is Status.DOWN
    assert private_content not in repr(checks)
    assert _store_files(service.store.directory) == before


@pytest.mark.parametrize(
    "unsafe",
    [
        "dangling-marker",
        "linked-marker",
        "fifo-marker",
        "linked-state",
        "fifo-state",
        "public-marker",
        "public-state",
        "hardlinked-marker",
        "hardlinked-state",
        "linked-directory",
        "public-directory",
        "unsafe-parent",
    ],
)
def test_health_rejects_unsafe_paths_without_locking_or_changes(make, settings, monkeypatch, unsafe):
    import os

    from heinzel.modules.system.notify import _store

    service = make(native=False)
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    directory = service.store.directory
    marker = service.store._durable
    state = service.store.path
    if unsafe == "linked-directory":
        physical = directory.with_name("physical-notify")
        directory.rename(physical)
        directory.symlink_to(physical, target_is_directory=True)
    elif unsafe == "public-directory":
        directory.chmod(0o755)
    elif unsafe == "unsafe-parent":
        settings.state_dir.chmod(0o777)
    else:
        target = marker if "marker" in unsafe else state
        if unsafe.startswith("public"):
            target.chmod(0o644)
        elif unsafe.startswith("hardlinked"):
            os.link(target, target.with_name(target.name + ".linked"))
        elif unsafe.startswith("fifo"):
            target.unlink()
            os.mkfifo(target, 0o600)
        else:
            physical = target.with_name(target.name + ".physical")
            target.rename(physical)
            target.symlink_to(physical if unsafe != "dangling-marker" else directory / "missing")
    before = _store_files(directory)
    parent_mode = stat.S_IMODE(settings.state_dir.stat().st_mode)

    def no_write(*args, **kwargs):
        pytest.fail("doctor must never write or acquire the notification writer lock")

    monkeypatch.setattr(service.store, "_locked", no_write)
    monkeypatch.setattr(_store, "_write_private", no_write)
    checks = service.health()
    assert checks[0].status is Status.DOWN
    assert _store_files(directory) == before
    assert stat.S_IMODE(settings.state_dir.stat().st_mode) == parent_mode


@pytest.mark.parametrize("fields", [{}, {"notices": []}, {"durable_keys": []}])
def test_missing_durable_fields_fail_closed_for_health_and_writes(make, fields):
    import json

    service = make(native=False)
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    service.store.path.write_text(json.dumps(fields), encoding="utf-8")
    before = service.store.path.read_bytes()
    assert service.health()[0].status is Status.DOWN

    def no_replay(notices):
        pytest.fail("unproven replay evidence must not allow a notice mutation")

    with pytest.raises(OSError, match="durable notification store"):
        service.store.change_once(service._clock(), "new-slot", no_replay)
    with pytest.raises(OSError, match="durable notification store"):
        service.store.has_once("slot")
    assert service.store.path.read_bytes() == before


def test_oversized_health_verification_is_unavailable_without_a_new_write_limit(make, monkeypatch):
    from heinzel.modules.system.notify import _store

    service = make(native=False)
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    before = _store_files(service.store.directory)
    monkeypatch.setattr(_store, "MAX_HEALTH_BYTES", 16)
    finding = service.health()[0]
    assert finding.status is Status.DOWN
    assert "verification is unavailable" in finding.detail
    assert "health-check read limit" in finding.detail
    assert service.store._read_state(strict=True).durable_keys
    assert _store_files(service.store.directory) == before


def test_health_reads_the_verified_descriptor_when_its_path_is_replaced(make, monkeypatch):
    import os

    from heinzel.modules.system.notify import _store

    service = make(native=False)
    assert service.emit_once(NoticeKind.HEINZEL, "Check-in", "Due", route="/heinzel", command="hz", key="slot")
    state = service.store.path
    original_bytes = state.read_bytes()
    original_inode = state.stat().st_ino
    retained = state.with_name("retained-state")
    actual_open, actual_fstat = os.open, os.fstat
    opens = 0
    replaced = False

    def checked_open(path, flags, *args, **kwargs):
        nonlocal opens
        if path in {"durable-notices", "notices.json"}:
            assert flags & os.O_NOFOLLOW
            assert flags & os.O_NONBLOCK
            assert isinstance(kwargs.get("dir_fd"), int)
        if path == "notices.json":
            opens += 1
            assert opens == 1, "health must not reopen the pathname after verification"
        return actual_open(path, flags, *args, **kwargs)

    def replace_after_fstat(descriptor):
        nonlocal replaced
        info = actual_fstat(descriptor)
        if info.st_ino == original_inode and not replaced:
            state.rename(retained)
            state.symlink_to(state.with_name("missing-state"))
            replaced = True
        return info

    def no_path_read(*args, **kwargs):
        pytest.fail("health must decode bytes from its verified descriptor, not read the pathname again")

    monkeypatch.setattr(_store.os, "open", checked_open)
    monkeypatch.setattr(_store.os, "fstat", replace_after_fstat)
    monkeypatch.setattr(service.store, "_read_state", no_path_read)
    assert all(check.status is not Status.DOWN for check in service.health())
    assert replaced
    assert opens == 1
    assert retained.read_bytes() == original_bytes
    assert stat.S_IMODE(retained.stat().st_mode) == 0o600
