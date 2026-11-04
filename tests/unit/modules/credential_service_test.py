import io

import keyring.core
import pytest
from keyring.backends import chainer, fail, null
from keyring.errors import KeyringError, PasswordDeleteError

from heinzel import Credentials, HeinzelError, Module, Status
from heinzel.kernel import EnvironmentBackend
from heinzel.modules.system.credential import _service
from heinzel.modules.system.credential._service import (
    CredentialService,
    KeyringBackend,
    is_os_keychain,
    read_secret,
    select_backend,
)
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit

SENTINEL = "not-a-real-token-5678"


class FakeKeychain:
    """A keyring backend in memory that can be told to fail; it is not an OS keychain."""

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
        if (service, username) not in self.values:
            msg = "not found"
            raise PasswordDeleteError(msg)
        del self.values[service, username]


class PlaintextKeyring(FakeKeychain):
    """Stands in for keyrings.alt's PlaintextKeyring: a backend that writes values to a file."""

    name = "plaintext"


PlaintextKeyring.__module__ = "keyrings.alt.file"


class MacKeychain(FakeKeychain):
    """Stands in for keyring's macOS backend, without touching the real keychain."""

    name = "macOS Keyring"


MacKeychain.__module__ = "keyring.backends.macOS"
MacKeychain.__qualname__ = "Keyring"


class Chain(FakeKeychain):
    """Stands in for keyring's ChainerBackend, whose real `backends` discovers the machine's keychains."""

    def __init__(self, *backends: object) -> None:
        super().__init__()
        self.backends = list(backends)


Chain.__module__ = chainer.__name__
Chain.__qualname__ = "ChainerBackend"


def chain(*backends) -> Chain:
    return Chain(*backends)


def test_keyring_backend_keeps_entries_under_a_heinzel_service():
    keychain = FakeKeychain()
    backend = KeyringBackend(keychain)
    backend.set("example", "api-token", SENTINEL)
    assert keychain.values == {("heinzel/example", "api-token"): SENTINEL}
    assert backend.get("example", "api-token") == SENTINEL
    assert backend.name == "keyring (fake keychain)"
    assert backend.delete("example", "api-token")
    assert not backend.delete("example", "api-token")


@pytest.mark.parametrize("error", [KeyringError(f"locked, {SENTINEL}"), RuntimeError(f"bug echoing {SENTINEL}")])
def test_keychain_errors_become_heinzel_errors_without_the_value(error):
    backend = KeyringBackend(FakeKeychain(error=error))
    with pytest.raises(HeinzelError, match="could not store the credential example/api-token") as raised:
        backend.set("example", "api-token", SENTINEL)
    assert SENTINEL not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    with pytest.raises(HeinzelError, match="could not read"):
        backend.get("example", "api-token")


def test_keychain_delete_errors_other_than_not_found_are_reported():
    keychain = FakeKeychain(delete_error=KeyringError("locked"))
    with pytest.raises(HeinzelError, match="could not remove"):
        KeyringBackend(keychain).delete("example", "api-token")


@pytest.mark.parametrize(
    ("keychain", "expected"),
    [
        (MacKeychain(), True),
        (PlaintextKeyring(), False),
        (FakeKeychain(), False),
        (fail.Keyring(), False),
        (null.Keyring(), False),
        (chain(MacKeychain(), MacKeychain()), True),
        (chain(MacKeychain(), PlaintextKeyring()), False),
        (chain(), False),
    ],
    ids=["macos", "plaintext", "unknown", "fail", "null", "chain-of-os", "chain-with-plaintext", "empty-chain"],
)
def test_only_os_keychains_count(keychain, expected):
    assert is_os_keychain(keychain) is expected


