"""`hz system credential` through every head, and the promise that no output ever carries a value (ADR 0030)."""

import json
import logging
import re

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel import CredentialLet, Credentials, Kernel, Module
from heinzel.heads import api, mcp
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


def bind(kernel: Kernel, backend) -> None:
    kernel.bind(Credentials, Credentials(backend, [(r.qualname, let) for r, let in kernel.lets(CredentialLet)]))


@pytest.fixture
def memory(kernel) -> MemoryBackend:
    backend = MemoryBackend()
    bind(kernel, backend)
    return backend


def example(output: str) -> list[dict[str, object]]:
    """The listed credentials of the test module; the built-in modules declare their own (`ai.access/api-key`)."""
    return [row for row in json.loads(output) if row["service"] == "example"]


def hz(kernel: Kernel, *args: str, stdin: str | None = None) -> tuple[int, str]:
    result = CliRunner().invoke(build(kernel), list(args), input=stdin)
    return result.exit_code, result.output


def test_set_reads_the_value_from_standard_input_and_list_shows_presence(kernel, memory):
    code, output = hz(kernel, "system", "credential", "set", "api-token", "--json", stdin=f"{SENTINEL}\n")
    assert code == 0, output
    assert json.loads(output)["present"] is True
    assert memory.values == {("example", "api-token"): SENTINEL}
    code, output = hz(kernel, "system", "credential", "list", "--json")
    assert example(output) == [
        {
            "name": "api-token",
            "service": "example",
            "purpose": "Example API token, from example.invalid/settings.",
            "module": "example",
            "backend": "memory",
            "present": True,
        }
    ]


def test_remove_deletes_the_value_and_says_whether_there_was_one(kernel, memory):
    memory.set("example", "api-token", SENTINEL)
    code, output = hz(kernel, "system", "credential", "remove", "api-token", "--service", "example", "--json")
    assert code == 0, output
    assert json.loads(output) | {"purpose": ""} == {
        "name": "api-token",
        "service": "example",
        "purpose": "",
        "module": "example",
        "backend": "memory",
        "present": False,
        "removed": True,
    }
    assert memory.values == {}
    assert json.loads(hz(kernel, "system", "credential", "remove", "api-token", "--json")[1])["removed"] is False


def test_set_refuses_undeclared_credentials_and_empty_values(kernel, memory):
    code, output = hz(kernel, "system", "credential", "set", "nope", stdin=SENTINEL)
    assert code == 1
    assert "No module declares the credential nope" in output
    code, output = hz(kernel, "system", "credential", "set", "api-token", stdin="\n")
    assert code == 1
    assert "empty" in output
    assert memory.values == {}


def test_doctor_reports_missing_and_stored_credentials(kernel, memory):
    def credential_check() -> dict[str, str]:
        checks = json.loads(hz(kernel, "system", "doctor", "--json")[1])["checks"]["system.credential"]
        return next(c for c in checks if c["name"] == "credential example/api-token")

    assert credential_check()["status"] == "degraded"
    assert credential_check()["fix"] == "hz system credential set api-token --service example"
    memory.set("example", "api-token", SENTINEL)
    assert credential_check()["status"] == "up"


def test_without_a_keychain_credentials_come_from_the_environment(kernel, monkeypatch):
    """The test environment forces the environment backend, as on Linux without Secret Service."""
    code, output = hz(kernel, "system", "credential", "list", "--json")
    assert (example(output)[0]["backend"], example(output)[0]["present"]) == ("environment", False)
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", SENTINEL)
    assert example(hz(kernel, "system", "credential", "list", "--json")[1])[0]["present"] is True
    code, output = hz(kernel, "system", "credential", "set", "api-token", stdin=SENTINEL)
    assert code == 1
    assert "export HZ_CREDENTIAL_EXAMPLE_API_TOKEN" in output
    checks = json.loads(hz(kernel, "system", "doctor", "--json")[1])["checks"]["system.credential"]
    assert (checks[0]["name"], checks[0]["status"]) == ("backend", "degraded")


