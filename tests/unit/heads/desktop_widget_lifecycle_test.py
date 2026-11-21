"""Isolated widget lifecycle policy; no native signal, app launch or live install."""

import os
import plistlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from heinzel.heads import _desktop_installation as desktop
from heinzel.heads import _desktop_widget as widget
from heinzel.heads._desktop_native import CodeIdentity, NativeUnavailableError, ProcessIdentity

pytestmark = pytest.mark.unit


@pytest.fixture
def model(tmp_path, monkeypatch):
    bundle = tmp_path / "Heinzelmaennchen.app"
    binary = bundle / widget.WIDGET_EXECUTABLE
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fictional executable, never launched")
    metadata = bundle / widget.WIDGET / "Contents/Info.plist"
    metadata.write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "team.heinzel.apple.widgets",
                "CFBundleExecutable": "HeinzelWidgets",
                "NSExtension": {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"},
            }
        )
    )
    code = CodeIdentity("team.heinzel.apple.widgets", "", adhoc=True, digest=b"old-code", requirement=b"requirement")
    process = ProcessIdentity(41, os.getuid(), os.getuid(), os.getuid(), 100, 7, (100, 2), b"audit generation")
    state = SimpleNamespace(
        bundle=bundle,
        code=code,
        process=process,
        rows=[widget.ExecutableIdentity(41, binary, 100, 7)],
        signaled=[],
        stopped=True,
        registered=True,
    )

    class Native:
        def static(self, path):
            return replace(state.code, identifier="team.heinzel.apple") if path == bundle else state.code

        def executable(self, pid):
            return next(row for row in state.rows if row.pid == pid)

        def process(self, pid):
            assert pid == 41
            return state.process

        def running(self, process):
            return state.code

        def request_exit(self, process):
            state.signaled.append(process)
            if state.stopped:
                state.rows = []
            return True

        def register(self, path):
            return state.registered

    state.native = Native()
    state.authoritative_inventory = widget.inventory
    monkeypatch.setattr(widget, "Native", lambda: state.native)
    monkeypatch.setattr(widget, "inventory", lambda: tuple(state.rows))
    return state


def test_exact_adhoc_widget_stops_before_replacement(model):
    assert widget.can_stage(model.bundle)
    assert widget.quiesce(model.bundle)
    assert model.signaled == [model.process]


@pytest.mark.parametrize("case", ["main", "helper", "foreign", "translocated", "saved-uid", "signature", "metadata"])
def test_forbidden_targets_never_receive_a_signal(model, monkeypatch, case):
    row = model.rows[0]
    if case in {"main", "helper"}:
        model.rows.append(replace(row, pid=42, path=model.bundle / "Contents/MacOS" / case))
    elif case == "foreign":
        model.process = replace(model.process, uid=os.getuid() + 1)
    elif case == "translocated":
        model.rows = [
            replace(row, path=Path("/private/AppTranslocation/x") / model.bundle.name / widget.WIDGET_EXECUTABLE)
        ]
    elif case == "saved-uid":
        model.process = replace(model.process, saved_uid=os.getuid() + 1)
    elif case == "signature":
        monkeypatch.setattr(model.native, "running", lambda _: replace(model.code, digest=b"foreign"))
    else:
        (model.bundle / widget.WIDGET / "Contents/Info.plist").write_bytes(plistlib.dumps({}))
    assert not widget.quiesce(model.bundle)
    assert not model.signaled


@pytest.mark.parametrize("case", ["pid-reuse", "timeout", "restart", "signal-refusal"])
def test_failed_exit_or_changed_generation_never_authorizes_swap(model, monkeypatch, case):
    model.stopped = False
    if case == "pid-reuse":
        calls = iter([model.process, replace(model.process, version=8)])
        monkeypatch.setattr(model.native, "process", lambda _: next(calls))
    elif case == "timeout":
        monkeypatch.setattr(widget, "_TIMEOUT", 0)
    elif case == "restart":

        def request(process):
            model.signaled.append(process)
            model.process = replace(process, version=8)
            return True

        monkeypatch.setattr(model.native, "request_exit", request)
    else:
        monkeypatch.setattr(model.native, "request_exit", lambda _: False)
    assert not widget.quiesce(model.bundle)
    assert len(model.signaled) == (1 if case in {"restart", "timeout"} else 0)


