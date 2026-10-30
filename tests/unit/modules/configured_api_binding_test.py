"""Real configured resolver with fake local environment values; no network or model calls."""

from dataclasses import replace

import pytest

from heinzel import CallIdentity, Config, Credentials, CurrentUser, HeinzelError, Principal, Settings
from heinzel.kernel._credentials import EnvironmentBackend, environment_variable
from heinzel.modules.ai.access._configured import ConfiguredApiBinding
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess
from heinzel.modules.heinzel._priced_model import MODEL

pytestmark = pytest.mark.unit


def service(selected, settings):
    return PydanticAiAccess(
        settings[0],
        Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        installed=lambda _: True,
        binding=lambda: selected[0],
        current_settings=lambda: settings[0],
    )


def test_real_resolver_key_rotation_selection_and_restart_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret-one")
    monkeypatch.delenv(environment_variable("ai.access", "api-key"), raising=False)
    monkeypatch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
    current = CurrentUser(tmp_path / "identity")
    selected = ["pydantic-ai"]
    settings = [AccessSettings(model=MODEL)]
    binding = ConfiguredApiBinding(current, service(selected, settings))
    with current.local("cli"):
        snapshot = binding.capture()
        assert snapshot.owner == current.principal().id
        assert snapshot.access == "pydantic-ai"
        assert snapshot.model == MODEL
        assert snapshot.endpoint == "https://api.openai.com/v1"
        assert len(snapshot.credential_fingerprint) == 64
        assert "fixture-secret" not in repr(snapshot)
        binding.recheck(snapshot)
        assert ConfiguredApiBinding(current, service(selected, settings)).capture() == snapshot
        assert (current.directory / "api-access-fingerprint-v1").stat().st_mode & 0o777 == 0o600
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret-two")
        with pytest.raises(HeinzelError):
            binding.recheck(snapshot)
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret-one")
        monkeypatch.setenv(environment_variable("ai.access", "api-key"), "fixture-secret-one")
        with pytest.raises(HeinzelError):
            binding.recheck(snapshot)  # source changed even if the key did not
        monkeypatch.delenv(environment_variable("ai.access", "api-key"))
        selected[0] = "claude-code"
        with pytest.raises(HeinzelError):
            binding.recheck(snapshot)
        selected[0] = "pydantic-ai"
        settings[0] = AccessSettings(model=MODEL, base_url="http://localhost:11434/v1")
        with pytest.raises(HeinzelError):
            binding.recheck(snapshot)
        settings[0] = AccessSettings(model=MODEL)
        monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "unsafe")
        with pytest.raises(HeinzelError):
            binding.recheck(snapshot)
        monkeypatch.delenv("OPENAI_CUSTOM_HEADERS")
        stranger = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset())
        with current.using(stranger), pytest.raises(HeinzelError):
            binding.capture()
        with pytest.raises(HeinzelError):
            binding.recheck(replace(snapshot, owner="u-" + "a" * 32))


def test_selected_claude_never_reads_api_key_or_creates_fingerprint_key(tmp_path, monkeypatch):
    current = CurrentUser(tmp_path / "identity")
    selected = ["claude-code"]
    settings = [AccessSettings(model=MODEL)]
    access = service(selected, settings)
    monkeypatch.setattr(access, "_key", lambda: pytest.fail("must not read the API credential"))
    with current.local("cli"), pytest.raises(HeinzelError):
        ConfiguredApiBinding(current, access).capture()
    assert not (current.directory / "api-access-fingerprint-v1").exists()


def test_key_symlink_or_damage_is_never_followed_or_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret")
    monkeypatch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
    current = CurrentUser(tmp_path / "identity")
    selected = ["pydantic-ai"]
    settings = [AccessSettings(model=MODEL)]
    with current.local("cli"):
        target = tmp_path / "target"
        target.write_bytes(b"private")
        path = current.directory / "api-access-fingerprint-v1"
        path.symlink_to(target)
        with pytest.raises(HeinzelError):
            ConfiguredApiBinding(current, service(selected, settings)).capture()
        assert target.read_bytes() == b"private"
        assert path.is_symlink()
        path.unlink()
        path.write_bytes(b"invalid")
        path.chmod(0o600)
        with pytest.raises(HeinzelError):
            ConfiguredApiBinding(current, service(selected, settings)).capture()
        assert path.read_bytes() == b"invalid"


def test_lease_builds_once_with_exact_captured_credential_even_if_store_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-original")
    monkeypatch.delenv(environment_variable("ai.access", "api-key"), raising=False)
    monkeypatch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
    current = CurrentUser(tmp_path / "identity")
    selected = ["pydantic-ai"]
    settings = [AccessSettings(model=MODEL)]
    access = service(selected, settings)
    read = access._key
    reads = []

    def read_once():
        reads.append(1)
        return read()

    monkeypatch.setattr(access, "_key", read_once)
    built = []
    binding = ConfiguredApiBinding(current, access, build=lambda key: built.append(key) or object())
    with current.local("cli"):
        lease = binding.lease()
        approved = lease.snapshot
        assert reads == [1]
        assert "fixture-original" not in repr(lease)
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-different")
        lease.build_approved(approved)
        assert reads == [1]
        assert built == ["fixture-original"]
        with pytest.raises(HeinzelError):
            lease.build_approved(approved)
        other = binding.lease()
        with pytest.raises(HeinzelError):
            other.build_approved(approved)
        denied = binding.lease()
        selected[0] = "claude-code"
        with pytest.raises(HeinzelError):
            denied.build_approved(denied.snapshot)
        assert built == ["fixture-original"]
