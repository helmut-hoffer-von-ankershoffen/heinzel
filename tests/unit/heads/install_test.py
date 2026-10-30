"""A standard composition is verified before one isolated installation; minimal core stays independent."""

import hashlib
import json
import subprocess
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


def test_standard_receipt_preserves_ordinary_index_updates(tmp_path, monkeypatch):
    from heinzel import Progress
    from heinzel.kernel import Installation, ReleaseSource
    from heinzel.modules.system._update import UpdateService

    file = profile_file(tmp_path)
    commands = []
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/bin/uv")
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
    receipt = overrides.parent.parent / "standard-app.json"
    assert install.read_profile(str(receipt)).core_version == "0.9.0"
    assert receipt.stat().st_mode & 0o777 == 0o600
    # This is uv's observed receipt shape for unbounded requested names plus installation-only wheel overrides.
    (tmp_path / "uv-receipt.toml").write_text(
        '[tool]\nrequirements=[{name="heinzel"}]\noverrides=[{name="heinzel", path="verified.whl"}]\n'
    )
    target = Installation(
        Progress(),
        prefix=tmp_path,
        uv="uv",
        runner=lambda args, environment=None: subprocess.CompletedProcess(args, 0, "", ""),
    )
    assert target.core_pin().kind == "index"
    assert target.core_pin().requirement == "heinzel"
    monkeypatch.setattr(target, "hz", lambda *args: "hz 0.9.1" if args == ("--version",) else "[]")

    class Releases(ReleaseSource):
        def highest_version(self, timeout, *, prereleases=False):
            return "0.9.1"

    result = UpdateService(target, Releases()).update(current="0.9.0")
    assert result.updated
    assert result.after == "0.9.1"
