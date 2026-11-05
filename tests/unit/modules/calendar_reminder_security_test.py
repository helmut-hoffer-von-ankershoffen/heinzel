"""Regression checks for reminder profile isolation, recovery and delivery boundaries."""

import plistlib
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from heinzel import Delivery, HeinzelError, NoticeKind
from heinzel.modules.org import _reminder_service as supervisor
from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify import _policy as policy
from heinzel.modules.system.notify._native import Native

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def verified_calendar_installation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from heinzel.modules.org import _reminder_service

    prefix = tmp_path / "verified-tools/heinzel"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(
        _reminder_service,
        "_installation",
        lambda: SimpleNamespace(
            prefix=prefix,
            startup_state_matches=lambda: True,
        ),
    )


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_supervisor_pins_profile_paths_and_never_reports_or_removes_another_profile(tmp_path, monkeypatch, platform):
    executable = tmp_path / "tool" / "hz"
    commands = []
    monkeypatch.setattr(supervisor, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(supervisor.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(supervisor, "_shim", lambda: executable)
    monkeypatch.setattr(
        supervisor,
        "_run",
        lambda cmd: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "state = running", ""),
    )
    config = tmp_path / "selected.toml"
    config.write_text("[system.notify]\ncalendar = true\n")
    monkeypatch.setenv("HZ_CONFIG", str(config))
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path / "profile-a"))
    monkeypatch.setenv("HZ_STATE_DIR", str(tmp_path / "state-a"))
    monkeypatch.setenv("HZ_DATA_DIR", str(tmp_path / "data-a"))
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_TOKEN", "must-not-persist")
    assert "installed" in supervisor.install()
    first = supervisor.path_for(platform, tmp_path)
    content = first.read_text()
    if platform == "darwin":
        env = plistlib.loads(content.encode())["EnvironmentVariables"]
        assert env["HZ_CONFIG"] == str(config)
        assert env["HZ_CONFIG_HOME"] == str(tmp_path / "profile-a")
        assert env["HZ_STATE_DIR"] == str(tmp_path / "state-a")
        assert env["HZ_DATA_DIR"] == str(tmp_path / "data-a")
    else:
        for key, value in {
            "HZ_CONFIG": config,
            "HZ_CONFIG_HOME": tmp_path / "profile-a",
            "HZ_STATE_DIR": tmp_path / "state-a",
            "HZ_DATA_DIR": tmp_path / "data-a",
        }.items():
            assert f'Environment="{key}={value}"' in content
    assert "must-not-persist" not in content
    assert "HZ_CREDENTIAL_EXAMPLE_TOKEN" not in content
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path / "profile-b"))
    assert supervisor.path_for(platform, tmp_path) != first
    assert supervisor.status() == "not installed"
    before = len(commands)
    supervisor.uninstall()
    assert len(commands) == before
    assert first.read_text() == content


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_failed_load_can_retry_and_never_loaded_owned_file_can_be_removed(tmp_path, monkeypatch, platform):
    path = tmp_path / "unit"
    monkeypatch.setattr(supervisor, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(supervisor, "path_for", lambda *_: path)
    monkeypatch.setattr(supervisor, "_shim", lambda: tmp_path / "hz")
    failing = True
    loaded = False

    def run(command):
        nonlocal loaded
        if "bootstrap" in command or "enable" in command:
            if failing:
                return subprocess.CompletedProcess(command, 1, "", "load failed")
            loaded = True
        if "bootout" in command or "disable" in command:
            return subprocess.CompletedProcess(command, 1, "", "never loaded")
        if "print" in command:
            return subprocess.CompletedProcess(
                command,
                0 if loaded else 113,
                "state = running" if loaded else "",
                "Could not find service" if not loaded else "",
            )
        if "show" in command:
            return subprocess.CompletedProcess(command, 0, "not-found", "")
        if "is-active" in command:
            return subprocess.CompletedProcess(command, 0 if loaded else 3, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(supervisor, "_run", run)
    with pytest.raises(HeinzelError, match="loaded"):
        supervisor.install()
    assert path.exists()
    assert "removed" in supervisor.uninstall()
    assert not path.exists()
    with pytest.raises(HeinzelError):
        supervisor.install()
    failing = False
    assert "installed" in supervisor.install()
    assert supervisor.status() == "running"


def calendar_service(settings):
    now = [datetime(2026, 11, 4, 10, tzinfo=UTC)]
    native = Mock(spec=Native)
    native.name = "fixture"
    native.clicks = False
    native.post.return_value = None
    service = NotifyService(
        settings, NotifySettings(calendar=True, working_hours=False), {}, clock=lambda: now[0], notifier=native
    )
    service.calendar_valid = lambda _: {"meeting"}
    assert service.emit_once(
        NoticeKind.CALENDAR, "Meeting", "", route="/", command="", key="meeting", expires=now[0] + timedelta(seconds=1)
    )
    return service, now, native


def test_provider_validation_crossing_expiry_cannot_claim(settings):
    service, now, _ = calendar_service(settings)
    [notice] = service.recent()

    def delayed_validation(_):
        now[0] += timedelta(seconds=1)
        return {notice.key}

    service.calendar_valid = delayed_validation
    assert service.deliver([notice.id], Delivery.WEB) == []
    assert service.recent()[0].delivered_by is None


def test_expiry_is_rechecked_immediately_before_native_presentation(settings, monkeypatch):
    service, now, native = calendar_service(settings)
    monkeypatch.setattr(service, "_end_leftover_instances", lambda: None)
    native.clicks = True

    def slow_url(_):
        now[0] += timedelta(seconds=1)

    service._ui_url = slow_url
    assert service.post_native() == 0
    native.post.assert_not_called()
    assert service.recent()[0].delivered_by is None


@pytest.mark.parametrize("changes", [{"quiet": True}, {"native": False}, {"calendar": False}, {"enabled": False}])
def test_cached_policy_cannot_override_current_notification_settings(settings, monkeypatch, changes):
    service, _, native = calendar_service(settings)
    monkeypatch.setattr(service, "_end_leftover_instances", lambda: None)
    service._refresh_settings = lambda: NotifySettings(calendar=True, working_hours=False).model_copy(update=changes)
    assert service.post_native() == 0
    native.post.assert_not_called()
    assert service.recent()[0].delivered_by is None


def test_policy_changes_during_provider_validation_are_respected(settings):
    service, _, _ = calendar_service(settings)
    [notice] = service.recent()

    def validation(_):
        service._refresh_settings = lambda: NotifySettings(calendar=True, quiet=True)
        return {notice.key}

    service.calendar_valid = validation
    assert service.deliver([notice.id], Delivery.WEB) == []


def test_fresh_profile_hours_and_timezone_govern_existing_notice(settings):
    service, now, _ = calendar_service(settings)
    [notice] = service.recent()
    service._refresh_settings = lambda: NotifySettings(calendar=True, working_hours=True)
    service._refresh_hours = lambda: policy.working_hours({"working_hours": "12-22", "timezone": "Europe/Berlin"}, UTC)
    assert service.deliver([notice.id], Delivery.WEB) == []
    assert service.hours.zone == ZoneInfo("Europe/Berlin")
    assert policy.next_start(service.hours, now[0]) == datetime(2026, 11, 4, 12, tzinfo=ZoneInfo("Europe/Berlin"))


def test_setup_requires_explicit_confirmation_before_supervisor_mutation(monkeypatch):
    from heinzel.modules import org

    install = Mock(return_value="installed")
    monkeypatch.setattr(org, "install_reminders", install)
    with pytest.raises(HeinzelError, match="Confirm"):
        org.calendar_reminders_install()
    install.assert_not_called()
    assert org.calendar_reminders_install(confirm=True) == "installed"


def test_user_policy_is_reloaded_from_selected_file_after_validation(tmp_path, settings, monkeypatch):
    from heinzel import Kernel

    path = tmp_path / "profile.toml"
    monkeypatch.setenv("HZ_CONFIG", str(path))
    path.write_text("[system.notify]\ncalendar = true\nworking_hours = false\n")
    service = NotifyService.for_user(Mock(spec=Kernel))
    now = datetime(2026, 11, 4, 10, tzinfo=UTC)
    service._clock = lambda: now
    service.store.directory = settings.state_dir / "notify"
    service.emit_once(
        NoticeKind.CALENDAR, "Meeting", "", route="/", command="", key="meeting", expires=now + timedelta(minutes=1)
    )
    [notice] = service.recent()

    def read(_):
        path.write_text(
            "[system.notify]\ncalendar = true\nworking_hours = true\n"
            '[org]\nworking_hours = "07-22"\ntimezone = "Europe/Berlin"\n'
        )
        service._clock = lambda: datetime(2026, 11, 4, 22, tzinfo=UTC)
        return {"meeting"}

    service.calendar_valid = read
    assert service.deliver([notice.id], Delivery.WEB) == []
    assert (service.hours.start, service.hours.end, str(service.hours.zone)) == (7, 22, "Europe/Berlin")


@pytest.mark.parametrize("hour", [22, 23, 0, 6])
def test_explicit_berlin_quiet_interval_22_to_07_holds_reminders(hour):
    hours = policy.working_hours({"working_hours": "07-22", "timezone": "Europe/Berlin"}, UTC)
    instant = datetime(2026, 11, 4, hour, tzinfo=ZoneInfo("Europe/Berlin"))
    assert policy.next_start(hours, instant) is not None


def test_supervised_execution_checks_exact_profile_binding(tmp_path, monkeypatch):
    from heinzel.modules import org

    worker = Mock()
    monkeypatch.setattr(org, "run_reminders", worker)
    monkeypatch.delenv("HZ_REMINDER_PROFILE", raising=False)
    with pytest.raises(HeinzelError, match="bound"):
        org.calendar_reminder_worker(Mock())
    worker.assert_not_called()
    monkeypatch.setenv("HZ_REMINDER_PROFILE", supervisor.Profile.selected().identity)
    org.calendar_reminder_worker(Mock())
    worker.assert_called_once()
    monkeypatch.setenv("HZ_STATE_DIR", str(tmp_path / "different-state"))
    with pytest.raises(HeinzelError, match="bound"):
        org.calendar_reminder_worker(Mock())
    worker.assert_called_once()


@pytest.mark.parametrize("foreign", ["modified", "link", "writable"])
def test_recovery_refuses_foreign_or_unsafe_definition(tmp_path, monkeypatch, foreign):
    path = tmp_path / "unit"
    executable = tmp_path / "hz"
    monkeypatch.setattr(supervisor, "path_for", lambda *_: path)
    monkeypatch.setattr(supervisor, "_shim", lambda: executable)
    content = supervisor.definition(supervisor.sys.platform, executable)
    if foreign == "link":
        other = tmp_path / "other"
        other.write_text(content)
        path.symlink_to(other)
    else:
        path.write_text(content if foreign == "writable" else "foreign content")
        path.chmod(0o666 if foreign == "writable" else 0o600)
    run = Mock()
    monkeypatch.setattr(supervisor, "_run", run)
    for action in (supervisor.install, supervisor.uninstall):
        with pytest.raises(HeinzelError):
            action()
    run.assert_not_called()
    assert path.exists()


def test_stop_failure_is_not_mistaken_for_an_unloaded_service(tmp_path, monkeypatch):
    path = tmp_path / "unit"
    monkeypatch.setattr(supervisor, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(supervisor, "path_for", lambda *_: path)
    monkeypatch.setattr(supervisor, "_shim", lambda: tmp_path / "hz")
    path.write_text(supervisor.definition("darwin", tmp_path / "hz"))
    monkeypatch.setattr(
        supervisor, "_run", lambda command: subprocess.CompletedProcess(command, 113, "", "Permission denied")
    )
    with pytest.raises(HeinzelError, match="stopped"):
        supervisor.uninstall()
    assert path.exists()


def test_consent_changes_while_waiting_for_notification_lock_prevent_claim(settings):
    service, _, _ = calendar_service(settings)
    [notice] = service.recent()
    change = service.store.change

    def locked(now, mutate):
        service._refresh_settings = lambda: NotifySettings(calendar=False)
        return change(now, mutate)

    service.store.change = locked
    assert service.deliver([notice.id], Delivery.WEB) == []
    assert service.recent()[0].delivered_by is None


def test_non_regular_owned_definition_cannot_block_recovery(tmp_path):
    import os
    import sys

    fifo = tmp_path / "unit-fifo"
    os.mkfifo(fifo)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "from heinzel import HeinzelError; "
                "from heinzel.modules.org._reminder_service import _owned\n"
                "try: _owned(Path(sys.argv[1]), '')\n"
                "except HeinzelError: print('refused')\n"
            ),
            str(fifo),
        ],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
