"""A standard composition is verified before one isolated installation; minimal core stays independent."""

import hashlib
import io
import json
import subprocess
from contextlib import nullcontext
from http.client import HTTPMessage
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from heinzel import HeinzelError
from heinzel.heads import install

pytestmark = pytest.mark.unit


@pytest.fixture
def latest_release(tmp_path, monkeypatch):
    profile = json.loads(profile_file(tmp_path).read_text())
    for artifact in profile["artifacts"]:
        artifact["url"] = f"{install.PROFILE_ROOT}/app-v0.9.0/{artifact['filename']}"
    calls = []

    class Opener:
        def open(self, request, **_kwargs):
            calls.append((request.full_url, request.get_method()))
            return nullcontext(SimpleNamespace(url=install.PROFILE_TAG + "0.9.0"))

    def read(source, _limit):
        calls.append((source, "GET"))
        assert source == f"{install.PROFILE_ROOT}/app-v0.9.0/standard.json"
        return json.dumps(profile).encode()

    monkeypatch.setattr(install, "build_opener", lambda *_args: Opener())
    monkeypatch.setattr(install, "_read", read)
    return profile, calls


def test_latest_app_is_independent_of_bootstrap_sdk_and_uses_no_rest_api(latest_release):
    _profile, calls = latest_release
    assert install.latest_profile().core_version == "0.9.0"
    assert calls == [
        (install.PROFILE_LATEST, "HEAD"),
        (f"{install.PROFILE_ROOT}/app-v0.9.0/standard.json", "GET"),
    ]


@pytest.mark.parametrize("suffix", ["0.9.1", "0.9", "0.9.0rc1", "0.9.0.dev1", "../other", "0.9.0?token=secret"])
def test_noncanonical_or_mismatched_app_identity_is_rejected(latest_release, monkeypatch, suffix):
    if suffix == "0.9.1":
        monkeypatch.setattr(install, "_app_release_version", lambda _source: install.Version(suffix))
        monkeypatch.setattr(
            install, "read_profile", lambda _source: install.AppProfile.model_validate(latest_release[0])
        )
        with pytest.raises(HeinzelError, match="does not match"):
            install.latest_profile()
    elif suffix == "0.9":
        assert install._app_release_version(install.PROFILE_TAG + suffix) == install.Version("0.9")
    else:
        with pytest.raises(HeinzelError):
            install._app_release_version(install.PROFILE_TAG + suffix)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/repo/releases/tag/app-v0.9.0",
        "https://github.com.evil.invalid/owner/repo/releases/tag/app-v0.9.0",
        "https://github.com/other/repo/releases/tag/app-v0.9.0",
        "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/tag/plugin/v0.9.0",
        install.PROFILE_TAG + "0.9.0#fragment",
    ],
)
def test_latest_redirect_refuses_other_authorities_before_following(url):
    handler = install._AppRedirects()
    with pytest.raises(HeinzelError):
        handler.redirect_request(
            Request(install.PROFILE_LATEST, method="HEAD"),  # noqa: S310 - inert request, no network
            io.BytesIO(),
            302,
            "",
            HTTPMessage(),
            url,
        )


def test_expected_redirects_keep_method_and_drop_all_headers():
    handler = install._AppRedirects()
    initial = Request(install.PROFILE_LATEST, headers={"Authorization": "must-not-forward"}, method="HEAD")  # noqa: S310 - inert test request, no network
    redirected = handler.redirect_request(initial, io.BytesIO(), 302, "", HTTPMessage(), install.PROFILE_TAG + "0.9.0")
    assert redirected.get_method() == "HEAD"
    assert not redirected.headers
    asset = "https://release-assets.githubusercontent.com/github-production-release-asset/1/id?sig=private"
    redirected = handler.redirect_request(Request(install.PROFILE_ROOT), io.BytesIO(), 302, "", HTTPMessage(), asset)  # noqa: S310 - inert test request, no network
    assert redirected.full_url == asset
    assert not redirected.headers


@pytest.mark.parametrize(
    "asset",
    [
        "https://evil.invalid/asset?sig=private",
        "http://release-assets.githubusercontent.com/github-production-release-asset/1/id",
        "https://release-assets.githubusercontent.com:443/github-production-release-asset/1/id",
        "https://user:private@release-assets.githubusercontent.com/github-production-release-asset/1/id",
        "https://release-assets.githubusercontent.com/other/id",
    ],
)
def test_artifact_redirect_refuses_unexpected_origin_without_echoing_urls(asset):
    request = Request(install.PROFILE_ROOT)  # noqa: S310 - inert test request, no network
    with pytest.raises(HeinzelError, match="unexpected download authority") as failure:
        install._AppRedirects().redirect_request(request, io.BytesIO(), 302, "", HTTPMessage(), asset)
    assert "private" not in str(failure.value)


def test_default_command_discovers_an_app_without_a_version_argument(latest_release, monkeypatch, capsys):
    monkeypatch.delenv("HZ_APP_PROFILE", raising=False)
    assert install.main(["--dry-run"]) == 0
    assert "heinzel-unforeseen" in capsys.readouterr().out


def test_latest_legacy_profile_is_refused_before_any_install(latest_release, monkeypatch, capsys):
    latest_release[0]["format"] = 1
    monkeypatch.setattr(install, "prepare", lambda *_args: pytest.fail("No package may be installed"))
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


def test_default_profile_cannot_redirect_a_package_to_another_release(latest_release):
    latest_release[0]["artifacts"][0]["url"] = "https://example.invalid/other.whl"
    with pytest.raises(HeinzelError, match="outside its immutable standard app release"):
        install.latest_profile()


@pytest.mark.parametrize("status", [403, 404, 503])
def test_discovery_http_error_names_phase_status_and_never_echoes_url(latest_release, monkeypatch, capsys, status):
    class Opener:
        def open(self, *_args, **_kwargs):
            url = "https://example.invalid/?token=secret"
            raise HTTPError(url, status, "secret", {"X-RateLimit-Remaining": "0"}, None)

    monkeypatch.setattr(install, "build_opener", lambda *_args: Opener())
    assert install.main([]) == 1
    message = capsys.readouterr().err
    assert f"App profile discovery failed (HTTP {status})" in message
    assert "secret" not in message
    assert "example.invalid" not in message


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


@pytest.mark.parametrize("problem", ["http", "transport", "hash"])
def test_package_failure_names_its_phase_without_leaking_sources(tmp_path, monkeypatch, capsys, problem):
    profile = profile_file(tmp_path)
    original = install._read

    def read(source, limit):
        if str(source) == str(profile):
            return original(source, limit)
        if problem == "http":
            url = "https://example.invalid/?sig=secret"
            raise HTTPError(url, 404, "secret", {}, None)
        if problem == "transport":
            reason = "https://example.invalid/?sig=secret"
            raise OSError(reason)
        return b"corrupted wheel"

    monkeypatch.setattr(install, "_read", read)
    assert install.main(["--profile", str(profile)]) == 1
    message = capsys.readouterr().err
    assert ("package verification failed" if problem == "hash" else "package download failed") in message
    assert "secret" not in message
    assert "example.invalid" not in message
