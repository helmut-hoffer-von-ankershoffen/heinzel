"""In-process parity across the CLI, MCP, REST and authenticated web heads."""

import json
from typing import Annotated, Literal

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from heinzel import Check, HeinzelError, Kernel, Module, Status
from heinzel.heads import api, cli, mcp, web

pytestmark = pytest.mark.integration


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


@pytest.mark.parametrize("head", ["cli", "api", "mcp", "web"])
@pytest.mark.parametrize("arguments", [{}, {"name": "Ada"}], ids=["default", "explicit"])
def test_typed_result_across_real_heads(
    kernel: Kernel, head: Literal["cli", "api", "mcp", "web"], arguments: dict[str, str]
) -> None:
    expected = {"text": f"Hello, {arguments.get('name', 'world')}!"}
    if head == "cli":
        options = [part for key, value in arguments.items() for part in (f"--{key}", value)]
        result = CliRunner().invoke(cli.build(kernel), ["example", "greet", *options, "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == expected
    elif head == "mcp":

        async def scenario() -> None:
            async with Client(mcp.build(kernel)) as client:
                result = await client.call_tool("example_greet", arguments)
                assert not result.is_error, result.content
                assert result.structured_content == expected

        anyio.run(scenario)
    else:
        if head == "web":
            token = "head-parity-session"
            client = TestClient(web.build(kernel, port=None, token=token, static=None), base_url="http://localhost")
            response = client.get("/api/example/greet", params=arguments, headers={"Authorization": f"Bearer {token}"})
        else:
            client = TestClient(api.build(kernel), base_url="http://localhost")
            response = client.get("/example/greet", params=arguments)
        assert response.status_code == 200, response.text
        assert response.json() == expected


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
