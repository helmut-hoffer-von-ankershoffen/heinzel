"""The 0.9.55 desktop fixes: no false "is open", a smooth flavor switch, update on quit, never a silent exit.

Offline and without a real app: processes, the quit, `open` and the installer are fakes. Whether a real Mac quits,
replaces and reopens the app needs a run on a Mac with a signed build.
"""

import ctypes
import errno
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from heinzel import Settings, __version__
from heinzel.heads import _desktop_installation as installer
from heinzel.heads import _desktop_native as native_module
from heinzel.heads import _desktop_release
from heinzel.heads import _desktop_widget as widget
from heinzel.heads.cli import _app
from heinzel.kernel import CorePin
from heinzel.kernel import _desktop_app as desktop_app
from heinzel.kernel._desktop_app import DesktopApp

pytestmark = pytest.mark.unit

BUNDLE = Path("/Applications/Heinzelmaennchen.app")
HEINZEL = widget.ExecutableIdentity(42, BUNDLE / "Contents/MacOS/Heinzel", 100, 7)


# --- detection: an unrelated program updated while it runs never makes Heinzel look open ---------------------------


def _processes(monkeypatch, pids: bytes, executable) -> None:
    monkeypatch.setattr(widget.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, pids))
    native = SimpleNamespace(executable=executable, zombie=lambda _pid: False)
    monkeypatch.setattr(widget, "Native", lambda: native)


def test_a_process_whose_executable_was_deleted_does_not_make_any_bundle_in_use(monkeypatch):
    def executable(pid):
        if pid == 41:  # e.g. a browser helper whose versioned folder an update replaced while it runs
            raise native_module.ImageGoneError
        return HEINZEL

    _processes(monkeypatch, b"41\n42\n", executable)
    assert widget.inventory() == (HEINZEL,)
    assert widget.in_use(Path("/Applications/Other.app")) is False
    assert widget.in_use(BUNDLE) is True  # the real app is still seen


def test_any_other_unreadable_process_still_counts_as_in_use(monkeypatch):
    def executable(pid):
        raise native_module.NativeUnavailableError

    _processes(monkeypatch, b"41\n", executable)
    assert widget.in_use(BUNDLE) is True  # fail closed: unknown is never idle


def test_only_a_missing_path_of_the_same_live_process_is_an_image_gone(monkeypatch):
    def fake(error: int, generations: list[tuple[int, int]]):
        probe = native_module.Native.__new__(native_module.Native)
        answers = iter(generations)
        probe.generation = lambda _pid: next(answers)  # type: ignore[method-assign]

        def pidpath(_token, _buffer, _size):
            ctypes.set_errno(error)
            return 0

        probe._pidpath = pidpath  # type: ignore[attr-defined]
        return probe

    with pytest.raises(native_module.ImageGoneError):
        fake(errno.ENOENT, [(9, 1), (9, 1)]).executable(41)
    with pytest.raises(native_module.NativeUnavailableError) as other:
        fake(errno.EPERM, [(9, 1), (9, 1)]).executable(41)
    assert not isinstance(other.value, native_module.ImageGoneError)
    with pytest.raises(native_module.NativeUnavailableError) as reused:
        fake(errno.ENOENT, [(9, 1), (9, 2)]).executable(41)  # the PID now belongs to another process
    assert not isinstance(reused.value, native_module.ImageGoneError)


def test_the_native_widget_alone_does_not_make_the_app_open(monkeypatch):
    lines = [str(BUNDLE / widget.WIDGET_EXECUTABLE).encode()]
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, b"\n".join(lines))
    )
    assert installer.is_open(BUNDLE) is False
    lines.append(str(BUNDLE / "Contents/MacOS/Heinzel").encode())
    assert installer.is_open(BUNDLE) is True


# --- never a silent exit -----------------------------------------------------------------------------------------