def test_unavailable_inventory_or_native_api_defers_without_signal(model, monkeypatch):
    def unavailable():
        raise NativeUnavailableError

    monkeypatch.setattr(widget, "inventory", unavailable)
    assert not widget.can_stage(model.bundle)
    assert not widget.quiesce(model.bundle)
    assert widget.retired_in_use(model.bundle, model.bundle.parent / "new.app")
    monkeypatch.setattr(widget, "Native", unavailable)
    assert not widget.register(model.bundle)
    assert not model.signaled


def test_retirement_checks_dynamic_old_image_even_when_ps_names_new_public_path(model, monkeypatch):
    retired = model.bundle.parent / "retired" / model.bundle.name
    assert widget.retired_in_use(retired, model.bundle)
    monkeypatch.setattr(model.native, "running", lambda _: replace(model.code, digest=b"new-code"))
    assert not widget.retired_in_use(retired, model.bundle)
    model.rows[0] = replace(model.rows[0], path=retired / widget.WIDGET_EXECUTABLE)
    assert widget.retired_in_use(retired, model.bundle)
    assert not model.signaled


def _fixture_cli(tmp_path):
    executable = tmp_path / "hz"
    executable.write_text("fictional executable")
    executable.chmod(0o700)
    return executable


def _transaction(tmp_path, monkeypatch):
    applications, config = tmp_path / "apps", tmp_path / "config"
    applications.mkdir()
    config.mkdir(mode=0o700)
    bundle = applications / desktop.BUNDLE_NAME
    bundle.mkdir()
    (bundle / "old-resource").write_text("keep while mapped")
    for name in (desktop._BINDING, desktop._RECEIPT):
        (config / name).write_text("old")
    monkeypatch.setattr(desktop, "_owned_installation", lambda *_: "owned")

    def rename(source, target, *, swap=False):
        if swap:
            third = source.parent / "exchange"
            target.rename(third)
            source.rename(target)
            third.rename(source)
        else:
            source.rename(target)

    monkeypatch.setattr(desktop, "_rename", rename)
    # journal path validation normally recognizes SYSTEM_APPLICATIONS or ~/Applications.
    monkeypatch.setattr(desktop, "SYSTEM_APPLICATIONS", applications)
    return bundle, config


@pytest.mark.parametrize("pending", ["late-process", "registration"])
def test_committed_install_retains_journal_and_resources_then_same_version_retries(tmp_path, monkeypatch, pending):
    bundle, config = _transaction(tmp_path, monkeypatch)
    calls = []
    blocked = {"value": True}
    monkeypatch.setattr(
        widget,
        "register",
        lambda path: calls.append(path) is None and (pending != "registration" or not blocked["value"]),
    )
    monkeypatch.setattr(widget, "retired_in_use", lambda *_: pending == "late-process" and blocked["value"])
    with desktop._staging(bundle.parent, config) as (staging, files):
        (staging / desktop.BUNDLE_NAME).mkdir()
        (staging / desktop.BUNDLE_NAME / "new-resource").write_text("new")
        for name in (desktop._BINDING, desktop._RECEIPT):
            (files / name).write_text("new")
        desktop._commit(bundle, config, staging, files, "install", native_lifecycle=True)
    assert (bundle / "new-resource").exists()
    assert (staging / desktop.BUNDLE_NAME / "old-resource").read_text() == "keep while mapped"
    assert (config / desktop._JOURNAL).exists()
    desktop._recover_transaction(bundle, config)
    assert staging.exists()
    blocked["value"] = False
    desktop._recover_transaction(bundle, config)
    assert not staging.exists()
    assert not files.exists()
    assert not (config / desktop._JOURNAL).exists()
    assert (bundle / "new-resource").exists()
    assert len(calls) == (3 if pending == "registration" else 1)


def test_retired_inode_substitution_is_never_cleaned(tmp_path, monkeypatch):
    bundle, config = _transaction(tmp_path, monkeypatch)
    monkeypatch.setattr(widget, "register", lambda _: True)
    monkeypatch.setattr(widget, "retired_in_use", lambda *_: True)
    with desktop._staging(bundle.parent, config) as (staging, files):
        (staging / desktop.BUNDLE_NAME).mkdir()
        for name in (desktop._BINDING, desktop._RECEIPT):
            (files / name).write_text("new")
        desktop._commit(bundle, config, staging, files, "install", native_lifecycle=True)
    retired = staging / desktop.BUNDLE_NAME
    retired.rename(staging / "original")
    retired.mkdir()
    with pytest.raises(desktop.DesktopInstallationError):
        desktop._recover_transaction(bundle, config)
    assert retired.exists()
    assert (staging / "original/old-resource").exists()


