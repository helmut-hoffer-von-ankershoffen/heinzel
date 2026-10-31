"""Standard app receipts retain update policy across installer and service layers."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.heads import install

pytestmark = pytest.mark.integration


def profile_file(tmp_path):
    artifacts = []
    for name in ("heinzel", "heinzel-unforeseen"):
        wheel = tmp_path / f"{name.replace('-', '_')}-0.9.0-py3-none-any.whl"
        wheel.write_bytes(b"local artifact")
        artifacts.append(
            {
                "name": name,
                "version": "0.9.0",
                "filename": wheel.name,
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "url": str(wheel),
            }
        )
    profile = tmp_path / "standard.json"
    profile.write_text(json.dumps({"format": 2, "core_version": "0.9.0", "sdk": 1, "artifacts": artifacts}))
    return profile


def test_standard_receipt_preserves_ordinary_index_updates(tmp_path, monkeypatch):  # noqa: PLR0915
    from contextlib import nullcontext

    from heinzel import Progress
    from heinzel.heads._tool_install import ToolTarget
    from heinzel.kernel import Installation, ReleaseSource
    from heinzel.modules.system._update import UpdateService

    monkeypatch.delenv("UV_NO_MANAGED_PYTHON", raising=False)
    monkeypatch.delenv("UV_PYTHON_PREFERENCE", raising=False)
    file = profile_file(tmp_path)
    commands = []
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/bin/uv")
    monkeypatch.setattr(install, "existing_requirements", lambda *_args: [])
    target = ToolTarget(tmp_path / "tools", tmp_path / "bin")
    monkeypatch.setattr(install, "resolve_target", lambda _uv: target)

    class Kernel:
        def bind(self, _kind, _value):
            pass

        def provides(self, _kind):
            return True

        def resolve(self, _kind):
            class Lifecycle:
                def quiescent(self, _binary):
                    return nullcontext()

            return Lifecycle()

    monkeypatch.setattr(install, "boot", lambda _settings: Kernel())

    def verified(command, _target, _expected, _wheels, _installation, _preserved):
        overrides = Path(command[command.index("--override") + 1])
        commands.append((command, overrides.read_text()))
        return "a" * 64

    recorded = []
    monkeypatch.setattr(install, "run_tool", verified)
    monkeypatch.setattr(
        Installation, "record_startup_state", lambda self, *, expected: recorded.append(expected), raising=False
    )
    assert install.main(["--profile", str(file)]) == 0
    [(command, override_text)] = commands
    assert "--force" not in command
    assert command[3] == "heinzel"
    assert command[-2:] == ["--with", "heinzel-unforeseen"]
    overrides = Path(command[command.index("--override") + 1])
    assert "heinzel @ file:" in override_text
    assert not overrides.exists()
    assert "app-artifacts" in str(overrides)
    assert "cache" not in overrides.parts
    receipt = overrides.parent.parent / "standard-app.json"
    assert install.read_profile(str(receipt)).core_version == "0.9.0"
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert recorded == ["a" * 64]
    # uv records index requirements and wheel overrides alongside the installed entrypoint.
    prefix = tmp_path / "tools" / "heinzel"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "hz").write_text("fixture", encoding="utf-8")
    base = tmp_path / "base-python"
    assert base.parent.resolve() == tmp_path.resolve()
    assert not base.exists()
    assert not base.is_symlink()
    base.write_text("#!/bin/sh\nexit 0\n")
    assert base.resolve().is_relative_to(tmp_path.resolve())
    assert not base.samefile(sys.executable)
    assert not base.samefile(sys._base_executable)
    base.chmod(0o700)
    (prefix / "bin" / "python").symlink_to(base)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "hz"
    shim.symlink_to(prefix / "bin" / "hz")
    (prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements=[{name="heinzel"}]\noverrides=[{name="heinzel", path="verified.whl"}]\n'
        f"entrypoints=[{{name='hz', install-path={str(shim)!r}}}]\n",
        encoding="utf-8",
    )
    info = prefix / "lib/python3.14/site-packages/heinzel-0.9.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.0\n")
    updates = []

    def update_runner(args, environment):
        updates.append((args, environment))
        return subprocess.CompletedProcess(args, 0, "", "")

    target = Installation(Progress(), prefix=prefix, uv="uv", runner=update_runner)
    assert target.core_pin().kind == "index"
    assert target.core_pin().requirement == "heinzel"

    class Releases(ReleaseSource):
        def highest_version(self, timeout, *, prereleases=False):
            return "0.9.1"

    from heinzel import HeinzelError

    with pytest.raises(HeinzelError, match="constraints cannot be safely preserved"):
        UpdateService(target, Releases()).update(current="0.9.0")
    assert not updates


def test_plain_index_receipt_can_update_with_real_installed_metadata(tmp_path, monkeypatch):
    from heinzel import Progress
    from heinzel.kernel import Installation, ReleaseSource
    from heinzel.modules.system._update import UpdateService

    prefix = tmp_path / "tools" / "heinzel"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "hz").write_text("fixture", encoding="utf-8")
    base = tmp_path / "base-python"
    assert base.parent.resolve() == tmp_path.resolve()
    assert not base.exists()
    assert not base.is_symlink()
    base.write_text("#!/bin/sh\nexit 0\n")
    assert base.resolve().is_relative_to(tmp_path.resolve())
    assert not base.samefile(sys.executable)
    assert not base.samefile(sys._base_executable)
    base.chmod(0o700)
    (prefix / "bin" / "python").symlink_to(base)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "hz"
    shim.symlink_to(prefix / "bin" / "hz")
    (prefix / "uv-receipt.toml").write_text(
        f"[tool]\nrequirements=[{{name=\"heinzel\"}}]\nentrypoints=[{{name='hz', install-path={str(shim)!r}}}]\n",
        encoding="utf-8",
    )
    site = prefix / "lib/python3.14/site-packages"
    before = site / "heinzel-0.9.0.dist-info"
    before.mkdir(parents=True)
    (before / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.0\n")
    updates = []

    def update_runner(args, environment):
        constraints = Path(args[args.index("--constraints") + 1]).read_text()
        updates.append((args, environment, constraints))
        after = site / "heinzel-0.9.1.dist-info"
        before.rename(after)
        (after / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.1\n")
        return subprocess.CompletedProcess(args, 0, "", "")

    target = Installation(Progress(), prefix=prefix, uv="uv", runner=update_runner)
    monkeypatch.setattr(target, "hz", lambda *args: "hz 0.9.1" if args == ("--version",) else "[]")

    class Releases(ReleaseSource):
        def highest_version(self, timeout, *, prereleases=False):
            return "0.9.1"

    result = UpdateService(target, Releases()).update(current="0.9.0")
    assert result.updated
    assert result.after == "0.9.1"
    [command, environment, constraints] = updates[0]
    assert "heinzel" in command
    assert constraints == "heinzel>=0.9.0\n"
    assert environment["UV_TOOL_DIR"] == str(prefix.parent)
    assert environment["UV_TOOL_BIN_DIR"] == str(bin_dir)
