"""A standard composition is verified before one isolated installation; minimal core stays independent."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from heinzel.heads import install

pytestmark = pytest.mark.unit


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
    profile.write_text(json.dumps({"format": 1, "core_version": "0.9.0", "sdk": 1, "artifacts": artifacts}))
    return profile


def test_exact_composition_is_cached_and_explicit_removals_win(tmp_path):
    profile = install.read_profile(str(profile_file(tmp_path)))
    artifacts = install.prepare(profile, tmp_path / "cache", {"heinzel-unforeseen"})
    assert [path.name for path in artifacts] == ["heinzel-0.9.0-py3-none-any.whl"]
    assert artifacts[0].parent.name == profile.artifacts[0].sha256
    assert artifacts[0].stat().st_mode & 0o777 == 0o600


def test_any_tampered_artifact_aborts_before_installation(tmp_path, monkeypatch):
    profile = profile_file(tmp_path)
    (tmp_path / "heinzel_unforeseen-0.9.0-py3-none-any.whl").write_bytes(b"modified")
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    assert install.main(["--profile", str(profile), "--python", "/isolated/bin/python"]) == 1
    assert calls == []


@pytest.mark.parametrize("change", ["duplicate", "wrong-name", "wrong-core", "future-sdk"])
def test_invalid_profiles_fail_closed(tmp_path, change):
    file = profile_file(tmp_path)
    data = json.loads(file.read_text())
    if change == "duplicate":
        data["artifacts"].append(data["artifacts"][1])
    elif change == "wrong-name":
        data["artifacts"][1]["name"] = "heinzel-different"
    elif change == "wrong-core":
        data["core_version"] = "0.8.0"
    else:
        data["sdk"] = 2
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=r"duplicate|mismatch|core|sdk"):
        install.read_profile(str(file))


def test_explicit_removal_survives_refresh_until_explicit_add(tmp_path):
    history = tmp_path / "plugins.jsonl"
    history.write_text(
        '{"action":"remove","distribution":"heinzel_unforeseen"}\n'
        'malformed\n[]\n{"action":"disable","distribution":"heinzel-unforeseen"}'
    )
    assert install.excluded_plugins(history) == {"heinzel-unforeseen"}
    with history.open("a") as handle:
        handle.write('\n{"action":"add","distribution":"heinzel-unforeseen"}')
    assert install.excluded_plugins(history) == set()


def test_dry_run_never_installs_or_downloads_wheels(tmp_path, monkeypatch, capsys):
    file = profile_file(tmp_path)
    calls = []
    monkeypatch.setattr(install, "prepare", lambda *args: calls.append(args))
    assert install.main(["--profile", str(file), "--dry-run"]) == 0
    assert "heinzel-unforeseen 0.9.0" in capsys.readouterr().out
    assert calls == []


def test_candidate_profile_environment_and_explicit_precedence(tmp_path, monkeypatch, capsys):
    file = profile_file(tmp_path)
    monkeypatch.setenv("HZ_APP_PROFILE", str(file))
    assert install.main(["--dry-run"]) == 0
    assert "heinzel-unforeseen" in capsys.readouterr().out
    monkeypatch.setenv("HZ_APP_PROFILE", str(tmp_path / "absent.json"))
    assert install.main(["--profile", str(file), "--dry-run"]) == 0


def test_verified_profile_uses_one_command_for_the_chosen_environment(tmp_path, monkeypatch):
    file = profile_file(tmp_path)
    calls = []
    monkeypatch.setattr(install, "_uv", lambda: "/trusted/bin/uv")
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )
    assert install.main(["--profile", str(file), "--python", "/isolated/bin/python"]) == 0
    [command] = calls
    assert command[:5] == ["/trusted/bin/uv", "pip", "install", "--python", "/isolated/bin/python"]
    assert len(command[5:]) == 2
    assert all(Path(path).is_file() for path in command[5:])


@pytest.mark.parametrize(
    "source",
    [
        "http://example.com/file",
        "https://user:secret@example.com/file",
        "file:///tmp/file",
        "https://example.com/file#fragment",
    ],
)
def test_unsupported_artifact_sources_are_rejected(source):
    with pytest.raises(ValueError, match="HTTPS"):
        install._read(source, 10)


def test_standard_receipt_preserves_ordinary_index_updates(tmp_path, monkeypatch):
    from heinzel import Progress
    from heinzel.kernel import Installation, ReleaseSource
    from heinzel.modules.system._update import UpdateService

    file = profile_file(tmp_path)
    commands = []
    monkeypatch.setattr(install, "_uv", lambda: "/trusted/bin/uv")
    monkeypatch.setattr(install, "existing_requirements", list)

    def installed(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(install.subprocess, "run", installed)
    assert install.main(["--profile", str(file)]) == 0
    [command] = commands
    assert command[4] == "heinzel"
    assert command[-2:] == ["--with", "heinzel-unforeseen"]
    overrides = Path(command[command.index("--override") + 1])
    assert "heinzel @ file:" in overrides.read_text()
    assert "app-artifacts" in str(overrides)
    assert "cache" not in overrides.parts
    # uv records index requirements and wheel overrides alongside the installed entrypoint.
    prefix = tmp_path / "tools" / "heinzel"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "hz").write_text("fixture", encoding="utf-8")
    base = tmp_path / "base-python"
    assert base.parent.resolve() == tmp_path.resolve()
    assert not base.exists()
    assert not base.is_symlink()
    base.write_text("#!/bin/sh\nexit 0\n")
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
