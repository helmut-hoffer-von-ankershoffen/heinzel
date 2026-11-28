"""In-process parity across the CLI, MCP, REST and authenticated web heads."""

from typing import Annotated

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from pydantic import BaseModel, Field

from heinzel import Check, HeinzelError, Module, ModuleRecord, Status
from heinzel.heads import api, mcp
from heinzel.kernel import Origin
from heinzel.ports import ProcessRunner
from heinzel.testing import ScriptedProcessRunner, scaled
from heinzel.testing.heads import HEADS, FakeKernel, OperationNotExposedError, operate

pytestmark = pytest.mark.integration
ORIGIN = Origin("heinzel-test", "9.9.9", builtin=False)


class Greeting(BaseModel):
    text: str


def example() -> Module:
    module = Module("example", "An example.")

    @module.operation
    def greet(name: Annotated[str, Field(description="Who to greet.", min_length=1)] = "world") -> Greeting:
        """Greet someone."""
        return Greeting(text=f"Hello, {name}!")

    @module.operation(read_only=False)
    def fail() -> None:
        """Always fail."""
        msg = "this went wrong"
        raise HeinzelError(msg)

    @module.operation(heads={"cli"})
    def wizard() -> None:
        """Interactive only."""

    module.health(lambda: [Check(name="db", status=Status.DOWN, detail="unreachable")])
    return module


@pytest.fixture
def kernel(make_kernel):
    return make_kernel(builtins=True, example=example())


@pytest.mark.parametrize("head", HEADS)
@pytest.mark.parametrize("arguments", [{}, {"name": "Ada"}], ids=["default", "explicit"])
def test_typed_result_across_real_heads(kernel, head, arguments):
    expected = {"text": f"Hello, {arguments.get('name', 'world')}!"}
    result = operate(head, kernel, "example.greet", **arguments)
    assert result.status == (200 if head in {"api", "web"} else 0), result.detail
    assert result.error_kind is None, result.detail
    assert result.payload == expected


@pytest.mark.parametrize("head", HEADS)
def test_declared_process_fake_is_used_by_every_head(settings, make_kernel, head):
    module = Module("probe", "An isolated process call.")

    @module.operation
    def echo(runner: ProcessRunner, text: str, upper: bool = False) -> Greeting:
        """Call an injected process runner and return the scripted text."""
        result = runner.run(("synthetic-probe", text.upper() if upper else text), timeout=scaled(1))
        return Greeting(text=result.stdout)

    fake_runner, real_runner = [
        ScriptedProcessRunner().on("synthetic-probe", "ADA", stdout="fixture response") for _ in range(2)
    ]
    fake = FakeKernel(
        settings=settings, records=[ModuleRecord("probe", module, ORIGIN)], services={ProcessRunner: fake_runner}
    )
    real = make_kernel(builtins=True, probe=module)
    real.bind(ProcessRunner, real_runner)
    outcomes = []
    for kernel, runner in ((fake, fake_runner), (real, real_runner)):
        result = operate(head, kernel, "probe.echo", text="Ada", upper=True)
        assert result.status == (200 if head in {"api", "web"} else 0), result.detail
        assert result.error_kind is None, result.detail
        assert runner.calls == [("synthetic-probe", "ADA")]
        outcomes.append(result.payload)
    assert outcomes == [{"text": "fixture response"}] * 2


@pytest.mark.parametrize("head", HEADS[1:])
def test_harness_refuses_operations_absent_from_the_head(settings, head):
    kernel = FakeKernel(settings=settings, records=[ModuleRecord("example", example(), ORIGIN)])
    with pytest.raises(OperationNotExposedError, match=r"example\.wizard"):
        operate(head, kernel, "example.wizard")


def test_mcp_tools(kernel):
    async def scenario() -> None:
        async with Client(mcp.build(kernel)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert {"example_greet", "example_fail", "system_doctor", "system_config_get"} <= set(tools)
            assert "example_wizard" not in tools
            assert "system_plugin_new" not in tools
            greet = tools["example_greet"]
            assert greet.input_schema["properties"]["name"]["description"] == "Who to greet."
            assert greet.annotations is not None
            assert greet.annotations.read_only_hint is True

            result = await client.call_tool("example_greet", {"name": "Ada"})
            assert not result.is_error
            assert result.structured_content == {"text": "Hello, Ada!"}

            failed = await client.call_tool("example_fail", {})
            assert failed.is_error
            assert "this went wrong" in failed.content[0].text  # type: ignore[union-attr]

            invalid = await client.call_tool("example_greet", {"name": ""})
            assert invalid.is_error

    anyio.run(scenario)


def test_rest_endpoints(kernel):
    client = TestClient(api.build(kernel), base_url="http://localhost")
    assert client.get("/example/greet", params={"name": "Ada"}).json() == {"text": "Hello, Ada!"}
    invalid = client.get("/example/greet", params={"name": ""})
    assert invalid.status_code == 400  # validated by the kernel, like every head
    assert "Invalid arguments for greet" in invalid.json()["error"]
    failed = client.post("/example/fail")
    assert (failed.status_code, failed.json()) == (400, {"error": "this went wrong"})
    assert client.get("/example/fail").status_code == 405  # state-changing operations are POST

    doctor = client.get("/system/doctor")
    assert doctor.status_code == 503  # the example module's check is down
    assert doctor.json()["status"] == "down"

    paths = client.get("/openapi.json").json()["paths"]
    assert "/example/wizard" not in paths
    assert paths["/example/greet"]["get"]["summary"] == "Greet someone."