def test_late_widget_after_final_inventory_keeps_committed_resources(tmp_path, monkeypatch):
    bundle, config = _transaction(tmp_path, monkeypatch)
    receipt = desktop._Receipt(
        release="0.9.49", flavor="swift", device=1, inode=2, birth_time=None, executable="/fixture/hz"
    )
    monkeypatch.setattr(desktop, "_owned_installation", lambda *_: receipt)
    late = {"running": False}
    original_rename = desktop._rename

    def rename(source, target, *, swap=False):
        original_rename(source, target, swap=swap)
        if target == config / desktop._RECEIPT:
            late["running"] = True  # old image started after pre-swap inventory

    monkeypatch.setattr(desktop, "_rename", rename)
    monkeypatch.setattr(desktop, "_in_use", lambda _: False)
    monkeypatch.setattr(widget, "in_use", lambda _: False)
    monkeypatch.setattr(widget, "register", lambda _: True)
    monkeypatch.setattr(widget, "retired_in_use", lambda *_: late["running"])
    with desktop._staging(bundle.parent, config) as (staging, files):
        (staging / desktop.BUNDLE_NAME).mkdir()
        for name in (desktop._BINDING, desktop._RECEIPT):
            (files / name).write_text("new")
        state = desktop._install_candidate(
            bundle, config, staging, files, receipt, desktop.SILENT, native_lifecycle=True
        )
    assert state == "installed-pending"
    assert (staging / desktop.BUNDLE_NAME / "old-resource").exists()
    assert not desktop._recover_before_use(tmp_path, config)
    assert staging.exists()


@pytest.mark.parametrize("fail_move", [1, 2, 3])
def test_native_transaction_rolls_back_each_failed_move_without_deleting_active_candidate(
    tmp_path, monkeypatch, fail_move
):
    bundle, config = _transaction(tmp_path, monkeypatch)
    original_rename = desktop._rename
    moves = []

    def rename(source, target, *, swap=False):
        moves.append(target)
        if len(moves) == fail_move:
            raise PermissionError
        original_rename(source, target, swap=swap)

    monkeypatch.setattr(desktop, "_rename", rename)
    monkeypatch.setattr(widget, "register", lambda _: pytest.fail("must not register rollback"))
    monkeypatch.setattr(widget, "retired_in_use", lambda *_: True)
    with desktop._staging(bundle.parent, config) as (staging, files):
        (staging / desktop.BUNDLE_NAME).mkdir()
        (staging / desktop.BUNDLE_NAME / "candidate-resource").write_text("retain")
        for name in (desktop._BINDING, desktop._RECEIPT):
            (files / name).write_text("new")
        with pytest.raises(PermissionError):
            desktop._commit(bundle, config, staging, files, "install", native_lifecycle=True)
    assert (bundle / "old-resource").exists()
    assert (staging / desktop.BUNDLE_NAME / "candidate-resource").exists()
    assert (config / desktop._JOURNAL).exists()
    monkeypatch.setattr(widget, "retired_in_use", lambda *_: False)
    desktop._recover_transaction(bundle, config)
    assert not staging.exists()
    assert (bundle / "old-resource").exists()


@pytest.mark.parametrize("output", [b"", b"41 501", b"unknown", b"41\n41"])
def test_incomplete_process_inventory_never_becomes_idle(monkeypatch, output):
    import subprocess

    monkeypatch.setattr(widget.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, output))
    with pytest.raises((NativeUnavailableError, ValueError)):
        widget.inventory()


def test_complete_inventory_uses_kernel_path_and_skips_kernel_pid(monkeypatch):
    import subprocess

    row = widget.ExecutableIdentity(41, Path("/Applications/An App.app/Contents/MacOS/An App"), 100, 7)
    monkeypatch.setattr(
        widget.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, b"0\n41\n")
    )
    native = SimpleNamespace(executable=lambda pid: row if pid == 41 else pytest.fail("kernel PID excluded"))
    monkeypatch.setattr(widget, "Native", lambda: native)
    assert widget.inventory() == (row,)


