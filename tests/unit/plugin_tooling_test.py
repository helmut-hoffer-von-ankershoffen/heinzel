"""Plugin tooling executes commands only after verified wheel installation succeeds."""

import importlib.util
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/plugin_tooling.py"
spec = importlib.util.spec_from_file_location("plugin_tooling_under_test", SCRIPT)
assert spec
assert spec.loader
tooling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tooling)


def test_failed_hash_locked_install_never_executes_toolkit(monkeypatch):
    calls = []
    monkeypatch.setattr(tooling.shutil, "which", lambda _: "/fictional/uv")

    def execute(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["pip", "install"]:
            assert "--require-hashes" in command
            assert "--no-build" in command
            assert command[command.index("--requirements") + 1] == str(tooling.REQUIREMENTS)
            raise subprocess.CalledProcessError(1, command)
        assert command[1] == "venv", "only environment creation may precede verified installation"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tooling.subprocess, "run", execute)
    with pytest.raises(subprocess.CalledProcessError):
        tooling.run("hz-plugin", ["check"])
    assert len(calls) == 2


def test_tampered_candidate_never_reaches_installation(monkeypatch):
    calls = []
    monkeypatch.setattr(tooling.shutil, "which", lambda _: "/fictional/uv")
    monkeypatch.setattr(tooling.urllib.request, "urlopen", lambda *_, **__: io.BytesIO(b"tampered wheel"))

    def execute(command, **kwargs):
        calls.append(command)
        assert command[1] == "venv", "a tampered candidate must not be installed or executed"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tooling.subprocess, "run", execute)
    with pytest.raises(ValueError, match="integrity check"):
        tooling.run("hz-plugin", ["check"], toolkit="https://example.test/toolkit.whl#sha256=" + "a" * 64)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "candidate",
    [
        "https://example.test/toolkit.whl",
        "https://example.test/toolkit.whl#" + "a" * 64,
        "http://example.test/toolkit.whl#sha256=" + "a" * 64,
        "https://example.test/toolkit.tar.gz#sha256=" + "a" * 64,
    ],
)
def test_remote_candidates_require_https_wheels_and_digests(candidate, tmp_path, monkeypatch):
    def refuse_download(*args, **kwargs):
        pytest.fail("an invalid candidate reached the network")

    monkeypatch.setattr(tooling.urllib.request, "urlopen", refuse_download)
    with pytest.raises(ValueError, match="HTTPS wheel URL"):
        tooling.candidate_wheel(candidate, tmp_path)


def test_verified_candidate_is_the_only_artifact_returned(tmp_path, monkeypatch):
    content = b"verified wheel fixture"
    digest = tooling.hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(tooling.urllib.request, "urlopen", lambda *_, **__: io.BytesIO(content))
    wheel = tooling.candidate_wheel("https://example.test/toolkit.whl#sha256=" + digest, tmp_path)
    assert wheel == tmp_path / "toolkit.whl"
    assert wheel.read_bytes() == content


def test_only_documented_toolkit_programs_can_execute(monkeypatch):
    def refuse_run(*args, **kwargs):
        pytest.fail("an unsupported executable reached a subprocess")

    monkeypatch.setattr(tooling.subprocess, "run", refuse_run)
    with pytest.raises(ValueError, match="Choose hz-plugin or python"):
        tooling.run("bash", ["-c", "exit 0"])


def test_local_candidate_with_option_shaped_name_becomes_an_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    candidate = Path("--target=unexpected.whl")
    candidate.write_bytes(b"local developer wheel fixture")
    assert tooling.candidate_wheel(str(candidate), tmp_path) == candidate.resolve()
