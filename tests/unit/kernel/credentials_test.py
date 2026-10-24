import pytest

from heinzel import CredentialLet, Credentials, HeinzelError, Module, Status
from heinzel.kernel import EnvironmentBackend, environment_variable, subprocess_environment
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.unit

SENTINEL = "not-a-real-token-1234"


def declare() -> tuple[Credentials, CredentialLet, CredentialLet]:
    example, other = Module("example", "x"), Module("other", "x")
    token = example.credential("api-token", purpose="Example API token.")
    shared = other.credential("api-token", purpose="Shared token.", service="shared")
    declared = [("example", token), ("other.sub", shared)]
    return Credentials(MemoryBackend(), declared), token, shared


def test_service_defaults_to_the_qualified_module_name():
    credentials, _, _ = declare()
    assert [(d.service, d.module, d.label) for d in credentials.declared] == [
        ("example", "example", "example/api-token"),
        ("shared", "other.sub", "shared/api-token"),
    ]


def test_set_get_has_require_and_delete_take_the_declared_let():
    credentials, token, shared = declare()
    assert credentials.get(token) is None
    assert not credentials.has(token)
    credentials.set(token, SENTINEL)
    assert credentials.get(token) == SENTINEL
    assert credentials.require(token) == SENTINEL
    assert not credentials.has(shared)
    assert credentials.delete(token)
    assert not credentials.delete(token)
    assert not credentials.has(token)


def test_a_let_nobody_registered_is_refused_even_with_the_same_fields():
    credentials, _, _ = declare()
    lookalike = CredentialLet(name="api-token", purpose="Example API token.")
    with pytest.raises(HeinzelError, match="not declared by a registered module"):
        credentials.get(lookalike)


def test_require_says_how_to_store_a_missing_credential():
    credentials, token, _ = declare()
    with pytest.raises(HeinzelError, match=r"`hz system credential set api-token --service example`"):
        credentials.require(token)


def test_empty_values_are_not_stored():
    credentials, token, _ = declare()
    with pytest.raises(HeinzelError, match="empty"):
        credentials.set(token, "  \n")
    assert not credentials.has(token)


class EchoingBackend(MemoryBackend):
    """A careless backend whose errors repeat the value."""

    def set(self, service: str, name: str, value: str) -> None:
        msg = f"cannot store {value!r}"
        raise RuntimeError(msg)


def test_backend_errors_are_redacted_and_unchained():
    token = Module("example", "x").credential("api-token", purpose="x")
    credentials = Credentials(EchoingBackend(), [("example", token)])
    with pytest.raises(HeinzelError, match=r"RuntimeError: cannot store '\*\*\*'") as raised:
        credentials.set(token, SENTINEL)
    assert SENTINEL not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_backend_heinzel_errors_pass_through():
    token = Module("example", "x").credential("api-token", purpose="x")
    with pytest.raises(HeinzelError, match="export HZ_CREDENTIAL_EXAMPLE_API_TOKEN"):
        Credentials(EnvironmentBackend(), [("example", token)]).set(token, SENTINEL)


def test_attention_is_a_degraded_check_with_the_fix():
    credentials, token, _ = declare()
    check = credentials.attention(token, "the example service rejected the token: 401")
    assert check.name == "credential example/api-token"
    assert check.status is Status.DEGRADED
    assert check.fix == "hz system credential set api-token --service example"


def test_environment_variable_names_are_upper_case_with_underscores():
    assert environment_variable("system.credential", "api-token") == "HZ_CREDENTIAL_SYSTEM_CREDENTIAL_API_TOKEN"


def test_environment_backend_reads_variables(monkeypatch):
    backend = EnvironmentBackend()
    assert backend.get("example", "api-token") is None
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", SENTINEL)
    assert backend.get("example", "api-token") == SENTINEL
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", "")
    assert backend.get("example", "api-token") is None


def test_environment_backend_explains_what_to_export_without_the_value():
    backend = EnvironmentBackend()
    with pytest.raises(HeinzelError, match="export HZ_CREDENTIAL_EXAMPLE_API_TOKEN") as raised:
        backend.set("example", "api-token", SENTINEL)
    assert SENTINEL not in str(raised.value)
    with pytest.raises(HeinzelError, match="unset HZ_CREDENTIAL_EXAMPLE_API_TOKEN"):
        backend.delete("example", "api-token")


def test_subprocesses_get_the_environment_without_credentials(monkeypatch):
    assert subprocess_environment({"PATH": "/bin", "HZ_CREDENTIAL_EXAMPLE_API_TOKEN": SENTINEL}) == {"PATH": "/bin"}
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", SENTINEL)
    monkeypatch.setenv("HZ_LOG_LEVEL", "INFO")
    environment = subprocess_environment()
    assert "HZ_CREDENTIAL_EXAMPLE_API_TOKEN" not in environment
    assert environment["HZ_LOG_LEVEL"] == "INFO"


def test_memory_backend_reports_whether_it_deleted_something():
    backend = MemoryBackend()
    backend.set("example", "api-token", SENTINEL)
    assert backend.delete("example", "api-token")
    assert not backend.delete("example", "api-token")


class FailingBackend(MemoryBackend):
    """A backend whose reads and deletes fail, with or without a message meant for people."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def get(self, service: str, name: str) -> str | None:
        raise self.error

    def delete(self, service: str, name: str) -> bool:
        raise self.error


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (HeinzelError("Unlock the keychain."), "Unlock the keychain."),
        (RuntimeError(f"holding {SENTINEL}"), "Could not (read|remove) example/api-token from memory: RuntimeError$"),
    ],
)
def test_read_and_delete_failures_say_what_failed_without_the_backends_text(error, message):
    token = Module("example", "x").credential("api-token", purpose="x")
    credentials = Credentials(FailingBackend(error), [("example", token)])
    for action in (credentials.get, credentials.require, credentials.delete):
        with pytest.raises(HeinzelError, match=message) as raised:
            action(token)
        assert SENTINEL not in str(raised.value)