def test_same_version_install_retries_registration_before_current_fast_path(tmp_path, monkeypatch):
    from contextlib import contextmanager

    bundle, config = _transaction(tmp_path, monkeypatch)
    registered = {"ready": False, "attempts": 0}

    def register(_):
        registered["attempts"] += 1
        return registered["ready"]

    monkeypatch.setattr(widget, "register", register)
    monkeypatch.setattr(widget, "retired_in_use", lambda *_: False)
    with desktop._staging(bundle.parent, config) as (staging, files):
        (staging / desktop.BUNDLE_NAME).mkdir()
        for name in (desktop._BINDING, desktop._RECEIPT):
            (files / name).write_text("new")
        desktop._commit(bundle, config, staging, files, "install", native_lifecycle=True)

    @contextmanager
    def locked(*_):
        yield config

    monkeypatch.setattr(desktop, "_locked_config", locked)
    monkeypatch.setattr(desktop, "_check_platform", lambda: None)
    monkeypatch.setattr(desktop, "_any_in_use", lambda _: True)  # New app does not block registration retry.
    calls = []

    def steps(version, *_args, **_kwargs):
        assert not (config / desktop._JOURNAL).exists()
        calls.append(version)
        return "current"

    monkeypatch.setattr(desktop, "_install_steps", steps)
    assert desktop.install_desktop("0.9.51", tmp_path / "hz", home=tmp_path) == "installed-pending"
    assert not calls
    registered["ready"] = True
    assert desktop.install_desktop("0.9.51", tmp_path / "hz", home=tmp_path) == "current"
    assert calls == ["0.9.51"]
    assert registered["attempts"] == 3


def test_failed_download_never_signals_existing_widget(tmp_path, monkeypatch):
    bundle, config = _transaction(tmp_path, monkeypatch)
    executable = tmp_path / "hz"
    executable.write_text("fixture")
    executable.chmod(0o700)
    existing = desktop._Receipt(
        release="0.9.49", flavor="swift", device=1, inode=2, birth_time=None, executable=str(executable)
    )
    monkeypatch.setattr(desktop, "_in_use", lambda _: True)
    monkeypatch.setattr(widget, "in_use", lambda _: True)
    monkeypatch.setattr(widget, "can_stage", lambda _: True)
    monkeypatch.setattr(widget, "quiesce", lambda _: pytest.fail("candidate is not staged"))

    def fetch(*_args, **_kwargs):
        raise ConnectionError

    monkeypatch.setattr(desktop, "fetch_archive", fetch)
    with pytest.raises(ConnectionError):
        desktop._install_release(
            "0.9.51", executable, bundle, config, existing, keep_quarantine=False, flavor=desktop.DesktopFlavor.NATIVE
        )
    assert (bundle / "old-resource").exists()
    assert not (config / desktop._JOURNAL).exists()


def test_cleanup_crash_after_retired_directory_removal_is_resumable(tmp_path, monkeypatch):
    bundle, config = _transaction(tmp_path, monkeypatch)
    monkeypatch.setattr(widget, "register", lambda _: True)
    monkeypatch.setattr(widget, "retired_in_use", lambda *_: False)
    original = desktop.shutil.rmtree
    calls = []

    def remove(path):
        original(path)
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError

    monkeypatch.setattr(desktop.shutil, "rmtree", remove)
    with desktop._staging(bundle.parent, config) as (staging, files):
        (staging / desktop.BUNDLE_NAME).mkdir()
        for name in (desktop._BINDING, desktop._RECEIPT):
            (files / name).write_text("new")
        with pytest.raises(PermissionError):
            desktop._commit(bundle, config, staging, files, "install", native_lifecycle=True)
    assert not staging.exists()
    assert files.exists()
    desktop._recover_transaction(bundle, config)
    assert not files.exists()
    assert not (config / desktop._JOURNAL).exists()


