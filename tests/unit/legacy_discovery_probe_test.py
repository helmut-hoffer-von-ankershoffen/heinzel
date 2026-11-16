"""The legacy discovery probe observes calls without retaining private values."""

import io
import json
import os
import runpy
import subprocess
import sys
import urllib.error
import zipfile
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
probe = runpy.run_path(str(Path(__file__).parents[2] / "scripts/acceptance/legacy_discovery_probe.py"))


def test_probe_returns_original_and_reraises_same_exception_without_secrets():
    recorder = probe["Probe"]()
    value = object()
    error = urllib.error.HTTPError("https://example.invalid/?token=secret", 429, "secret", None, None)
    calls = []

    def success(arg):
        calls.append(arg)
        return value

    def failure():
        calls.append("failed")
        raise error

    assert recorder.wrap(success, "requires_python")("x") is value
    with pytest.raises(urllib.error.HTTPError) as captured:
        recorder.wrap(failure, "highest_version")()
    assert captured.value is error
    assert calls == ["x", "failed"]
    assert recorder.events[1]["http_status"] == 429
    assert recorder.events[1]["exception_class"] == "HTTPError"
    assert "secret" not in json.dumps(recorder.events)
    error.close()


def test_probe_bounds_events_and_output_and_rejects_unicode_versions():
    recorder = probe["Probe"]()
    for _ in range(65):
        recorder.wrap(lambda: "0.9.49", "highest_version")()
    assert len(recorder.events) == 64
    assert recorder.truncated
    capture = probe["Capture"]()
    assert capture.write("x" * 70000) == 70000
    assert len(capture.data) == 65536
    assert capture.truncated
    assert probe["safe_version"]("\uff10.9.49") is None


def test_probe_checks_wheel_and_installed_source_before_import(tmp_path, monkeypatch):
    module = probe["verified_bridge"]
    source = b"example = 42\n"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("heinzel/example.py", source)
        archive.writestr("heinzel-0.9.43.dist-info/METADATA", b"Version: 0.9.43\n")
    data = stream.getvalue()
    wheel = tmp_path / "bridge.whl"
    wheel.write_bytes(data)
    prefix = tmp_path / "tools/heinzel"
    site = prefix / "lib/python3.12/site-packages/heinzel"
    site.mkdir(parents=True)
    (site / "example.py").write_bytes(source)
    dist_info = site.parent / "heinzel-0.9.43.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_bytes(b"Version: 0.9.43\n")
    monkeypatch.setitem(module.__globals__, "BRIDGE_SHA256", sha256(data).hexdigest())
    module(wheel, prefix)
    bytecode = site / "__pycache__/example.cpython-312.pyc"
    bytecode.parent.mkdir()
    bytecode.write_bytes(b"unsafe")
    with pytest.raises(ValueError, match="bytecode"):
        module(wheel, prefix)
    bytecode.unlink()
    link = site / "extra"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        module(wheel, prefix)
    link.unlink()
    (site / "example.py").write_text("changed")
    with pytest.raises(ValueError, match="installed source differs"):
        module(wheel, prefix)


def test_probe_refuses_tampered_wheel_and_symlink(tmp_path):
    wheel = tmp_path / "bridge.whl"
    wheel.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="digest mismatch"):
        probe["verified_bridge"](wheel, tmp_path)
    link = tmp_path / "link.whl"
    link.symlink_to(wheel)
    with pytest.raises(OSError, match=r"(?i)symbolic|loop"):
        probe["verified_bridge"](link, tmp_path)


def test_probe_rejects_private_path_escape_and_wrong_interpreter(tmp_path, monkeypatch):
    root = tmp_path / "case"
    root.mkdir()
    names = {
        "HOME": "home",
        "TMPDIR": "tmp",
        "UV_TOOL_DIR": "tools",
        "UV_TOOL_BIN_DIR": "bin",
        "UV_PYTHON_INSTALL_DIR": "uv-python",
        "UV_CACHE_DIR": "uv-cache",
        "HZ_CONFIG_HOME": "config_home",
        "HZ_DATA_DIR": "data_dir",
        "HZ_CACHE_DIR": "cache_dir",
        "HZ_STATE_DIR": "state_dir",
        "HZ_LOG_DIR": "log_dir",
        "HZ_SYSTEM_DAEMON__UNIT_DIR": "units",
        "XDG_CACHE_HOME": "xdg-cache",
        "XDG_CONFIG_HOME": "xdg-config",
        "XDG_DATA_HOME": "xdg-data",
        "XDG_STATE_HOME": "xdg-state",
    }
    env = {name: str(root / value) for name, value in names.items()}
    env.update(UV_NO_CONFIG="1", PATH=f"{root}/bin:/usr/bin:/bin")
    (root / "tools/heinzel/bin").mkdir(parents=True)
    (root / "uv-python").mkdir()
    python = root / "uv-python/python3.12"
    python.touch()
    executable = root / "tools/heinzel/bin/python"
    executable.symlink_to(python)
    flags = SimpleNamespace(isolated=True, no_site=True)
    monkeypatch.chdir(root)
    assert probe["isolated_root"](root, environment=env, executable=executable, flags=flags, version=(3, 12))
    escaped = dict(env, HZ_DATA_DIR=str(tmp_path / "outside"))
    with pytest.raises(ValueError, match="private environment"):
        probe["isolated_root"](root, environment=escaped, executable=executable, flags=flags, version=(3, 12))
    with pytest.raises(ValueError, match="managed interpreter"):
        probe["isolated_root"](root, environment=env, executable=sys.executable, flags=flags, version=(3, 12))
    with pytest.raises(ValueError, match="with -S"):
        probe["isolated_root"](
            root,
            environment=env,
            executable=executable,
            flags=SimpleNamespace(isolated=True, no_site=False),
            version=(3, 12),
        )


def test_native_and_child_output_is_not_forwarded():
    code = """import atexit, os, subprocess, sys
atexit.register(lambda: os.write(1, b"secret-exit"))
os.write(1, b"secret-native")
subprocess.run([sys.executable, "-c", "import os; os.write(2, b'secret-child')"], check=True)
os.write(int(sys.argv[1]), b'{"safe": true}')
"""
    result = probe["supervise"]([sys.executable, "-I", "-c", code], dict(os.environ), Path.cwd())
    assert result == {"safe": True}


def test_supervisor_sanitizes_even_hostile_worker_result():
    unsafe = {
        "kind": "instrumented-non-acceptance",
        "events": [
            {
                "operation": "highest_version",
                "category": "value",
                "version": "secret",
                "duration_ms": 1,
                "http_status": "secret",
                "extra": "secret",
            }
        ],
        "post_interpreter": "secret",
        "update": {"before": "secret"},
        "extra": "secret",
    }
    result = probe["sanitized_result"](unsafe)
    assert "secret" not in json.dumps(result)
    assert result["events"][0]["version"] is None
    unsafe["events"][0]["operation"] = "secret"
    assert probe["sanitized_result"](unsafe)["refused"] is True


def test_preflight_refusal_never_echoes_paths(tmp_path):
    script = Path(__file__).parents[2] / "scripts/acceptance/legacy_discovery_probe.py"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(script),
            "--case-root",
            str(tmp_path / "secret"),
            "--wheel",
            str(tmp_path / "secret.whl"),
            "--confirm-dedicated-vm",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["refused"] is True
    assert "secret" not in result.stdout + result.stderr
