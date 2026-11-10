"""Fail-closed installation recovery against plain private fixture files; never launch an app."""

import os
import subprocess
from contextlib import contextmanager

import pytest

from heinzel.heads import _desktop_installation as desktop

pytestmark = pytest.mark.unit


def test_move_status_rejects_unexpected_file_identity_and_reverses(tmp_path, monkeypatch):
    public, private = tmp_path / "public", tmp_path / "private"
    private.write_text("owned")
    expected = desktop._MoveIdentity(public=None, private=desktop._identity(private))
    move = desktop._Move(public, private, expected)
    assert move.status() is False
    monkeypatch.setattr(desktop, "_rename", lambda source, target, *, swap: source.rename(target))
    move.apply()
    assert move.status() is True
    move.apply(reverse=True)
    assert move.status() is False
    private.rename(tmp_path / "retained-original")
    private.write_text("replaced")
    with pytest.raises(desktop.DesktopInstallationError):
        move.status()


def test_pending_recovery_refuses_nonprefix_and_rolls_back_in_reverse(monkeypatch, tmp_path):
    class Move:
        def __init__(self, name, *, applied):
            self.name, self.applied = name, applied

        def status(self):
            return self.applied

        def apply(self, *, reverse):
            assert reverse
            calls.append(self.name)

    journal = type("Journal", (), {"state": "pending"})()
    calls = []
    monkeypatch.setattr(desktop, "_write_journal", lambda _config, entry: calls.append(entry.state))
    with pytest.raises(desktop.DesktopInstallationError):
        desktop._rollback_pending(tmp_path, journal, (Move("a", applied=False), Move("b", applied=True)))
    assert calls == []
    desktop._rollback_pending(
        tmp_path, journal, (Move("a", applied=True), Move("b", applied=True), Move("c", applied=False))
    )
    assert calls == ["b", "a", "rolled_back"]


def test_pending_journal_requires_valid_metadata_and_is_retained(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    journal = config / desktop._JOURNAL
    journal.write_text("{invalid")
    journal.chmod(0o600)
    with pytest.raises(desktop.DesktopInstallationError):
        desktop._recover_transaction(tmp_path / "bundle", config)
    assert journal.read_text() == "{invalid"


@pytest.mark.parametrize("mode", ["world-readable", "too-large"])
def test_metadata_rejects_nonprivate_or_oversized_record(tmp_path, mode):
    path = tmp_path / "receipt"
    path.write_bytes(b"x" * (desktop._MAX_METADATA_BYTES + 1 if mode == "too-large" else 1))
    path.chmod(0o644 if mode == "world-readable" else 0o600)
    with pytest.raises(desktop.DesktopInstallationError):
        desktop._read_metadata(path)
    assert path.exists()


def test_unsafe_directory_and_lock_are_rejected(tmp_path):
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    with pytest.raises(desktop.DesktopInstallationError):
        desktop._owned_directory(unsafe)
    lock = tmp_path / "lock"
    lock.write_text("")
    lock.chmod(0o644)
    with pytest.raises(desktop.DesktopInstallationError), desktop._open_lock(lock):
        pytest.fail("must reject before yielding lock")


def test_staging_cleanup_preserves_files_if_recovery_journal_exists(tmp_path):
    applications, config = tmp_path / "applications", tmp_path / "config"
    applications.mkdir()
    config.mkdir()
    with desktop._staging(applications, config) as (stage, metadata):
        assert stage.is_dir()
        assert metadata.is_dir()
    assert not stage.exists()
    assert not metadata.exists()
    with desktop._staging(applications, config) as (stage, metadata):
        (config / desktop._JOURNAL).write_text("pending")
    assert stage.is_dir()
    assert metadata.is_dir()


@pytest.mark.parametrize("state", ["lock-unavailable", "running", "corrupt", "recovered"])
def test_recovery_defers_or_preserves_failed_journal_before_next_operation(monkeypatch, tmp_path, state):
    config = tmp_path / "config"
    config.mkdir()
    journal = config / desktop._JOURNAL
    journal.write_text("pending")
    calls = []

    @contextmanager
    def use(_config):
        yield state != "lock-unavailable"

    monkeypatch.setattr(desktop, "_desktop_use", use)
    monkeypatch.setattr(desktop, "_any_in_use", lambda _home: state == "running")

    def recover(*_args):
        calls.append("recover")
        if state == "corrupt":
            msg = "fixture conflict"
            raise desktop.DesktopInstallationError(msg)

    monkeypatch.setattr(desktop, "_recover_transaction", recover)
    if state == "corrupt":
        with pytest.raises(desktop.DesktopInstallationError, match="fixture conflict"):
            desktop._recover_before_use(tmp_path, config)
    else:
        assert desktop._recover_before_use(tmp_path, config) is (state == "recovered")
    assert calls == (["recover"] if state in {"corrupt", "recovered"} else [])
    assert journal.read_text() == "pending"


def test_in_use_recognizes_owned_and_translocated_paths(monkeypatch, tmp_path):
    bundle = tmp_path / "Heinzelmaennchen.app"
    owned = os.fsencode(bundle / "Contents/MacOS/Heinzelmaennchen")
    translocated = b"/private/AppTranslocation/random/Heinzelmaennchen.app/Contents/MacOS/Heinzelmaennchen"
    unrelated = b"/different/app"
    for output, expected in [(owned, True), (translocated, True), (unrelated, False)]:
        monkeypatch.setattr(
            desktop.subprocess,
            "run",
            lambda *_args, output=output, **_kwargs: subprocess.CompletedProcess([], 0, output + b"\n"),
        )
        assert desktop._in_use(bundle) is expected