@pytest.mark.parametrize("case", ["certificate", "different-team", "adhoc-with-team", "certificate-without-team"])
def test_signing_policy_requires_coherent_outer_widget_and_dynamic_identity(model, monkeypatch, case):
    if case == "certificate":
        model.code = replace(model.code, team="EXAMPLETEAM", adhoc=False)
        assert widget.quiesce(model.bundle)
        assert model.signaled == [model.process]
        return
    if case == "different-team":

        def static(path):
            return (
                replace(model.code, identifier="team.heinzel.apple", team="FOREIGN")
                if path == model.bundle
                else model.code
            )

        monkeypatch.setattr(model.native, "static", static)
    elif case == "adhoc-with-team":
        model.code = replace(model.code, team="EXAMPLETEAM")
    else:
        model.code = replace(model.code, adhoc=False)
    assert not widget.quiesce(model.bundle)
    assert not model.signaled


def test_same_code_copy_with_spoofed_argv0_is_not_a_signal_target(model, monkeypatch):
    # A copy can advertise the installed path as argv0; the kernel vnode path still names the copy.
    model.rows[0] = replace(model.rows[0], path=Path("/fictional-copy/HeinzelWidgets"))
    monkeypatch.setattr(widget, "inventory", model.authoritative_inventory)
    monkeypatch.setattr(widget, "_pids", lambda: tuple(row.pid for row in model.rows))
    assert widget.quiesce(model.bundle)
    assert not model.signaled
    assert not widget.in_use(model.bundle)


def test_hidden_old_image_is_found_despite_unrelated_or_fallback_argv0(model, monkeypatch):
    monkeypatch.setattr(widget, "inventory", model.authoritative_inventory)
    monkeypatch.setattr(widget, "_pids", lambda: tuple(row.pid for row in model.rows))
    assert widget.in_use(model.bundle)
    assert widget.retired_in_use(model.bundle.parent / "retired" / model.bundle.name, model.bundle)
    assert widget.quiesce(model.bundle)
    assert model.signaled == [model.process]


@pytest.mark.parametrize("case", ["live-unknown", "gone", "zombie", "foreign-system"])
def test_unknown_inventory_is_never_idle_but_verified_gone_or_unrelated_images_are_safe(monkeypatch, case):
    calls = iter([(41,), () if case == "gone" else (41,)])
    monkeypatch.setattr(widget, "_pids", lambda: next(calls))

    def executable(pid):
        if case == "foreign-system":
            return widget.ExecutableIdentity(pid, Path("/System/Library/verified-system-executable"), 123, 7)
        raise NativeUnavailableError

    native = SimpleNamespace(
        executable=executable,
        zombie=lambda _: case == "zombie",
        process=lambda _: pytest.fail("never use same-UID flavor18 on unrelated processes"),
    )
    monkeypatch.setattr(widget, "Native", lambda: native)
    if case == "live-unknown":
        with pytest.raises(NativeUnavailableError):
            widget.inventory()
    else:
        rows = widget.inventory()
        assert len(rows) == (1 if case == "foreign-system" else 0)


@pytest.mark.parametrize("phase", ["before-staging", "before-commit"])
@pytest.mark.parametrize("target", [desktop.DesktopFlavor.NATIVE, desktop.DesktopFlavor.ELECTRON])
def test_native_install_never_uses_false_legacy_comm_guard_to_admit_hidden_main(
    model, monkeypatch, tmp_path, phase, target
):
    model.rows[0] = replace(model.rows[0], path=model.bundle / "Contents/MacOS/Heinzel")
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    executable = tmp_path / "hz"
    executable.write_text("fixture")
    executable.chmod(0o700)
    receipt = desktop._Receipt(
        release="0.9.49", flavor="swift", device=1, inode=2, birth_time=None, executable=str(executable)
    )
    monkeypatch.setattr(desktop, "_in_use", lambda _: False)
    monkeypatch.setattr(desktop, "_owned_installation", lambda *_: receipt)
    monkeypatch.setattr(desktop, "_commit", lambda *_args, **_kwargs: pytest.fail("main must block commit"))
    monkeypatch.setattr(desktop, "fetch_archive", lambda *_args, **_kwargs: pytest.fail("main must block staging"))
    if phase == "before-staging":
        state = desktop._install_release(
            "0.9.51",
            executable,
            model.bundle,
            config,
            receipt,
            keep_quarantine=False,
            flavor=target,
        )
    else:
        state = desktop._install_candidate(
            model.bundle, config, tmp_path / "stage", tmp_path / "files", receipt, desktop.SILENT, native_lifecycle=True
        )
    assert state == "deferred"
    assert not model.signaled