@pytest.mark.parametrize(
    ("choice", "keychain", "expected"),
    [
        ("auto", MacKeychain(), KeyringBackend),
        ("auto", PlaintextKeyring(), EnvironmentBackend),
        ("auto", fail.Keyring(), EnvironmentBackend),
        ("keyring", PlaintextKeyring(), KeyringBackend),
        ("environment", MacKeychain(), EnvironmentBackend),
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


def service_with(backend, *modules: tuple[str, Module]) -> CredentialService:
    if not modules:
        example = Module("example", "x")
        example.credential("api-token", purpose="Example API token.")
        modules = (("example", example),)
    declared = [(qualname, let) for qualname, module in modules for let in module.lets]
    return CredentialService(Credentials(backend, declared))


def test_lookup_by_name_and_service():
    shared, other = Module("a", "x"), Module("b", "x")
    shared.credential("api-token", purpose="x", service="shared")
    other.credential("api-token", purpose="x")
    service = service_with(MemoryBackend(), ("a", shared), ("b", other))
    assert service.lookup("api-token", "shared").module == "a"
    with pytest.raises(HeinzelError, match="Several services declare the credential api-token: b, shared"):
        service.lookup("api-token")
    with pytest.raises(HeinzelError, match="No module declares the credential nobody/api-token"):
        service.lookup("api-token", "nobody")


def test_checks_report_the_backend_and_missing_credentials():
    service = service_with(MemoryBackend())
    backend, credential = service.checks()
    assert (backend.status, backend.detail) == (Status.UP, "memory")
    assert (credential.name, credential.status) == ("credential example/api-token", Status.DEGRADED)
    assert credential.fix == "hz system credential set api-token --service example"
    service.set("api-token", None, read=lambda _: SENTINEL)
    assert service.checks()[1].status is Status.UP


def test_checks_name_a_shared_credential_once():
    first, second = Module("a", "x"), Module("b", "x")
    first.credential("api-token", purpose="x", service="shared")
    second.credential("api-token", purpose="x", service="shared")
    names = [check.name for check in service_with(MemoryBackend(), ("a", first), ("b", second)).checks()]
    assert names == ["backend", "credential shared/api-token"]


def test_checks_detect_colliding_variable_names():
    first, second = Module("foo", "x"), Module("foo-bar", "x")
    first.credential("token", purpose="x", service="foo.bar")
    second.credential("token", purpose="x")
    checks = service_with(MemoryBackend(), ("foo", first), ("foo-bar", second)).checks()
    collision = next(check for check in checks if check.name.startswith("variable"))
    assert collision.name == "variable HZ_CREDENTIAL_FOO_BAR_TOKEN"
    assert collision.status is Status.DEGRADED
    assert "foo-bar/token and foo.bar/token" in collision.detail


def test_checks_warn_about_the_environment_fallback():
    backend, _ = service_with(EnvironmentBackend()).checks()
    assert backend.status is Status.DEGRADED
    assert "HZ_CREDENTIAL_" in backend.detail


def test_checks_warn_about_a_forced_backend_that_is_not_an_os_keychain():
    backend, _ = service_with(KeyringBackend(PlaintextKeyring())).checks()
    assert backend.status is Status.DEGRADED
    assert "plaintext" in backend.detail
    assert service_with(KeyringBackend(MacKeychain())).checks()[0].status is Status.UP


def test_remove_reports_whether_there_was_a_value():
    service = service_with(MemoryBackend())
    service.set("api-token", "example", read=lambda _: SENTINEL)
    assert service.remove("api-token", None).removed
    assert not service.remove("api-token", None).removed


@pytest.mark.parametrize("backend", [MemoryBackend(), EnvironmentBackend(), KeyringBackend(MacKeychain())])
def test_unused_optional_credentials_do_not_degrade_health(backend):
    module = Module("example", "Optional provider authentication.")
    module.credential("token", purpose="Only used by the selected provider.", required=False)
    checks = service_with(backend, ("example", module)).checks()
    assert all(check.status is Status.UP for check in checks)
    assert "not stored" in checks[1].detail
    assert checks[1].fix == ""


@pytest.mark.parametrize("mandatory_first", [True, False])
def test_optional_shared_declaration_cannot_hide_a_mandatory_requirement(mandatory_first):
    required, optional = Module("a", "Required."), Module("b", "Optional.")
    required.credential("token", purpose="Required by an enabled module.", service="shared")
    optional.credential("token", purpose="Optional alternative.", service="shared", required=False)
    modules = [("a", required), ("b", optional)]
    if not mandatory_first:
        modules.reverse()
    checks = service_with(MemoryBackend(), *modules).checks()
    assert len(checks) == 2
    assert checks[1].status is Status.DEGRADED
    assert "Required by an enabled module" in checks[1].detail


def test_optional_credentials_still_report_unsafe_storage_and_backend_errors():
    module = Module("example", "Optional.")
    module.credential("token", purpose="Optional.", required=False)
    checks = service_with(KeyringBackend(PlaintextKeyring()), ("example", module)).checks()
    assert checks[0].status is Status.DEGRADED
    with pytest.raises(HeinzelError, match="could not read"):
        service_with(KeyringBackend(MacKeychain(error=KeyringError("locked"))), ("example", module)).checks()


def test_stored_optional_environment_credentials_keep_the_security_warning(monkeypatch):
    module = Module("example", "Optional.")
    module.credential("token", purpose="Optional.", required=False)
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_TOKEN", SENTINEL)
    checks = service_with(EnvironmentBackend(), ("example", module)).checks()
    assert checks[0].status is Status.DEGRADED
    assert checks[1].detail == "stored"


def test_optional_declarations_still_report_environment_name_collisions():
    first, second = Module("first", "Optional."), Module("second", "Optional.")
    first.credential("token", purpose="Optional.", service="foo.bar", required=False)
    second.credential("token", purpose="Optional.", service="foo-bar", required=False)
    checks = service_with(MemoryBackend(), ("first", first), ("second", second)).checks()
    assert next(check for check in checks if check.name.startswith("variable")).status is Status.DEGRADED
