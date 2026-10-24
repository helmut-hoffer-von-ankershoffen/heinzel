import io

import keyring.core
import pytest
from keyring.backends import fail, null
from keyring.errors import KeyringError, PasswordDeleteError

from heinzel import Credentials, HeinzelError, Module, Status
from heinzel.kernel import EnvironmentBackend
from heinzel.modules.system.credential import _service
from heinzel.modules.system.credential._service import (
    CredentialService,
    KeyringBackend,
    read_secret,
    select_backend,
    usable,
)
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit

SENTINEL = "not-a-real-token-5678"


class FakeKeychain:
    """A keyring backend in memory that can be told to fail."""

    name = "fake keychain"

    def __init__(self, error: Exception | None = None, delete_error: Exception | None = None) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.error = error
        self.delete_error = delete_error

    def get_password(self, service: str, username: str) -> str | None:
        if self.error:
            raise self.error
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.error:
            raise self.error
        self.values[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.delete_error:
            raise self.delete_error
        del self.values[service, username]


def test_keyring_backend_keeps_entries_under_a_heinzel_service():
    keychain = FakeKeychain()
    backend = KeyringBackend(keychain)
    backend.set("example", "api-token", SENTINEL)
    assert keychain.values == {("heinzel/example", "api-token"): SENTINEL}
    assert backend.get("example", "api-token") == SENTINEL
    assert backend.name == "keyring (fake keychain)"
    assert backend.delete("example", "api-token")
    assert not backend.delete("example", "api-token")


def test_keyring_errors_become_heinzel_errors_without_the_value():
    backend = KeyringBackend(FakeKeychain(error=KeyringError(f"locked while storing {SENTINEL}")))
    with pytest.raises(HeinzelError, match="could not store the credential api-token of example") as raised:
        backend.set("example", "api-token", SENTINEL)
    assert SENTINEL not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    with pytest.raises(HeinzelError, match="could not read"):
        backend.get("example", "api-token")


def test_keyring_delete_errors():
    keychain = FakeKeychain(delete_error=PasswordDeleteError("gone"))
    keychain.values["heinzel/example", "api-token"] = SENTINEL
    assert not KeyringBackend(keychain).delete("example", "api-token")
    keychain.delete_error = KeyringError("locked")
    with pytest.raises(HeinzelError, match="could not remove"):
        KeyringBackend(keychain).delete("example", "api-token")


def test_fail_and_null_keyrings_are_not_usable():
    assert not usable(fail.Keyring())
    assert not usable(null.Keyring())
    assert usable(FakeKeychain())


@pytest.mark.parametrize(
    ("choice", "keychain", "expected"),
    [
        ("auto", FakeKeychain(), KeyringBackend),
        ("auto", fail.Keyring(), EnvironmentBackend),
        ("keyring", fail.Keyring(), KeyringBackend),
        ("environment", FakeKeychain(), EnvironmentBackend),
    ],
)
def test_backend_selection(choice, keychain, expected):
    assert isinstance(select_backend(choice, lambda: keychain), expected)


def test_the_default_keychain_is_keyrings_choice(monkeypatch):
    placeholder = fail.Keyring()
    monkeypatch.setattr(keyring.core, "_keyring_backend", placeholder)
    assert _service._default_keychain() is placeholder


def test_read_secret_reads_piped_standard_input(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{SENTINEL}\n"))
    assert read_secret("Value: ") == SENTINEL


def test_read_secret_prompts_without_echo_on_a_terminal(monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    prompts = []
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("getpass.getpass", lambda prompt: prompts.append(prompt) or SENTINEL)
    assert read_secret("Value: ") == SENTINEL
    assert prompts == ["Value: "]


def service_with(backend) -> CredentialService:
    module = Module("example", "x")
    token = module.credential("api-token", purpose="Example API token.")
    return CredentialService(Credentials(backend, [("example", token)]))


def test_checks_report_the_backend_and_missing_credentials():
    service = service_with(MemoryBackend())
    backend, credential = service.checks()
    assert (backend.status, backend.detail) == (Status.UP, "memory")
    assert credential.status is Status.DEGRADED
    assert credential.fix == "hz system credential set api-token --service example"
    service.set("api-token", None, read=lambda _: SENTINEL)
    assert service.checks()[1].status is Status.UP


def test_checks_warn_about_the_environment_fallback():
    backend, _ = service_with(EnvironmentBackend()).checks()
    assert backend.status is Status.DEGRADED
    assert "HZ_CREDENTIAL_" in backend.detail