def _cli(monkeypatch, tmp_path, install):
    monkeypatch.setattr(installer, "install_desktop", install)
    monkeypatch.setattr(_app, "desktop_executable", lambda _kernel: tmp_path / "hz")
    monkeypatch.setattr(
        _app, "_plan", lambda error, keep_quarantine: (SimpleNamespace(close=lambda complete: None), BUNDLE.parent)
    )
    monkeypatch.setattr(_desktop_release.sys, "platform", "darwin")
    output, error = Console(width=200, record=True), Console(stderr=True, width=200, record=True)
    return _app.desktop_group(None, output, error), output, error  # type: ignore[arg-type]


@pytest.mark.parametrize("failure", [RuntimeError("identity unverifiable"), ValueError("damaged record")])
def test_an_unexpected_installer_error_is_said_in_two_lines_on_every_run(monkeypatch, tmp_path, failure):
    def install(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(_app, "_switching", lambda flavor: False)
    for _ in range(2):  # the second run tells the same as the first
        group, _, error = _cli(monkeypatch, tmp_path, install)
        result = CliRunner().invoke(group, ["install", "--flavor", "native"])
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Desktop installation failed." in error.export_text()


# --- the flavor switch quits, replaces and reopens without asking ------------------------------------------------


def _installed(monkeypatch, *, variant: str, release: str, open_now: bool, quits_ok: bool = True):
    calls: dict[str, list[Path]] = {"quit": [], "open": []}
    monkeypatch.setattr(_app.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(installer, "desktop_to_update", lambda: (BUNDLE, release))
    monkeypatch.setattr(
        _app,
        "read_bundle",
        lambda bundle: DesktopApp(path=str(bundle), variant=variant, bundle_id=None, version=release),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(installer, "is_open", lambda _bundle: open_now)
    monkeypatch.setattr(installer, "quit_app", lambda bundle: calls["quit"].append(bundle) or quits_ok)
    monkeypatch.setattr(_app, "_launch", calls["open"].append)
    monkeypatch.setattr(_app, "_located", lambda _error: BUNDLE)
    return calls


def test_switching_flavor_quits_the_open_app_replaces_it_and_opens_it_again(monkeypatch, tmp_path):
    calls = _installed(monkeypatch, variant="electron", release=__version__, open_now=True)
    group, _, _ = _cli(monkeypatch, tmp_path, lambda *_a, **_k: "updated")
    result = CliRunner().invoke(group, ["install", "--flavor", "native"])  # no --restart: the switch implies it
    assert result.exit_code == 0, result.output
    assert calls == {"quit": [BUNDLE], "open": [BUNDLE]}


def test_the_same_flavor_at_the_same_release_quits_nothing(monkeypatch, tmp_path):
    calls = _installed(monkeypatch, variant="native", release=__version__, open_now=True)
    group, _, _ = _cli(monkeypatch, tmp_path, lambda *_a, **_k: "current")
    result = CliRunner().invoke(group, ["install", "--flavor", "native", "--restart"])
    assert result.exit_code == 0, result.output
    assert calls == {"quit": [], "open": []}


def test_a_switch_whose_install_fails_reopens_the_app_it_quit(monkeypatch, tmp_path):
    calls = _installed(monkeypatch, variant="electron", release="0.0.1", open_now=True)

    def install(*_args, **_kwargs):
        msg = "offline"
        raise installer.DesktopInstallationError(msg, installer.say.busy())

    group, _, _ = _cli(monkeypatch, tmp_path, install)
    result = CliRunner().invoke(group, ["install", "--flavor", "native"])
    assert result.exit_code == 1
    assert calls == {"quit": [BUNDLE], "open": [BUNDLE]}


def test_an_app_that_will_not_quit_for_a_switch_is_left_open_with_the_deferred_code(monkeypatch, tmp_path):
    calls = _installed(monkeypatch, variant="electron", release=__version__, open_now=True, quits_ok=False)
    group, _, _ = _cli(monkeypatch, tmp_path, lambda *_a, **_k: pytest.fail("never replaced while open"))
    result = CliRunner().invoke(group, ["install", "--flavor", "native"])
    assert result.exit_code == _app.RESTART_DEFERRED
    assert calls == {"quit": [BUNDLE], "open": []}  # asked once, never forced


def test_switching_is_known_only_on_a_mac_with_the_other_flavor_installed(monkeypatch):
    _installed(monkeypatch, variant="electron", release=__version__, open_now=False)
    assert _app._switching(_app.DesktopFlavor.NATIVE) is True
    assert _app._switching(_app.DesktopFlavor.ELECTRON) is False
    assert _app._switching(None) is False
    monkeypatch.setattr(_app.platform, "system", lambda: "Linux")
    assert _app._switching(_app.DesktopFlavor.NATIVE) is False


# --- update on quit keeps the promise the update made -------------------------------------------------------------


def _kernel(tmp_path, *, auto_update: bool):
    settings = Settings(auto_update=auto_update, update_check=True)
    config = SimpleNamespace(reread=lambda: SimpleNamespace(settings=lambda *_args: settings))
    installation = SimpleNamespace(
        core_pin=lambda: CorePin("index", "heinzel"), tool_entrypoints=lambda: (tmp_path / "hz",)
    )
    return SimpleNamespace(config=config, resolve=lambda _service: installation)


@pytest.mark.parametrize("auto_update", [True, False])
def test_a_promised_update_installs_at_quit_even_with_automatic_updates_off(monkeypatch, tmp_path, auto_update):
    states = iter(["deferred", "deferred", "updated"])
    calls = []
    monkeypatch.setattr(_app.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(installer, "install_desktop", lambda *args, **kwargs: calls.append(kwargs) or next(states))
    desktop_app.promise_update_on_quit(tmp_path, __version__)
    assert desktop_app.update_on_quit_promised(tmp_path)

    _app._refresh_after_exit(_kernel(tmp_path, auto_update=auto_update), home=tmp_path)  # type: ignore[arg-type]

    assert len(calls) == 3  # waits while the quitting app still runs, then installs
    assert all(call == {"update_only": True} for call in calls)
    assert not desktop_app.update_on_quit_promised(tmp_path)  # kept, so forgotten


def test_without_a_promise_automatic_updates_off_still_leave_the_app_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(_app.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(installer, "install_desktop", lambda *_a, **_k: pytest.fail("no refresh without consent"))
    _app._refresh_after_exit(_kernel(tmp_path, auto_update=False), home=tmp_path)  # type: ignore[arg-type]


def test_a_promise_is_kept_until_the_install_happens(monkeypatch, tmp_path):
    clock = iter([0.0, 1.0, 1e9])
    monkeypatch.setattr(_app.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(_app.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(installer, "install_desktop", lambda *_a, **_k: "deferred")
    desktop_app.promise_update_on_quit(tmp_path, __version__)
    _app._refresh_after_exit(_kernel(tmp_path, auto_update=False), home=tmp_path)  # type: ignore[arg-type]
    assert desktop_app.update_on_quit_promised(tmp_path)  # still open at the deadline: the next quit tries again


def test_the_update_that_says_installs_when_you_quit_records_the_promise(tmp_path):
    from heinzel.modules.system._models import UpdateResult
    from heinzel.modules.system.updater._update_heads import App, UpdateHeads

    heads = UpdateHeads.__new__(UpdateHeads)
    heads._home = tmp_path  # type: ignore[attr-defined]
    result = UpdateResult.model_construct(after="9.9.9", updated=True)
    head = heads._app_outcome(result, App("1.0.0", running=True, variant="electron"), (True, True, True))
    assert head.detail == "installs when you quit the app."
    assert (tmp_path / desktop_app.UPDATE_ON_QUIT).read_text() == "9.9.9"


def test_the_exit_code_of_a_deferred_switch_is_the_one_the_update_reads():
    assert _app.RESTART_DEFERRED == 75
    with pytest.raises(typer.Exit):
        raise typer.Exit(_app.RESTART_DEFERRED)
