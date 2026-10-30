"""Exercise uv's collision behavior only in a private, guarded temporary tool store."""

import base64
import csv
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _fixture_wheel(directory, version, commands):
    name = "fixture_owned"
    metadata = f"{name}-{version}.dist-info"
    contents = {
        f"{name}/__init__.py": b'def run():\n    print("fixture")\n',
        f"{metadata}/METADATA": f"Metadata-Version: 2.1\nName: fixture-owned\nVersion: {version}\n".encode(),
        f"{metadata}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{metadata}/entry_points.txt": (
            "[console_scripts]\n" + "".join(f"{command} = {name}:run\n" for command in commands)
        ).encode(),
    }
    rows = [
        (name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("="), str(len(data)))
        for name, data in contents.items()
    ]
    rows.append((f"{metadata}/RECORD", "", ""))
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    contents[f"{metadata}/RECORD"] = output.getvalue().encode()
    wheel = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return wheel


def test_uv_reinstall_preserves_foreign_entrypoint_and_allows_restoration(tmp_path, monkeypatch):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not available")
    root = tmp_path.resolve(strict=True)
    if not root.is_relative_to(Path(tempfile.gettempdir()).resolve(strict=True)):
        pytest.fail("The fixture tool store must be under the system temporary directory")
    names = ("tools", "bin", "home", "cache", "pythons", "config", "data", "state", "logs", "tmp", "wheels")
    for name in names:
        (root / name).mkdir()
    environment = {
        "PATH": os.defpath,
        "HOME": str(root / "home"),
        "CFFIXED_USER_HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"),
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "bin"),
        "UV_CACHE_DIR": str(root / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(root / "pythons"),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_CONFIG": "1",
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_STATE_HOME": str(root / "state"),
        "HZ_CONFIG_HOME": str(root / "config"),
        "HZ_DATA_DIR": str(root / "data"),
        "HZ_STATE_DIR": str(root / "state"),
        "HZ_CACHE_DIR": str(root / "cache"),
        "HZ_LOG_DIR": str(root / "logs"),
    }
    python = os.path.realpath(sys.executable)
    uv = os.path.realpath(uv)

    def run(wheel, *, reinstall):
        for key, name in (
            ("UV_TOOL_DIR", "tools"),
            ("UV_TOOL_BIN_DIR", "bin"),
            ("HOME", "home"),
            ("UV_CACHE_DIR", "cache"),
            ("UV_PYTHON_INSTALL_DIR", "pythons"),
        ):
            path = root / name
            if path.is_symlink() or path.resolve(strict=True) != path or environment[key] != str(path):
                msg = "The fixture tool target changed"
                raise RuntimeError(msg)
            if not path.is_relative_to(root):
                msg = "The fixture tool target escaped its temporary root"
                raise RuntimeError(msg)
        if wheel.is_symlink() or not wheel.resolve(strict=True).is_relative_to(root / "wheels"):
            msg = "The fixture wheel escaped its temporary root"
            raise RuntimeError(msg)
        command = [uv, "tool", "install", "--offline", "--no-config", "--python", python]
        if reinstall:
            command.append("--reinstall")
        command.append(str(wheel))
        return subprocess.run(
            command, env=environment, cwd=root, capture_output=True, text=True, timeout=120, check=False
        )

    first = _fixture_wheel(root / "wheels", "1.0.0", ["hz"])
    previous = _fixture_wheel(root / "wheels", "1.0.1", ["hz"])
    candidate = _fixture_wheel(root / "wheels", "1.0.2", ["hz", "fixture-foreign"])
    assert run(first, reinstall=False).returncode == 0
    assert run(previous, reinstall=True).returncode == 0
    foreign = root / "bin" / "fixture-foreign"
    foreign.write_bytes(b"unrelated fixture sentinel\n")
    before = hashlib.sha256(foreign.read_bytes()).digest()
    failed = run(candidate, reinstall=True)
    assert failed.returncode != 0
    assert "fixture-foreign" in failed.stderr
    assert not foreign.is_symlink()
    assert hashlib.sha256(foreign.read_bytes()).digest() == before
    assert not (root / "tools" / "fixture-owned" / "uv-receipt.toml").exists()
    assert not (root / "bin" / "hz").exists()
    assert run(previous, reinstall=True).returncode == 0
    shim = root / "bin" / "hz"
    assert shim.is_symlink()
    assert shim.resolve(strict=True).is_relative_to(root / "tools" / "fixture-owned")
    assert (root / "tools" / "fixture-owned" / "uv-receipt.toml").is_file()
    assert not foreign.is_symlink()
    assert hashlib.sha256(foreign.read_bytes()).digest() == before

    _assert_dangling_guard(root, shim, monkeypatch, run, previous)


def _assert_dangling_guard(root, shim, monkeypatch, run, previous):
    # An unresolved foreign link is invisible to uv's exists() collision check. Our guard must stop
    # the command before its runner, even though the fixture would otherwise let uv replace the link.
    from heinzel import HeinzelError, Progress
    from heinzel.kernel import Installation, _installation

    dangling = root / "bin" / "fixture-dangling"
    missing = root / "absent-target"
    dangling.symlink_to(missing)
    later = _fixture_wheel(root / "wheels", "1.0.3", ["hz", "fixture-dangling"])
    monkeypatch.setattr(_installation, "CORE_DISTRIBUTION", "fixture-owned")
    installation = Installation(
        Progress(), prefix=root / "tools" / "fixture-owned", uv="fixture-guard", runner=lambda *_: pytest.fail("uv ran")
    )
    with pytest.raises(HeinzelError, match="unresolved uv executable symlinks"):
        installation.attempt(installation.tool_install_command(str(later), []), "fixture")
    assert dangling.is_symlink()
    assert dangling.readlink() == missing
    assert shim.is_symlink()
    assert (root / "tools" / "fixture-owned" / "uv-receipt.toml").is_file()

    # Recovery may proceed only for the same previously verified owned link, not a foreign one.
    dangling.unlink()
    prefix = root / "tools" / "fixture-owned"
    calls = []

    def guarded_runner(command, environment):
        calls.append((command, environment))
        assert environment["UV_TOOL_DIR"] == str(root / "tools")
        assert environment["UV_TOOL_BIN_DIR"] == str(root / "bin")
        return run(previous, reinstall=True)

    installation = Installation(Progress(), prefix=prefix, uv="fixture-guard", runner=guarded_runner)
    with installation.tool_transaction():
        (prefix / "bin" / "hz").unlink()
        assert shim.is_symlink()
        assert not shim.exists()
        result = installation.attempt(
            installation.tool_install_command(str(previous), []), "restore fixture", recovery=True
        )
    assert result.returncode == 0
    assert len(calls) == 1
    assert shim.resolve(strict=True).is_relative_to(prefix)
    assert (prefix / "uv-receipt.toml").is_file()
