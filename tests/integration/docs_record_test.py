"""The documentation recorder runs real stdin pipelines without a shell."""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/docs_record.py"
spec = importlib.util.spec_from_file_location("docs_record_integration", SCRIPT)
assert spec
assert spec.loader
recorder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = recorder
spec.loader.exec_module(recorder)


def test_transcripts_pass_stdin_to_readers_without_executing_shell_syntax(tmp_path, monkeypatch):
    executable = tmp_path / "hz"
    executable.write_text(f"#!{sys.executable}\nimport sys\nprint(sys.argv[1])\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    scene = recorder.Scene(name="safe", title="Safe", kind="cli", commands=["hz ';touch stolen' | cat -n"])
    transcript = recorder.transcript_cli(scene, {"PATH": f"{tmp_path}{os.pathsep}{os.defpath}"})
    assert "1\t;touch stolen" in transcript
    assert not (tmp_path / "stolen").exists()


def test_pipeline_diagnostics_are_shown_without_becoming_the_next_readers_stdin(tmp_path):
    executable = tmp_path / "hz"
    executable.write_text(
        f'#!{sys.executable}\nimport sys\nprint("fixture diagnostic", file=sys.stderr)\n', encoding="utf-8"
    )
    executable.chmod(0o700)
    scene = recorder.Scene(name="diagnostic", title="Diagnostic", kind="cli", commands=["hz sample | cat -n"])
    transcript = recorder.transcript_cli(scene, {"PATH": f"{tmp_path}{os.pathsep}{os.defpath}"})
    assert "fixture diagnostic" in transcript
    assert "1\tfixture diagnostic" not in transcript


def test_a_silent_web_backend_times_out_and_the_owned_process_is_reaped(tmp_path, monkeypatch):
    executable = tmp_path / "hz"
    executable.write_text(f"#!{sys.executable}\nimport signal\nsignal.pause()\n", encoding="utf-8")
    executable.chmod(0o700)
    children = []
    original = recorder.subprocess.Popen

    def start(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(recorder.subprocess, "Popen", start)
    monkeypatch.setattr(recorder, "STARTUP_TIMEOUT", 0.05, raising=False)
    try:
        with (
            pytest.raises(recorder.RecordError, match=r"start|ready"),
            recorder.web_app({"PATH": str(tmp_path)}),
        ):
            pytest.fail("A silent backend cannot be ready")
        assert len(children) == 1
        assert children[0].poll() is not None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(5)


def test_a_ready_backend_yields_only_its_loopback_session_and_stops_on_context_exit(tmp_path):
    executable = tmp_path / "hz"
    executable.write_text(
        f"#!{sys.executable}\nimport signal\n"
        'print("http://127.0.0.1:45678/#token=fixture-token", flush=True)\nsignal.pause()\n',
        encoding="utf-8",
    )
    executable.chmod(0o700)
    with recorder.web_app({"PATH": str(tmp_path)}) as url:
        assert url == "http://127.0.0.1:45678/#token=fixture-token"
