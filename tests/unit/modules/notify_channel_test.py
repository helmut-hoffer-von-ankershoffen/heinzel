"""The native channel (ADR 0112): what each receipt says, and that nothing is claimed that wasn't observed."""

import pathlib
import subprocess

import pytest

from heinzel import ChannelNotice, Settings
from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify._channel import SENDER, NativeChannel, identity
from heinzel.modules.system.notify._macos import NotifierApp
from heinzel.modules.system.notify._native import HEINZEL, Native

pytestmark = pytest.mark.unit

ROUTE = "/heinzel?chat=hz-abc&event=n-1"


def notice(**values: object) -> ChannelNotice:
    fields: dict[str, object] = {
        "key": "n-1",
        "helper": "Mira",
        "title": "Mira checked in",
        "body": "Due.",
        "route": ROUTE,
        "hat": "blue",
        "reason": "scheduled check-in",
    }
    return ChannelNotice.model_validate(fields | values)


def heinzel(tmp_path, answers: dict[str, str], code: int = 0):
    """A service over Heinzel Notifier whose app answers by its first argument; the commands it was started with."""
    bundle = tmp_path / "HeinzelNotifier.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "notifier").write_bytes(b"app")
    commands: list[list[str]] = []

    def run(command, timeout=None):
        commands.append(list(command))
        if "--stdout" in command:
            arguments = command[command.index("--args") + 1 :]
            key = next((flag for flag in ("--status", "--delivered") if flag in arguments), "--message")
            pathlib.Path(command[command.index("--stdout") + 1]).write_text(
                answers.get(key, "authorized"), encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, code, "", "")

    app = NotifierApp(tmp_path / "home", bundled=bundle, runner=run)
    notifier = Native(HEINZEL, str(app.path), clicks=True, app=app)
    settings = Settings(data_dir=tmp_path, state_dir=tmp_path / "state")
    service = NotifyService(
        settings, NotifySettings(), {}, notifier=notifier, ui_url=lambda route: f"http://127.0.0.1:18737{route}"
    )
    return service, commands


def test_a_notification_macos_holds_is_delivered_with_the_helpers_face_sound_and_exact_chat(tmp_path):
    service, commands = heinzel(tmp_path, {"--message": "posted", "--delivered": "n-1,other"})
    receipt = service.channel.deliver(notice())
    assert (receipt.channel, receipt.status, receipt.reference) == ("native", "delivered", "n-1")
    sent = next(c for c in commands if "--message" in c)
    arguments = sent[sent.index("--args") + 1 :]
    assert arguments[arguments.index("--open") + 1] == f"http://127.0.0.1:18737{ROUTE}"  # the exact chat
    assert arguments[arguments.index("--subtitle") + 1] == "Mira · scheduled check-in"
    assert arguments[arguments.index("--hat") + 1] == "blue"
    assert arguments[arguments.index("--id") + 1] == "n-1"
    assert arguments[arguments.index("--thread") + 1] == "/heinzel?chat=hz-abc"
    assert "--sound" in arguments


def test_accepted_but_not_held_is_only_queued(tmp_path):
    service, _ = heinzel(tmp_path, {"--message": "posted", "--delivered": "none"})
    assert service.channel.deliver(notice()).status == "queued"


def test_a_refusal_is_refused_and_safe_to_retry(tmp_path):
    service, _ = heinzel(tmp_path, {"--message": "denied", "--status": "authorized", "--delivered": "none"})
    receipt = service.channel.deliver(notice())
    assert receipt.status == "refused"
    assert receipt.detail is not None
    assert "Notifications" in receipt.detail
    asked, _ = heinzel(tmp_path / "second", {"--message": "posted", "--status": "not-determined"})
    assert asked.channel.deliver(notice()).status == "refused"


def test_an_unanswered_notifier_is_unknown_and_never_claimed_delivered(tmp_path):
    service, _ = heinzel(tmp_path, {"--message": "posted", "--status": "authorized"}, code=124)
    assert service.channel.deliver(notice()).status == "unknown"


def test_a_notice_no_helper_wrote_shows_without_a_name_or_face(tmp_path):
    service, commands = heinzel(tmp_path, {"--message": "posted", "--delivered": "d-1"})
    service.channel.deliver(notice(key="d-1", helper=SENDER, hat=None, reason=None, route="/doctor"))
    sent = next(c for c in commands if "--message" in c)
    assert "--subtitle" not in sent
    assert "--hat" not in sent


def test_a_key_macos_cannot_hold_becomes_a_digest_and_the_same_key_the_same_identity():
    assert identity("n-1") == "n-1"
    first = identity("x" * 200)
    assert first == identity("".join(["x"] * 200))  # the same key, built again, is the same identity
    assert len(first) == 40
    assert identity("a b/c") != identity("a b/d")


def test_the_channel_is_available_only_when_allowed_and_asked_for(tmp_path):
    service, _ = heinzel(tmp_path, {"--status": "authorized banners=banner sound=enabled"})
    assert service.channel.available() is True
    denied, _ = heinzel(tmp_path / "d", {"--status": "denied"})
    assert denied.channel.available() is False
    off = NotifyService(
        Settings(data_dir=tmp_path, state_dir=tmp_path / "off"),
        NotifySettings(native=False),
        {},
        notifier=Native("notify-send", "/usr/bin/notify-send"),
    )
    assert off.channel.available() is False
    assert isinstance(off.channel, NativeChannel)


def test_another_notifier_only_queues(tmp_path):
    ran: list[object] = []

    def runner(command):
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    service = NotifyService(
        Settings(data_dir=tmp_path, state_dir=tmp_path / "s"),
        NotifySettings(),
        {},
        notifier=Native("notify-send", "/usr/bin/notify-send", runner=runner),
    )
    assert service.channel.deliver(notice()).status == "queued"
    assert ran
