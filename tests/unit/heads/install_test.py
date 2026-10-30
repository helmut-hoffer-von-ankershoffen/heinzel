"""A standard composition is verified before one isolated installation; minimal core stays independent."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

from heinzel.heads import install

pytestmark = pytest.mark.unit


def test_newest_complete_app_is_independent_of_bootstrap_sdk(tmp_path, monkeypatch):
    profile = json.loads(profile_file(tmp_path).read_text())
    for artifact in profile["artifacts"]:
        artifact["url"] = f"https://example.com/{artifact['filename']}"
    calls = []

    def read(source, _limit):
        calls.append(source)
        if source == install.PROFILE_REFS:
            return json.dumps(
                [
                    {"ref": "refs/tags/app-v0.9.0"},
                    {"ref": "refs/tags/app-v0.9.1"},
                    {"ref": "refs/tags/app-v9.0.0rc1"},
                    {"ref": "refs/tags/unrelated-v99.0.0"},
                    {"ref": "refs/tags/app-v../invalid"},
                    {},
                ]
            ).encode()
        if source == f"{install.PROFILE_ROOT}/app-v0.9.1/standard.json":
            raise HTTPError(source, 404, "Not published yet", {}, None)
        if source.startswith(install.PROFILE_RELEASES):
            return b'{"draft":false,"prerelease":false}'
        assert source == f"{install.PROFILE_ROOT}/app-v0.9.0/standard.json"
        return json.dumps(profile).encode()

    monkeypatch.setattr(install, "_read", read)
    assert install.latest_profile().core_version == "0.9.0"
    assert len(calls) == 5


@pytest.mark.parametrize("problem", ["rate-limit", "wrong-core", "invalid-index"])
def test_discovery_does_not_hide_corruption_or_github_errors(tmp_path, monkeypatch, problem):
    profile = install.read_profile(str(profile_file(tmp_path)))

    def read(source, _limit):
        if problem == "invalid-index":
            return b"{}"
        if source == install.PROFILE_REFS:
            return b'[{"ref":"refs/tags/app-v0.9.1"}]'
        if problem == "wrong-core" and source.startswith(install.PROFILE_RELEASES):
            return b'{"draft":false,"prerelease":false}'
        raise HTTPError(source, 403, "Rate limited", {}, None)

    monkeypatch.setattr(install, "_read", read)
    if problem == "wrong-core":
        monkeypatch.setattr(install, "read_profile", lambda _source: profile)
    with pytest.raises((ValueError, TypeError, HTTPError), match=r"index|release|403"):
        install.latest_profile()


def test_default_command_discovers_an_app_without_a_version_argument(tmp_path, monkeypatch, capsys):
    profile = install.read_profile(str(profile_file(tmp_path)))
    monkeypatch.delenv("HZ_APP_PROFILE", raising=False)
    monkeypatch.setattr(install, "latest_profile", lambda: profile)
    assert install.main(["--dry-run"]) == 0
    assert "heinzel-unforeseen" in capsys.readouterr().out


def test_discovery_skips_legacy_lifecycles_and_still_selects_an_older_compatible_app(tmp_path, monkeypatch):
    compatible = json.loads(profile_file(tmp_path).read_text())
    legacy = json.loads(json.dumps(compatible))
    legacy.update(format=1, core_version="0.9.1")
    for artifact in legacy["artifacts"]:
        artifact["version"] = "0.9.1"
        artifact["filename"] = artifact["filename"].replace("0.9.0", "0.9.1")
    for profile in (legacy, compatible):
        for artifact in profile["artifacts"]:
            artifact["url"] = f"https://example.com/{artifact['filename']}"

    def read(source, _limit):
        if source == install.PROFILE_REFS:
            return b'[{"ref":"refs/tags/app-v0.9.1"},{"ref":"refs/tags/app-v0.9.0"}]'
        if source.startswith(install.PROFILE_RELEASES):
            return b'{"draft":false,"prerelease":false}'
        return json.dumps(legacy if "app-v0.9.1" in source else compatible).encode()

    monkeypatch.setattr(install, "_read", read)
    assert install.latest_profile().core_version == "0.9.0"


def test_legacy_only_discovery_cannot_install_an_app_with_the_old_update_cycle(tmp_path, monkeypatch, capsys):
    legacy = json.loads(profile_file(tmp_path).read_text())
    legacy["format"] = 1
    for artifact in legacy["artifacts"]:
        artifact["url"] = f"https://example.com/{artifact['filename']}"

    def read(source, _limit):
        if source == install.PROFILE_REFS:
            return b'[{"ref":"refs/tags/app-v0.9.0"}]'
        if source.startswith(install.PROFILE_RELEASES):
            return b'{"draft":false,"prerelease":false}'
        return json.dumps(legacy).encode()

    monkeypatch.setattr(install, "_read", read)
    monkeypatch.setattr(install, "prepare", lambda *_args: pytest.fail("Legacy profiles must not download packages"))
    assert install.main([]) == 1
    assert "compatible standard app release" in capsys.readouterr().err


def test_an_explicit_legacy_profile_is_refused_before_installation_even_for_a_dry_run(tmp_path, monkeypatch, capsys):
    file = profile_file(tmp_path)
    legacy = json.loads(file.read_text())
    legacy["format"] = 1
    file.write_text(json.dumps(legacy))
    monkeypatch.setattr(install, "prepare", lambda *_args: pytest.fail("No package may be installed"))
    assert install.main(["--profile", str(file), "--dry-run"]) == 1
    assert "legacy installation lifecycle" in capsys.readouterr().err


def test_a_published_manifest_with_a_missing_asset_fails_closed_instead_of_hiding_damage(tmp_path, monkeypatch, capsys):
    profile = json.loads(profile_file(tmp_path).read_text())
    profile["core_version"] = "0.9.1"
    for artifact in profile["artifacts"]:
        artifact["version"] = "0.9.1"
        artifact["filename"] = artifact["filename"].replace("0.9.0", "0.9.1")
        artifact["url"] = f"https://example.com/{artifact['filename']}"
    calls = []

    def read(source, _limit):
        calls.append(source)
        if source == install.PROFILE_REFS:
            return b'[{"ref":"refs/tags/app-v0.9.1"},{"ref":"refs/tags/app-v0.9.0"}]'
        if source.startswith(install.PROFILE_RELEASES):
            return b'{"draft":false,"prerelease":false}'
        if source.endswith("app-v0.9.1/standard.json"):
            return json.dumps(profile).encode()
        error = HTTPError(source, 404, "Published wheel missing", {}, None)
        error.close()
        raise error

    monkeypatch.setattr(install, "_read", read)
    assert install.main([]) == 1
    assert "could not be verified" in capsys.readouterr().err
    assert not any("app-v0.9.0" in source for source in calls)


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_default_discovery_skips_unstable_release_state_even_with_a_stable_tag(tmp_path, monkeypatch, flag):
    profile = install.read_profile(str(profile_file(tmp_path)))

    def read(source, _limit):
        if source == install.PROFILE_REFS:
            return b'[{"ref":"refs/tags/app-v0.9.1"},{"ref":"refs/tags/app-v0.9.0"}]'
        return json.dumps(
            {"draft": False, "prerelease": False} | ({flag: True} if source.endswith("app-v0.9.1") else {})
        ).encode()

    calls = []
    monkeypatch.setattr(install, "_read", read)
    monkeypatch.setattr(install, "read_profile", lambda source: calls.append(source) or profile)
    assert install.latest_profile() == profile
    assert calls == [f"{install.PROFILE_ROOT}/app-v0.9.0/standard.json"]


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
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/bin/uv")
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


def test_standard_receipt_preserves_ordinary_index_updates(tmp_path, monkeypatch):  # noqa: PLR0915
    from contextlib import nullcontext

    from heinzel import Progress
    from heinzel.heads._tool_install import ToolTarget
    from heinzel.kernel import Installation, ReleaseSource
    from heinzel.modules.system._update import UpdateService

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
