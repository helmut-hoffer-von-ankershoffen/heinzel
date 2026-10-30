"""Continuity checks use only temporary files and never start the installed interpreter."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, _installation, _installation_continuity

pytestmark = pytest.mark.unit


@pytest.fixture
def tool(tmp_path):
    prefix = tmp_path / "tools" / "heinzel"
    scripts = prefix / "bin"
    scripts.mkdir(parents=True)
    (scripts / "hz").write_text("entrypoint")
    bin_dir = tmp_path / "installed-bin"
    bin_dir.mkdir()
    (bin_dir / "hz").symlink_to(scripts / "hz")
    (prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "heinzel" }]\nentrypoints = [{ name = "hz", install-path = '
        + repr(str(bin_dir / "hz"))
        + " }]\n"
    )
    package = prefix / "lib" / "site-packages" / "example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("initial package")
    return Installation(Progress(), prefix=prefix, uv="unused")


def _record(tool):
    tool.record_startup_state(expected=tool.startup_fingerprint())


def test_record_requires_an_exact_verified_generation(tool):
    _record(tool)
    expected = tool.startup_fingerprint()
    prior = (tool.prefix / ".heinzel-startup-state").read_bytes()
    (tool.prefix / "bin/hz").write_text("changed after caller checks")
    with pytest.raises(HeinzelError, match="verified generation"):
        tool.record_startup_state(expected=expected)
    assert (tool.prefix / ".heinzel-startup-state").read_bytes() == prior
    assert not tool.startup_state_matches()
    with pytest.raises(TypeError):
        tool.record_startup_state()
    for invalid in ("", "a" * 63, "A" * 64, "g" * 64):
        with pytest.raises(HeinzelError, match="fingerprint is invalid"):
            tool.record_startup_state(expected=invalid)


def test_generation_change_after_pending_marker_refuses_commit(tool, monkeypatch):
    expected = tool.startup_fingerprint()
    original = _installation_continuity.fingerprint
    calls = []

    def changed_after_first(prefix, authority):
        result = original(prefix, authority)
        calls.append(1)
        if len(calls) == 1:
            (tool.prefix / "bin/hz").write_text("changed after first capture")
        return result

    monkeypatch.setattr(_installation_continuity, "fingerprint", changed_after_first)
    with pytest.raises(HeinzelError, match="changed before recording"):
        tool.record_startup_state(expected=expected)
    assert (tool.prefix / ".heinzel-startup-pending").exists()
    assert not tool.startup_state_matches()


def test_missing_evidence_never_seeds_and_verified_change_can_be_recorded(tool, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("inspection executed code"))
    state = tool.prefix / ".heinzel-startup-state"
    assert not tool.startup_state_matches()
    assert not state.exists()
    first = tool.startup_fingerprint()
    assert first == tool.startup_fingerprint()
    _record(tool)
    assert state.stat().st_mode & 0o777 == 0o600
    assert tool.startup_state_matches()
    package = tool.prefix / "lib/site-packages/example/__init__.py"
    package.write_text("changed package")
    assert not tool.startup_state_matches()
    assert tool.startup_fingerprint() != first
    _record(tool)  # A caller must first verify this mutation independently.
    assert tool.startup_state_matches()
    package.unlink()
    assert not tool.startup_state_matches()


def test_destination_verification_accepts_canonical_paths_and_aliases(tool, tmp_path):
    store = tool.prefix.parent
    binary = tmp_path / "installed-bin"
    tool.verify_tool_destination(store, binary)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    tool.verify_tool_destination(alias / "tools", alias / "installed-bin")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(HeinzelError, match="does not match"):
        tool.verify_tool_destination(other, binary)
    with pytest.raises(HeinzelError, match="does not match"):
        tool.verify_tool_destination(store, other)
    with pytest.raises(HeinzelError, match="does not match"):
        tool.verify_tool_destination(store, tmp_path / "missing")


def test_receipt_queries_accept_alias_but_continuity_requires_physical_prefix(tool, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    aliased = Installation(Progress(), prefix=alias / "tools/heinzel", uv="unused")
    assert aliased.requirements() == tool.requirements()
    assert aliased.core_pin() == tool.core_pin()
    assert aliased.plugin_pins() == tool.plugin_pins()
    with pytest.raises(HeinzelError, match="physical path"):
        aliased.startup_fingerprint()


def test_bytecode_is_excluded_but_startup_and_receipt_changes_are_not(tool):
    _record(tool)
    (tool.prefix / "lib/site-packages/example/cache.pyc").write_bytes(b"generated")
    cache = tool.prefix / "lib/site-packages/example/__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"generated")
    assert tool.startup_state_matches()
    (cache / "unexpected.py").write_text("not bytecode")
    assert not tool.startup_state_matches()
    (cache / "unexpected.py").unlink()
    (cache / "module.pyc").unlink()
    cache.rmdir()
    assert tool.startup_state_matches()
    (tool.prefix / "lib/site-packages/startup.pth").write_text("startup")
    assert not tool.startup_state_matches()
    (tool.prefix / "lib/site-packages/startup.pth").unlink()
    assert tool.startup_state_matches()
    with (tool.prefix / "uv-receipt.toml").open("a") as receipt:
        receipt.write("\n# changed receipt\n")
    assert not tool.startup_state_matches()


@pytest.mark.parametrize("unsafe", ["symlink", "mode", "hardlink", "malformed"])
def test_unsafe_record_is_not_evidence(tool, tmp_path, unsafe):
    _record(tool)
    record = tool.prefix / ".heinzel-startup-state"
    if unsafe == "symlink":
        record.unlink()
        record.symlink_to(tmp_path / "external")
    elif unsafe == "mode":
        record.chmod(0o644)
    elif unsafe == "hardlink":
        os.link(record, tmp_path / "copy")
    else:
        record.write_text("bad")
    assert not tool.startup_state_matches()


def test_unowned_shim_and_unsafe_links_fail_closed(tool, tmp_path):
    _record(tool)
    shim = tmp_path / "installed-bin/hz"
    shim.unlink()
    assert not tool.startup_state_matches()
    with pytest.raises(HeinzelError):
        tool.startup_fingerprint()
    shim.symlink_to(tool.prefix / "bin/hz")
    (tool.prefix / "lib/site-packages/external").symlink_to(tmp_path)
    assert not tool.startup_state_matches()
    with pytest.raises(HeinzelError, match="external link"):
        tool.startup_fingerprint()


@pytest.mark.parametrize(
    "target_name", ["module.pyc", "module.pyo", ".heinzel-startup-state", ".heinzel-startup-pending"]
)
def test_links_cannot_hide_excluded_content(tool, target_name):
    target = tool.prefix / target_name
    target.write_bytes(b"inert")
    (tool.prefix / "lib/site-packages/example/startup.pth").symlink_to(target)
    with pytest.raises(HeinzelError, match="excluded continuity or bytecode"):
        tool.startup_fingerprint()


def test_interpreter_external_link_is_content_bound_without_execution(tool, tmp_path):
    interpreter = tmp_path / "python"
    assert not interpreter.exists()
    assert not interpreter.is_symlink()
    interpreter.write_bytes(b"ordinary fixture file")
    (tool.prefix / "bin/python").symlink_to(interpreter)
    _record(tool)
    assert tool.startup_state_matches()
    assert interpreter.resolve().is_relative_to(tmp_path.resolve())
    assert not interpreter.samefile(sys.executable)
    assert not interpreter.samefile(sys._base_executable)
    interpreter.write_bytes(b"different fixture")
    assert not tool.startup_state_matches()
    assert interpreter.resolve().is_relative_to(tmp_path.resolve())
    assert not interpreter.samefile(sys.executable)
    assert not interpreter.samefile(sys._base_executable)
    interpreter.unlink()
    assert not tool.startup_state_matches()


def test_oversized_observation_is_refused_without_writing_evidence(tool, monkeypatch):
    expected = tool.startup_fingerprint()
    monkeypatch.setattr(_installation_continuity, "_MAX_BYTES", 1)
    with pytest.raises(HeinzelError, match="oversized"):
        tool.record_startup_state(expected=expected)
    assert not (tool.prefix / ".heinzel-startup-state").exists()


@pytest.mark.parametrize("unsafe", ["large", "writable", "symlink"])
def test_receipt_reads_refuse_oversize_or_unsafe_files(tool, tmp_path, monkeypatch, unsafe):
    receipt = tool.prefix / "uv-receipt.toml"
    target = tool._tool_target()
    if unsafe == "large":
        monkeypatch.setattr(_installation, "_MAX_RECEIPT_BYTES", 16)
    elif unsafe == "writable":
        receipt.chmod(0o666)
    else:
        outside = tmp_path / "receipt"
        receipt.rename(outside)
        receipt.symlink_to(outside)
    with pytest.raises((OSError, ValueError)):
        tool.requirements()
    with pytest.raises(HeinzelError):
        tool.startup_fingerprint()
    with pytest.raises(HeinzelError):
        tool._owned_tool_links(target)
    assert not tool.startup_state_matches()


def test_receipt_growth_during_read_is_detected(tool, monkeypatch):
    receipt = tool.prefix / "uv-receipt.toml"
    read = os.read
    called = False

    def changing(fd, size):
        nonlocal called
        chunk = read(fd, size)
        if not called:
            called = True
            with receipt.open("a") as output:
                output.write("# changed\n")
        return chunk

    monkeypatch.setattr(os, "read", changing)
    with pytest.raises(ValueError, match="changed during inspection"):
        tool.requirements()


@pytest.mark.parametrize("relative", ["lib", "lib/site-packages/example/__init__.py", "bin/hz"])
@pytest.mark.parametrize("mode", [0o020, 0o002])
def test_writable_nested_content_is_refused(tool, relative, mode):
    path = tool.prefix / relative
    path.chmod(path.stat().st_mode | mode)
    assert not tool.startup_state_matches()
    with pytest.raises(HeinzelError, match="unsafe ownership or permissions"):
        tool.startup_fingerprint()


def test_foreign_owner_and_writable_external_python_are_refused(tool, tmp_path, monkeypatch):
    sample = (tool.prefix / "bin/hz").stat()
    monkeypatch.setattr(os, "getuid", lambda: sample.st_uid + 10000)
    with pytest.raises(HeinzelError, match="unsafe ownership"):
        _installation_continuity._owned(sample)
    monkeypatch.undo()
    python = tmp_path / "python"
    assert not python.exists()
    assert not python.is_symlink()
    python.write_bytes(b"fixture")
    assert python.resolve().is_relative_to(tmp_path.resolve())
    assert not python.samefile(sys.executable)
    assert not python.samefile(sys._base_executable)
    python.chmod(0o666)
    (tool.prefix / "bin/python").symlink_to(python)
    with pytest.raises(HeinzelError, match="unsafe ownership or permissions"):
        tool.startup_fingerprint()
    assert python.resolve().is_relative_to(tmp_path.resolve())
    assert not python.samefile(sys.executable)
    assert not python.samefile(sys._base_executable)
    python.chmod(0o644)
    tmp_path.chmod(0o777)
    with pytest.raises(HeinzelError, match="interpreter directory"):
        tool.startup_fingerprint()


def test_prefix_replacement_after_atomic_write_is_reported_uncertain(tool, monkeypatch):
    original_replace = os.replace

    def replaced(*args, **kwargs):
        original_replace(*args, **kwargs)
        tool.prefix.rename(tool.prefix.with_name("orphan"))
        tool.prefix.mkdir()

    monkeypatch.setattr(os, "replace", replaced)
    with pytest.raises(HeinzelError, match="could not be confirmed"):
        _record(tool)
    assert not tool.startup_state_matches()


@pytest.mark.parametrize("prior", [False, True])
def test_unconfirmed_record_is_invalidated(tool, monkeypatch, prior):
    if prior:
        _record(tool)
        (tool.prefix / "bin/hz").write_text("updated")

    def fail(*_args):
        msg = "uncertain"
        raise HeinzelError(msg)

    monkeypatch.setattr(_installation_continuity, "_confirm_record", fail)
    with pytest.raises(HeinzelError, match="uncertain"):
        _record(tool)
    assert not (tool.prefix / ".heinzel-startup-state").exists()
    assert not tool.startup_state_matches()
    assert (tool.prefix / ".heinzel-startup-pending").exists()


def test_interruption_after_record_write_leaves_durable_pending_evidence(tool, monkeypatch):
    def interrupt(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(_installation_continuity, "_confirm_record", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _record(tool)
    assert (tool.prefix / ".heinzel-startup-state").exists()
    assert (tool.prefix / ".heinzel-startup-pending").exists()
    assert not tool.startup_state_matches()
    with pytest.raises(FileExistsError):
        _record(tool)


def test_pending_removal_denial_leaves_guard(tool, monkeypatch):
    original = os.unlink

    def deny(path, *args, **kwargs):
        if path == ".heinzel-startup-pending":
            raise PermissionError
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", deny)
    with pytest.raises(PermissionError):
        _record(tool)
    assert (tool.prefix / ".heinzel-startup-pending").exists()
    assert not tool.startup_state_matches()


def test_foreign_pending_replacement_is_never_removed(tool, monkeypatch):
    confirm = _installation_continuity._confirm_record
    marker = tool.prefix / ".heinzel-startup-pending"

    def replace_marker(*args):
        confirm(*args)
        other = tool.prefix / "foreign-pending"
        other.write_text("other writer")
        other.replace(marker)

    monkeypatch.setattr(_installation_continuity, "_confirm_record", replace_marker)
    with pytest.raises(HeinzelError, match="pending marker changed"):
        _record(tool)
    assert marker.read_text() == "other writer"
    assert not tool.startup_state_matches()


def test_unconfirmed_record_does_not_remove_an_already_replaced_record(tool, monkeypatch):
    record = tool.prefix / ".heinzel-startup-state"

    def replace_then_fail(*_args):
        replacement = tool.prefix / "new-record"
        replacement.write_text("other writer")
        Path(replacement).replace(record)
        msg = "uncertain"
        raise HeinzelError(msg)

    monkeypatch.setattr(_installation_continuity, "_confirm_record", replace_then_fail)
    with pytest.raises(HeinzelError, match="uncertain"):
        _record(tool)
    assert record.read_text() == "other writer"
    assert not tool.startup_state_matches()


def test_observation_reads_fixture_content_twice_and_validates_receipt_twice(tool, monkeypatch):
    files = tool.prefix / "lib/site-packages/example"
    for index in range(128):
        (files / f"part-{index}.py").write_bytes(b"x" * 1024)
    expected = sum(path.stat().st_size for path in tool.prefix.rglob("*") if path.is_file())
    original_read = os.read
    read_sizes = []

    def counted(fd, size):
        data = original_read(fd, size)
        read_sizes.append(len(data))
        return data

    monkeypatch.setattr(os, "read", counted)
    tool.startup_fingerprint()
    receipt_size = (tool.prefix / "uv-receipt.toml").stat().st_size
    assert sum(read_sizes) == 2 * expected + 2 * receipt_size


def test_replaced_prefix_does_not_inherit_prior_record(tool):
    _record(tool)
    old = tool.prefix.with_name("old")
    tool.prefix.rename(old)
    tool.prefix.mkdir()
    (tool.prefix / ".heinzel-startup-state").write_bytes((old / ".heinzel-startup-state").read_bytes())
    assert not tool.startup_state_matches()


def test_unstable_observation_cannot_be_recorded(tool, monkeypatch):
    _record(tool)
    expected = tool.startup_fingerprint()
    record = tool.prefix / ".heinzel-startup-state"
    prior = record.read_bytes()
    original = _installation_continuity._snapshot
    calls = []

    def changing(prefix: Path) -> str:
        calls.append(1)
        result = original(prefix)
        if len(calls) == 1:
            (prefix / "bin/hz").write_text("changed")
        return result

    monkeypatch.setattr(_installation_continuity, "_snapshot", changing)
    with pytest.raises(HeinzelError, match="changed"):
        tool.record_startup_state(expected=expected)
    assert record.read_bytes() == prior
    assert not tool.startup_state_matches()
