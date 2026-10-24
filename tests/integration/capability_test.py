"""Capabilities through every head: mode gating, `hz system capability list`, doctor and info (ADR 0028).

The adapters are test-only; core ships no vendor adapters.
"""

import json

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel import Config, Document, Documents, Kernel, Module, SectionReplacement
from heinzel.heads import api, mcp
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration


class Notes:
    """A documents adapter for tests, in memory."""

    def __init__(self) -> None:
        self.sections: dict[tuple[str, str], str] = {}

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=ref.title(), text="body")

    def replace_section(self, ref: str, marker: str, text: str) -> SectionReplacement:
        self.sections[ref, marker] = text
        return SectionReplacement(ref=ref, marker=marker, revision="2")


def notes() -> Module:
    module = Module("notes", "Test-only documents adapter.")
    module.provides("documents", modes={"read", "write"}, hosts=["notes.invalid"])(Notes)
    return module


def digest() -> Module:
    module = Module("digest", "Reads documents and publishes a digest.", requires=["documents:read"])

    @module.operation
    def title(documents: Documents) -> str:
        """Show the title of the weekly document."""
        return documents.read("weekly").title

    @module.operation
    def overwrite(documents: Documents) -> str:
        """Try to write with a read-only declaration."""
        return documents.replace_section("weekly", "summary", "sneaky").ref

    @module.operation(read_only=False, requires=["documents:write"])
    def publish(documents: Documents) -> str:
        """Replace the summary of the weekly document."""
        return documents.replace_section("weekly", "summary", "approved").revision or ""

    return module


@pytest.fixture
def kernel(make_kernel) -> Kernel:
    config = Config(files=(), data={"capabilities": {"documents": "notes"}})
    return make_kernel(builtins=True, notes=notes(), digest=digest(), config=config)


def hz(kernel: Kernel, *args: str) -> tuple[int, dict | list | str]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def test_capability_parameters_are_injected_not_exposed(kernel):
    for head in ("cli", "mcp", "api"):
        title = next(item for item in exposed(kernel, head) if item.name == "digest_title")
        assert title.parameters == ()


def test_a_read_declaration_reads_through_the_bound_adapter_in_every_head(kernel):
    assert hz(kernel, "digest", "title") == (0, "Weekly")
    assert TestClient(api.build(kernel)).get("/digest/title").json() == "Weekly"

    async def scenario() -> object:
        async with Client(mcp.build(kernel)) as client:
            return (await client.call_tool("digest_title", {})).structured_content

    assert anyio.run(scenario) == {"result": "Weekly"}


def test_a_write_with_a_read_declaration_fails_in_every_head(kernel):
    code, output = hz(kernel, "digest", "overwrite")
    assert code != 0
    assert "digest overwrite can't call documents.replace_section, which needs write" in str(output)
    response = TestClient(api.build(kernel)).get("/digest/overwrite")
    assert response.status_code >= 400
    assert "needs write" in response.text

    async def scenario() -> tuple[bool, str]:
        async with Client(mcp.build(kernel)) as client:
            result = await client.call_tool("digest_overwrite", {})
            return result.is_error, repr(result.content)

    is_error, content = anyio.run(scenario)
    assert is_error
    assert "needs write" in content


def test_a_publish_operation_that_declared_write_writes(kernel):
    assert hz(kernel, "digest", "publish") == (0, "2")


def test_capability_list_shows_bindings_adapters_and_the_matrix(kernel):
    code, report = hz(kernel, "system", "capability", "list")
    assert code == 0
    assert {c["name"]: c["bound"] for c in report["capabilities"]} == {
        "agent-runner": "claude-code",
        "code-host": None,
        "documents": "notes",
        "tracker": None,
    }
    assert [a for a in report["adapters"] if a["capability"] == "documents"] == [
        {
            "adapter": "notes",
            "capability": "documents",
            "modes": ["read", "write"],
            "hosts": ["notes.invalid"],
            "module": "notes",
        }
    ]
    assert [(u["requester"], u["requirement"], u["adapter"], u["hosts"]) for u in report["uses"]] == [
        ("digest", "documents:read", "notes", ["notes.invalid"]),
        ("digest publish", "documents:write", "notes", ["notes.invalid"]),
    ]
    rendered = CliRunner().invoke(build(kernel), ["system", "capability", "list"])
    assert rendered.exit_code == 0
    assert "Who reads and writes what" in rendered.output
    assert "notes.invalid" in rendered.output


def test_info_shows_who_reads_and_writes_what(kernel):
    code, info = hz(kernel, "system", "info")
    assert code == 0
    assert info["sections"]["system.capability"] == {
        "digest → documents:read": "notes → notes.invalid",
        "digest publish → documents:write": "notes → notes.invalid",
    }


def test_doctor_reports_unmet_requirements_and_unknown_bindings(make_kernel):
    module = Module("announcer", "Needs to read issues.", requires=["tracker:read"])
    config = Config(files=(), data={"capabilities": {"code-host": "nowhere"}})
    kernel = make_kernel(builtins=True, announcer=module, notes=notes(), config=config)
    code, report = hz(kernel, "system", "doctor")
    assert code == 0  # unmet needs are degraded, not down
    checks = {check["name"]: check for check in report["checks"]["system.capability"]}
    assert checks["tracker:read for announcer"]["status"] == "degraded"
    assert checks["tracker:read for announcer"]["detail"] == "tracker:read: no adapter bound"
    assert checks["tracker:read for announcer"]["fix"] == "Install a plugin that provides tracker."
    assert (
        checks["binding code-host"]["detail"] == "code-host is bound to 'nowhere', which no installed module provides"
    )


def test_doctor_reports_met_requirements_as_up(kernel):
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    checks = {check["name"]: check for check in report["checks"]["system.capability"]}
    assert checks["documents:read for digest"] == {
        "name": "documents:read for digest",
        "status": "up",
        "detail": "served by notes",
        "fix": "",
    }


def test_doctor_is_quiet_when_nothing_is_declared(make_kernel):
    code, report = hz(make_kernel(builtins=True), "system", "doctor")
    assert code == 0
    assert report["checks"]["system.capability"] == [
        {"name": "requirements", "status": "up", "detail": "no module declares capabilities", "fix": ""}
    ]


def test_bindings_are_settings_like_any_other(kernel, tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    assert hz(kernel, "system", "config", "set", "capabilities.tracker", "acme")[0] == 0
    assert 'tracker = "acme"' in (tmp_path / "config.toml").read_text()