def test_set_and_remove_are_only_on_the_cli_so_no_web_request_reaches_them(kernel):
    for head in ("mcp", "api"):
        names = {item.name for item in exposed(kernel, head)}
        assert "system_credential_list" in names
        assert not {"system_credential_set", "system_credential_remove"} & names


CLI_COMMANDS = [
    ("system", "credential", "list", "--json"),
    ("system", "credential", "list"),
    ("system", "doctor", "--json"),
    ("system", "info", "--json"),
    ("system", "info"),
    ("system", "modules", "--json"),
    ("system", "config", "show", "--json"),
    ("system", "config", "show"),
    ("system", "credential", "remove", "api-token", "--json"),
]


def mcp_outputs(kernel: Kernel) -> list[str]:
    outputs: list[str] = []

    async def scenario() -> None:
        async with Client(mcp.build(kernel)) as client:
            for tool in ("system_credential_list", "system_doctor", "system_info", "system_config_show"):
                result = await client.call_tool(tool, {})
                outputs.append(json.dumps(result.structured_content) + repr(result.content))

    anyio.run(scenario)
    return outputs


def rest_outputs(kernel: Kernel) -> list[str]:
    client = TestClient(api.build(kernel), base_url="http://localhost")
    return [client.get(path).text for path in ("/system/credential/list", "/system/doctor", "/system/info")]


@pytest.mark.parametrize("backend", ["memory", "environment"])
def test_no_output_of_any_head_or_log_contains_a_value(kernel, backend, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    outputs = []
    if backend == "memory":
        memory = MemoryBackend()
        bind(kernel, memory)
        outputs.append(hz(kernel, "system", "credential", "set", "api-token", "--json", stdin=SENTINEL)[1])
        assert memory.values == {("example", "api-token"): SENTINEL}, "the value must be stored for this to count"
    else:
        monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", SENTINEL)
        outputs.append(hz(kernel, "system", "credential", "set", "api-token", stdin=SENTINEL)[1])
    assert example(hz(kernel, "system", "credential", "list", "--json")[1])[0]["present"] is True
    outputs += mcp_outputs(kernel) + rest_outputs(kernel)
    outputs += [hz(kernel, *args)[1] for args in CLI_COMMANDS]
    assert [output for output in outputs if SENTINEL in output] == []
    assert SENTINEL not in caplog.text


class LeakyBackend(MemoryBackend):
    """A buggy backend whose errors carry the value: the CLI's "Unexpected error" path would print them."""

    def get(self, service: str, name: str) -> str | None:
        msg = f"broken while holding {SENTINEL}"
        raise RuntimeError(msg)

    def set(self, service: str, name: str, value: str) -> None:
        msg = f"refusing {value}"
        raise RuntimeError(msg)

    def delete(self, service: str, name: str) -> bool:
        raise RuntimeError(SENTINEL)


def test_a_backend_error_holding_the_value_never_reaches_the_terminal(kernel):
    bind(kernel, LeakyBackend())
    for args, stdin in [
        (("system", "credential", "set", "api-token"), SENTINEL),
        (("system", "credential", "list"), None),
        (("system", "credential", "remove", "api-token"), None),
        (("system", "doctor"), None),
    ]:
        code, output = hz(kernel, *args, stdin=stdin)
        assert SENTINEL not in output, args
        assert "Unexpected error" not in output, args
    code, output = hz(kernel, "system", "credential", "list")
    assert code == 1
    assert re.search(r"Could not read [a-z.]+/[a-z-]+ from memory: RuntimeError", output)


def test_the_hz_credentials_fixture_keeps_credentials_in_memory(hz_credentials, hz):
    checks = hz("system", "doctor").json["checks"]["system.credential"]
    assert checks[0] == {"name": "backend", "status": "up", "detail": "memory", "fix": ""}
