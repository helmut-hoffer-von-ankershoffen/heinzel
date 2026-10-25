"""Recording isolation: every invocation gets private temporary state, even when setup fails."""

import errno
import importlib.util
import stat
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/docs_record.py"
spec = importlib.util.spec_from_file_location("docs_record", SCRIPT)
assert spec
assert spec.loader
recorder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = recorder
spec.loader.exec_module(recorder)


def test_recording_uses_a_fresh_private_sandbox_and_cleans_it_on_failure(monkeypatch):
    sandboxes = []

    def unavailable(env):
        directory = Path(env["HZ_CONFIG_HOME"]).parent
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert directory not in sandboxes
        sandboxes.append(directory)
        msg = "Tool unavailable"
        raise recorder.RecordError(msg)

    monkeypatch.setattr(recorder, "tools", unavailable)
    assert recorder.main([]) == 1
    assert recorder.main([]) == 1
    assert len(sandboxes) == 2
    assert all(not directory.exists() for directory in sandboxes)


def test_recordings_do_not_inherit_the_real_home_credentials_or_relative_executables(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "real-home"))
    monkeypatch.setenv("GITHUB_TOKEN", "not-a-real-token")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-a-real-secret")
    monkeypatch.setenv("BASH_ENV", str(tmp_path / "real-shell-startup"))
    monkeypatch.setenv("PATH", f".:{tmp_path / 'tools'}")
    sandbox = tmp_path / "sandbox"
    env = recorder.environment(sandbox)
    assert env["HOME"] == str(sandbox / "home")
    assert env["XDG_CONFIG_HOME"] == str(sandbox / "home" / ".config")
    assert env["BASH_ENV"] == "/dev/null"
    assert "GITHUB_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert all(Path(part).is_absolute() for part in env["PATH"].split(recorder.os.pathsep))


def test_custom_output_directories_are_reported_after_a_successful_recording(tmp_path, monkeypatch, capsys):
    asset = tmp_path / "outside-checkout" / "scene.png"
    asset.parent.mkdir()
    asset.write_bytes(b"verified media")
    for name in ("tools", "web_app_built", "install"):
        monkeypatch.setattr(recorder, name, lambda *_args: None)
    monkeypatch.setattr(recorder, "pages", lambda *_args, **_kwargs: [object()])
    monkeypatch.setattr(recorder, "record_page", lambda *_args, **_kwargs: [asset])
    assert recorder.main(["--directory", str(asset.parent)]) == 0
    assert "scene.png" in capsys.readouterr().out


@pytest.mark.parametrize("extension", ["gif", "webp", "mp4"])
def test_recording_export_preserves_the_previous_asset_when_any_format_exceeds_the_hook_limit(
    tmp_path, monkeypatch, extension
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"source")
    target = tmp_path / "published" / "scene"
    target.parent.mkdir()
    original = target.with_suffix(f".{extension}")
    original.write_bytes(b"previous verified recording")
    monkeypatch.setattr(recorder, "ENCODINGS", ((extension, []),))

    def oversized(command, **_kwargs):
        output = Path(command[-1])
        output.write_bytes(b"x" * (501 * 1024) if output.suffix == f".{extension}" else b"still")
        return ""

    monkeypatch.setattr(recorder, "run", oversized)
    with pytest.raises(recorder.RecordError, match="size limit"):
        recorder.publish(video, target, {}, budget_mb=2)
    assert original.read_bytes() == b"previous verified recording"


def test_verified_recordings_export_across_filesystems_without_leaving_temporary_assets(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"source")
    target = tmp_path / "published" / "scene"
    monkeypatch.setattr(recorder, "ENCODINGS", (("gif", []),))

    def encode(command, **_kwargs):
        Path(command[-1]).write_bytes(b"verified media")
        return ""

    replace = recorder.os.replace

    def cross_device(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is None or src_dir_fd != dst_dir_fd:
            raise OSError(errno.EXDEV, "Different filesystems")
        return replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(recorder, "run", encode)
    monkeypatch.setattr(recorder.os, "replace", cross_device)
    exported = recorder.publish(video, target, {}, budget_mb=2)
    assert {path.name for path in exported} == {"scene.gif", "scene.png"}
    assert all(path.read_bytes() == b"verified media" for path in exported)
    assert sorted(target.parent.iterdir()) == sorted(exported)


def test_new_recording_files_and_directories_start_private(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"source")
    target = tmp_path / "private" / "assets" / "scene"

    def encode(command, **_kwargs):
        Path(command[-1]).write_bytes(b"verified media")
        return ""

    monkeypatch.setattr(recorder, "run", encode)
    previous = recorder.os.umask(0)
    try:
        exported = recorder.publish(video, target, {}, budget_mb=2)
    finally:
        recorder.os.umask(previous)
    assert exported
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in exported)
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.parent.parent.stat().st_mode) == 0o700
