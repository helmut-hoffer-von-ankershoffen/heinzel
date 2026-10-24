"""`hz system credential` through the CLI head, and the promise that no output ever carries a value (ADR 0030)."""

import json
import logging

import pytest
from typer.testing import CliRunner

from heinzel import CredentialLet, Credentials, Kernel, Module
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.integration

SENTINEL = "sentinel-value-that-must-never-be-printed-9f3c"


def example_module() -> Module:
    module = Module("example", "Declares credentials for these tests.")
    module.credential("api-token", purpose="Example API token, from example.invalid/settings.")
    return module


@pytest.fixture
def kernel(make_kernel) -> Kernel:
    return make_kernel(builtins=True, example=example_module())


@pytest.fixture
def memory(kernel) -> MemoryBackend:
    backend = MemoryBackend()
    kernel.bind(Credentials, Credentials(backend, [(r.qualname, let) for r, let in kernel.lets(CredentialLet)]))
    return backend


def hz(kernel: Kernel, *args: str, stdin: str | None = None) -> tuple[int, str]:
    result = CliRunner().invoke(build(kernel), list(args), input=stdin)
    return result.exit_code, result.output


def test_set_reads_the_value_from_standard_input_and_list_shows_presence(kernel, memory):
    code, output = hz(kernel, "system", "credential", "set", "api-token", "--json", stdin=f"{SENTINEL}\n")
    assert code == 0, output
    assert json.loads(output)["present"] is True
    assert memory.values == {("example", "api-token"): SENTINEL}
    code, output = hz(kernel, "system", "credential", "list", "--json")
    assert json.loads(output) == [
        {
            "name": "api-token",
            "service": "example",
            "purpose": "Example API token, from example.invalid/settings.",
            "module": "example",
            "backend": "memory",
            "present": True,
        }
    ]


def test_remove_deletes_the_value(kernel, memory):
    memory.set("example", "api-token", SENTINEL)
    code, output = hz(kernel, "system", "credential", "remove", "api-token", "--service", "example", "--json")
    assert code == 0, output
    assert json.loads(output)["present"] is False
    assert memory.values == {}


def test_set_refuses_undeclared_credentials_and_empty_values(kernel, memory):
    code, output = hz(kernel, "system", "credential", "set", "nope", stdin=SENTINEL)
    assert code == 1
    assert "No module declares the credential nope" in output
    code, output = hz(kernel, "system", "credential", "set", "api-token", stdin="\n")
    assert code == 1
    assert "empty" in output
    assert memory.values == {}


def test_doctor_reports_missing_and_stored_credentials(kernel, memory):
    code, output = hz(kernel, "system", "doctor", "--json")
    checks = {c["name"]: c for c in json.loads(output)["checks"]["system.credential"]}
    assert code == 0
    assert checks["credential api-token"]["status"] == "degraded"
    assert checks["credential api-token"]["fix"] == "hz system credential set api-token --service example"
    memory.set("example", "api-token", SENTINEL)
    code, output = hz(kernel, "system", "doctor", "--json")
    checks = {c["name"]: c for c in json.loads(output)["checks"]["system.credential"]}
    assert checks["credential api-token"]["status"] == "up"


def test_without_a_keychain_credentials_come_from_the_environment(kernel, monkeypatch):
    """The test environment forces the environment backend, as on Linux without Secret Service."""
    code, output = hz(kernel, "system", "credential", "list", "--json")
    assert json.loads(output)[0] | {"purpose": ""} == {
        "name": "api-token",
        "service": "example",
        "purpose": "",
        "module": "example",
        "backend": "environment",
        "present": False,
    }
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", SENTINEL)
    assert json.loads(hz(kernel, "system", "credential", "list", "--json")[1])[0]["present"] is True
    code, output = hz(kernel, "system", "credential", "set", "api-token", stdin=SENTINEL)
    assert code == 1
    assert "export HZ_CREDENTIAL_EXAMPLE_API_TOKEN" in output
    checks = json.loads(hz(kernel, "system", "doctor", "--json")[1])["checks"]["system.credential"]
    assert checks[0]["name"] == "backend"
    assert checks[0]["status"] == "degraded"


def test_set_is_only_on_the_cli_so_no_payload_carries_a_value(kernel):
    for head in ("mcp", "api"):
        names = {item.name for item in exposed(kernel, head)}
        assert {"system_credential_list", "system_credential_remove"} <= names
        assert "system_credential_set" not in names


def test_no_operation_output_or_log_contains_a_value(kernel, memory, caplog):
    caplog.set_level(logging.DEBUG)
    outputs = [hz(kernel, "system", "credential", "set", "api-token", "--json", stdin=SENTINEL)[1]]
    assert memory.values == {("example", "api-token"): SENTINEL}, "the value must really be stored for this to count"
    commands = [
        ("system", "credential", "list", "--json"),
        ("system", "credential", "list"),
        ("system", "doctor", "--json"),
        ("system", "info", "--json"),
        ("system", "modules", "--json"),
        ("system", "credential", "remove", "api-token", "--json"),
    ]
    outputs += [hz(kernel, *args)[1] for args in commands]
    leaks = [output for output in outputs if SENTINEL in output]
    assert leaks == []
    assert SENTINEL not in caplog.text


def test_the_hz_credentials_fixture_keeps_credentials_in_memory(hz_credentials, hz):
    checks = hz("system", "doctor").json["checks"]["system.credential"]
    assert checks[0] == {"name": "backend", "status": "up", "detail": "memory", "fix": ""}
