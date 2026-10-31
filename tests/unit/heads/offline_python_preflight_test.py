"""Selected wheel Python bounds are checked without executing managed interpreters."""

import hashlib
import platform
import subprocess
import sys
import zipfile

import pytest

from heinzel import HeinzelError
from heinzel.heads import _tool_install as tool
from heinzel.kernel import ProcessOutcome

pytestmark = pytest.mark.unit


def _wheel(path, requirement):
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(
            "fixture-1.0.dist-info/METADATA", f"Name: fixture\nVersion: 1.0\nRequires-Python: {requirement}\n"
        )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _key(version, *, system=None, architecture=None):
    system = system or {"darwin": "macos", "linux": "linux", "win32": "windows"}[sys.platform]
    machine = platform.machine().lower()
    architecture = architecture or {"arm64": "aarch64", "amd64": "x86_64"}.get(machine, machine)
    return f"cpython-{version}-{system}-{architecture}-none"


def test_downloads_enabled_skips_wheel_and_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "run_bounded_process", lambda *_a, **_kw: pytest.fail("must not query"))
    tool.verify_offline_managed_python("uv", {tmp_path / "missing": "x"}, {})


@pytest.mark.parametrize("policy", ["never", "manual"])
@pytest.mark.parametrize(
    "scenario",
    [
        ([], [">=3.12"], False),
        (["3.14.8"], [">=3.12", "<3.15"], True),
        (["3.14.8"], [">=3.15"], False),
        (["3.12.15", "3.14.8"], [">=3.13", "<3.15"], True),
        (["3.12.15", "3.14.8"], [">=3.13", "<3.14"], False),
    ],
)
def test_selected_wheel_bounds_and_nonexecuting_managed_inventory(tmp_path, monkeypatch, policy, scenario):
    versions, requirements, allowed = scenario
    calls = []
    for version in versions:
        (tmp_path / _key(version)).mkdir()

    def inventory(command, **kwargs):
        calls.append((command, kwargs))
        return ProcessOutcome(0, str(tmp_path), "")

    monkeypatch.setattr(tool, "run_bounded_process", inventory)
    wheels = dict(_wheel(tmp_path / f"wheel-{n}.whl", requirement) for n, requirement in enumerate(requirements))
    env = {"UV_PYTHON_DOWNLOADS": policy, "UV_PYTHON": "/foreign", "VIRTUAL_ENV": "/foreign-venv"}
    if allowed:
        tool.verify_offline_managed_python("uv", wheels, env)
    else:
        with pytest.raises(HeinzelError, match="no compatible uv-managed Python"):
            tool.verify_offline_managed_python("uv", wheels, env)
    assert calls[0][0] == ["uv", "python", "dir", "--offline"]
    assert "UV_PYTHON" not in calls[0][1]["env"]
    assert "VIRTUAL_ENV" not in calls[0][1]["env"]
    assert env["UV_PYTHON"] == "/foreign"


def test_other_platform_does_not_count(tmp_path, monkeypatch):
    (tmp_path / _key("3.14.8", system="windows")).mkdir()
    monkeypatch.setattr(tool, "run_bounded_process", lambda *_a, **_kw: ProcessOutcome(0, str(tmp_path), ""))
    with pytest.raises(HeinzelError, match="no compatible"):
        tool.verify_offline_managed_python(
            "uv", dict([_wheel(tmp_path / "x.whl", ">=3.12")]), {"UV_PYTHON_DOWNLOADS": "never"}
        )


def test_other_arch_defers_to_uv_without_executing_interpreter(tmp_path, monkeypatch):
    other_arch = "x86_64" if platform.machine().lower() in {"arm64", "aarch64"} else "aarch64"
    executable = tmp_path / _key("3.14.8", architecture=other_arch) / "bin" / "python"
    executable.parent.mkdir(parents=True)
    marker = tmp_path / "executed"
    executable.write_text(f"#!/bin/sh\ntouch {marker}\n")
    executable.chmod(0o700)
    monkeypatch.setattr(tool, "run_bounded_process", lambda *_a, **_kw: ProcessOutcome(0, str(tmp_path), ""))
    tool.verify_offline_managed_python(
        "uv", dict([_wheel(tmp_path / "x.whl", ">=3.12")]), {"UV_PYTHON_DOWNLOADS": "never"}
    )
    assert not marker.exists()


def test_unknown_managed_layout_is_not_rejected_as_incompatible(tmp_path, monkeypatch):
    (tmp_path / "unrecognized-managed-layout").mkdir()
    monkeypatch.setattr(tool, "run_bounded_process", lambda *_a, **_kw: ProcessOutcome(0, str(tmp_path), ""))
    tool.verify_offline_managed_python(
        "uv", dict([_wheel(tmp_path / "x.whl", ">=3.15")]), {"UV_PYTHON_DOWNLOADS": "never"}
    )


@pytest.mark.parametrize(
    "outcome",
    [ProcessOutcome(0, "relative", ""), ProcessOutcome(0, "", "", truncated=True), ProcessOutcome(None, "", "")],
)
def test_unverifiable_inventory_is_refused(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(tool, "run_bounded_process", lambda *_a, **_kw: outcome)
    with pytest.raises(HeinzelError, match="Cannot verify"):
        tool.verify_offline_managed_python(
            "uv", dict([_wheel(tmp_path / "x.whl", ">=3.12")]), {"UV_PYTHON_DOWNLOADS": "never"}
        )


def test_invalid_wheel_metadata_and_digest_refused_before_inventory(tmp_path, monkeypatch):
    path, digest = _wheel(tmp_path / "x.whl", "not-a-specifier")
    monkeypatch.setattr(tool, "run_bounded_process", lambda *_a, **_kw: pytest.fail("must not query"))
    for expected in (digest, "0" * 64):
        with pytest.raises(HeinzelError, match="Cannot verify"):
            tool.verify_offline_managed_python("uv", {path: expected}, {"UV_PYTHON_DOWNLOADS": "never"})


def test_inventory_timeout_has_fixed_guidance(tmp_path, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["uv"], 15)

    monkeypatch.setattr(tool, "run_bounded_process", timeout)
    with pytest.raises(HeinzelError, match="Cannot verify"):
        tool.verify_offline_managed_python(
            "uv", dict([_wheel(tmp_path / "x.whl", ">=3.12")]), {"UV_PYTHON_DOWNLOADS": "never"}
        )