def test_native_migration_cannot_move_a_hidden_running_main(model, monkeypatch, tmp_path):
    model.rows[0] = replace(model.rows[0], path=model.bundle / "Contents/MacOS/Heinzel")
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    record = desktop._Receipt(
        release="0.9.49", flavor="swift", device=1, inode=2, birth_time=None, executable="/fictional/hz"
    )
    monkeypatch.setattr(desktop, "SYSTEM_APPLICATIONS", tmp_path / "system-applications")
    monkeypatch.setattr(desktop, "system_applications_usable", lambda: True)
    monkeypatch.setattr(desktop, "_owned_installation", lambda *_: record)
    monkeypatch.setattr(desktop, "_in_use", lambda _: False)
    monkeypatch.setattr(desktop, "_rename", lambda *_args, **_kwargs: pytest.fail("main must block migration"))
    assert desktop._migrate(model.bundle, config) == model.bundle
    assert not model.signaled


@pytest.mark.parametrize("late_widget", [False, True])
def test_swift_to_electron_stages_before_stopping_old_widget_and_retains_late_image(tmp_path, monkeypatch, late_widget):
    bundle, config = _transaction(tmp_path, monkeypatch)
    executable = _fixture_cli(tmp_path)
    existing = desktop._Receipt(
        release="0.9.51", flavor="swift", device=1, inode=2, birth_time=None, executable=str(executable)
    )
    monkeypatch.setattr(desktop, "_owned_installation", lambda *_: existing)
    events = []
    running = {"widget": True, "late": False}
    monkeypatch.setattr(desktop, "_in_use", lambda _: False)  # legacy comm could conceal the old widget
    monkeypatch.setattr(widget, "in_use", lambda _: running["widget"])
    monkeypatch.setattr(widget, "can_stage", lambda _: True)
    monkeypatch.setattr(desktop, "fetch_archive", lambda _version, staging, _reporter: staging / "fixture.zip")

    def extract(_archive, expanded):
        candidate = expanded / desktop.BUNDLE_NAME
        candidate.mkdir()
        (candidate / "new-electron-resource").write_text("new")
        return candidate

    monkeypatch.setattr(desktop, "extract_bundle", extract)

    def verify(_candidate, version, flavor):
        assert version == "0.9.51"
        assert flavor == desktop.DesktopFlavor.ELECTRON
        events.append("verify-electron")

    monkeypatch.setattr(desktop, "_verify_bundle", verify)
    monkeypatch.setattr(desktop, "_prepare_for_gatekeeper", lambda *_args, **_kwargs: events.append("seal"))

    def quiesce(old_bundle):
        assert old_bundle == bundle
        assert (old_bundle / "old-resource").exists()
        assert events == ["verify-electron", "seal"]
        events.append("stop-old-swift-widget")
        running["widget"] = False
        return True

    monkeypatch.setattr(widget, "quiesce", quiesce)
    original_rename = desktop._rename

    def rename(source, target, *, swap=False):
        original_rename(source, target, swap=swap)
        if target == config / desktop._RECEIPT:
            running["late"] = late_widget

    monkeypatch.setattr(desktop, "_rename", rename)
    monkeypatch.setattr(widget, "register", lambda _: (bundle / "new-electron-resource").exists())
    monkeypatch.setattr(widget, "retired_in_use", lambda *_: running["late"])
    result = desktop._install_release(
        "0.9.51", executable, bundle, config, existing, keep_quarantine=False, flavor=desktop.DesktopFlavor.ELECTRON
    )
    assert events[-1] == "stop-old-swift-widget"
    assert (bundle / "new-electron-resource").exists()
    assert result == ("installed-pending" if late_widget else "updated")
    journal_bytes = desktop._read_metadata(config / desktop._JOURNAL)
    if late_widget:
        journal = desktop._Journal.model_validate_json(journal_bytes)
        assert journal.native_lifecycle
        assert journal.state == "committed"
        assert (bundle.parent / journal.staging / desktop.BUNDLE_NAME / "old-resource").exists()
    else:
        assert journal_bytes is None
    new_receipt = desktop._Receipt.model_validate_json(desktop._read_metadata(config / desktop._RECEIPT))
    assert new_receipt.flavor == "electron"
