import pytest

from heinzel import CredentialLet, Credentials, HeinzelError, Module, Status
from heinzel.kernel import EnvironmentBackend, environment_variable
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
    credentials, token, shared = declare()
    assert credentials.lookup(token).service == "example"
    assert credentials.lookup(shared).service == "shared"
    assert credentials.lookup(shared).module == "other.sub"


def test_set_get_has_and_delete_by_let_or_by_name_and_service():
    credentials, token, _ = declare()
    assert credentials.get(token) is None
    assert not credentials.has(token)
    credentials.set(token, SENTINEL)
    assert credentials.get("api-token", service="example") == SENTINEL
    assert credentials.require(token) == SENTINEL
    credentials.delete("api-token", service="example")
    assert not credentials.has(token)


def test_a_name_declared_by_several_services_needs_the_service():
    credentials, _, _ = declare()
    with pytest.raises(HeinzelError, match="Several services declare the credential api-token: example, shared"):
        credentials.get("api-token")


@pytest.mark.parametrize(
    ("credential", "service"),
    [("missing", None), ("api-token", "nobody"), (CredentialLet(name="api-token", purpose="undeclared"), None)],
)
def test_undeclared_credentials_are_refused(credential, service):
    credentials, _, _ = declare()
    with pytest.raises(HeinzelError, match="No module declares the credential"):
        credentials.get(credential, service)


def test_require_says_how_to_store_a_missing_credential():
    credentials, token, _ = declare()
    with pytest.raises(HeinzelError, match=r"`hz system credential set api-token --service example`"):
        credentials.require(token)


def test_empty_values_are_not_stored():
    credentials, token, _ = declare()
    with pytest.raises(HeinzelError, match="empty"):
        credentials.set(token, "  \n")
    assert not credentials.has(token)


def test_attention_is_a_degraded_check_with_the_fix():
    credentials, token, _ = declare()
    check = credentials.attention(token, "the example service rejected the token: 401")
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


def test_memory_backend_reports_whether_it_deleted_something():
    backend = MemoryBackend()
    backend.set("example", "api-token", SENTINEL)
    assert backend.delete("example", "api-token")
    assert not backend.delete("example", "api-token")
