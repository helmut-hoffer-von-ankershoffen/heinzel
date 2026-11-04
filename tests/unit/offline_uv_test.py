"""Offline removal trusts installed publisher bytes, never uv imports, PATH or cached downloads."""

import base64
import hashlib
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from heinzel import HeinzelError
from heinzel.kernel import _installed_metadata, _offline_uv

pytestmark = pytest.mark.unit


@pytest.fixture
def dependency(tmp_path, monkeypatch):
    prefix = tmp_path / "tool"
    metadata = prefix / "lib/python3.13/site-packages/uv-0.13.7.dist-info"
    (metadata / "licenses").mkdir(parents=True)
    (prefix / "bin").mkdir()
    binary = prefix / "bin/uv"
    binary.write_bytes(b"immutable native fixture")
    binary.chmod(0o500)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (metadata / "METADATA").write_bytes(b"Name: uv\nVersion: 0.13.7\n")
    (metadata / "WHEEL").write_bytes(b"native wheel fixture")
    for name in _offline_uv.LICENSE_HASHES:
        (metadata / "licenses" / name).write_bytes(name.encode())
    (metadata / "RECORD").write_text(
        "../../../bin/uv,sha256="
        + base64.urlsafe_b64encode(bytes.fromhex(digest)).decode().rstrip("=")
        + f",{binary.stat().st_size}\n"
    )
    monkeypatch.setattr(_offline_uv, "selection", lambda: (digest, hashlib.sha256(b"native wheel fixture").hexdigest()))
    monkeypatch.setattr(_offline_uv, "METADATA_HASH", hashlib.sha256((metadata / "METADATA").read_bytes()).hexdigest())
    monkeypatch.setattr(
        _offline_uv,
        "LICENSE_HASHES",
        {name: hashlib.sha256(name.encode()).hexdigest() for name in _offline_uv.LICENSE_HASHES},
    )
    return prefix, metadata, binary


def test_stage_copies_opened_dependency_before_executing_with_exact_environment(dependency, tmp_path, monkeypatch):
    prefix, _metadata, binary = dependency
    monkeypatch.setenv("PATH", "/foreign/uv")
    monkeypatch.setenv("UV_INDEX_URL", "must-not-leak")
    monkeypatch.setitem(sys.modules, "uv", None)
    calls = []

    def run(command, **kwargs):
        copied = Path(command[0])
        assert copied != binary
        assert copied.read_bytes() == binary.read_bytes()
        assert copied.stat().st_mode & 0o777 == 0o500
        assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C"}
        assert command[1:] == ["--offline", "--no-config", "--no-python-downloads", "--version"]
        calls.append(command)
        return CompletedProcess(command, 0, "uv 0.13.7 (fixture)\n", "")

    monkeypatch.setattr(_offline_uv.subprocess, "run", run)
    result = _offline_uv.stage(prefix, tmp_path / "private")
    result.verify()
    assert len(calls) == 1
    assert (result.path.parent / "LICENSE.uv").is_file()


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "hash",
        "metadata",
        "record",
        "wheel-architecture",
        "symlink",
        "writable",
        "direct-source",
        "ambiguous",
    ],
)
def test_invalid_dependency_refuses_before_execution(dependency, tmp_path, monkeypatch, change):
    prefix, metadata, binary = dependency
    if change == "missing":
        binary.unlink()
    elif change == "hash":
        binary.chmod(0o700)
        binary.write_bytes(b"foreign bytes")
    elif change == "metadata":
        (metadata / "METADATA").write_text("Name: uv\nVersion: 0.13.7\nModified: true\n")
    elif change == "record":
        (metadata / "RECORD").write_text("../../../bin/uv,sha256=foreign,7\n")
    elif change == "wheel-architecture":
        (metadata / "WHEEL").write_text("wrong native architecture")
    elif change == "symlink":
        binary.rename(binary.with_suffix(".foreign"))
        binary.symlink_to(binary.with_suffix(".foreign"))
    elif change == "writable":
        binary.parent.chmod(0o770)
    elif change == "direct-source":
        (metadata / "direct_url.json").write_text('{"url":"file:///foreign"}')
    else:
        duplicate = metadata.with_name("uv-0.13.4.dist-info")
        duplicate.mkdir()
        (duplicate / "METADATA").write_text("Name: uv\nVersion: 0.13.4\n")
    monkeypatch.setattr(_offline_uv.subprocess, "run", lambda *args, **kwargs: pytest.fail("No untrusted uv execution"))
    with pytest.raises((HeinzelError, OSError)):
        _offline_uv.stage(prefix, tmp_path / "private")
    assert not (tmp_path / "private").exists()


def test_unsafe_staging_refuses_before_probe(dependency, tmp_path, monkeypatch):
    prefix, _metadata, _binary = dependency
    directory = tmp_path / "private"
    directory.symlink_to(prefix, target_is_directory=True)
    monkeypatch.setattr(
        _offline_uv.subprocess, "run", lambda *args, **kwargs: pytest.fail("No redirected staging probe")
    )
    with pytest.raises((HeinzelError, OSError)):
        _offline_uv.stage(prefix, directory)


def test_no_ambient_distribution_search_for_dependency(dependency, monkeypatch):
    prefix, _metadata, _binary = dependency
    observed = []
    original = _installed_metadata.distributions

    def selected(*, path):
        observed.append(path)
        return original(path=path)

    monkeypatch.setattr(_installed_metadata, "distributions", selected)
    _offline_uv._installed_bytes(prefix)
    assert observed == [[str(prefix / "lib/python3.13/site-packages")]]
