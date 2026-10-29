"""An unknown provider contributes every common surface against a plugin-free core."""

import json

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel import (
    DetailLine,
    LiveClock,
    MetricWidget,
    Module,
    Navigate,
    NoticeKind,
    Notifications,
    ServiceContract,
    StatusItem,
    StatusMeter,
)
from heinzel.heads import api, mcp, web
from heinzel.heads.cli import build
from heinzel.modules.system.ui import UiService

pytestmark = pytest.mark.integration
TOKEN = "synthetic-plugin-contract-test-session"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(params=["sprocket", "unforeseen"])
def provider(request, tmp_path):
    module = Module(request.param, "A provider core has never heard of.")

    @module.operation
    def read() -> int:
        """Read the provider's value."""
        return 7

    @module.operation(read_only=False, private=True, heads={"cli", "mcp", "web"})
    def publish(notifications: Notifications) -> str:
        """Publish one notice through the ordinary notification policy."""
        notice = notifications.emit(
            NoticeKind.PLUGIN, "A provider notice", route="/", command="hz system info", key="synthetic:notice"
        )
        return notice.title if notice else "Off"

    @module.status(source="read", order=123, slots={"footer": True, "sidebar": True})
    def value(number: int) -> StatusItem:
        return StatusItem(
            text=f"Value {number}",
            presentation="ticker",
            meters=[StatusMeter(label="Capacity", value=70, tone="ok")],
            segments=[DetailLine(text="Here", live=LiveClock(zone="UTC"))],
        )

    @module.widget(source="read", order=124)
    def today(number: int) -> MetricWidget:
        return MetricWidget(label="Provider value", value=str(number))

    module.action(
        "inspect",
        label="Inspect provider",
        icon="globe",
        effect=Navigate(path=f"/?panel={module.name}:inspector"),
        order=125,
    )
    module.nav(title="Provider", icon="globe", sidebar=Navigate(path=f"/?panel={module.name}:inspector"), order=126)
    bundle = tmp_path / module.name
    bundle.mkdir()
    (bundle / "index.js").write_text("export default sdk => () => sdk.React.createElement('p', null, 'Plugin view');")
    (bundle / "style.css").write_text(".provider { color: inherit; }")
    module.view(bundle, abi=1, name="inspector", source="read", title="Inspector", fallback="Use the CLI.", order=127)
    return module


def test_unknown_provider_operations_have_cli_mcp_and_api_parity(make_kernel, provider):
    kernel = make_kernel(builtins=True, **{provider.name: provider})
    output = CliRunner().invoke(build(kernel), [provider.name, "read", "--json"])
    assert output.exit_code == 0, output.output
    assert json.loads(output.stdout) == 7
    http = TestClient(api.build(kernel), base_url="http://localhost")
    assert http.get(f"/{provider.name}/read").json() == 7

    async def invoke():
        async with Client(mcp.build(kernel)) as client:
            answer = await client.call_tool(f"{provider.name}_read")
            assert not answer.is_error
            return json.loads(answer.content[0].text)

    assert anyio.run(invoke) == 7


def test_unknown_provider_contributes_widgets_notices_status_navigation_and_view(make_kernel, provider):
    kernel = make_kernel(builtins=True, **{provider.name: provider})
    ui = kernel.resolve(UiService)
    status = next(entry for entry in ui.status() if entry.module == provider.name)
    assert status.order == 123
    assert status.item.meters[0].value == 70
    assert status.item.segments[0].live.zone == "UTC"
    assert next(entry for entry in ui.widgets() if entry.module == provider.name).widget.value == "7"
    assert next(entry for entry in ui.actions() if entry.module == provider.name).order == 125
    assert next(entry for entry in ui.navigation() if entry.module == provider.name).order == 126
    view = ui.views()[0]
    assert (view.id, view.order, view.problem) == (f"{provider.name}:inspector", 127, None)
    assert view.source.path == f"/{provider.name}/read"
    output = CliRunner().invoke(build(kernel), [provider.name, "publish", "--json"])
    assert output.exit_code == 0, output.output
    assert json.loads(output.stdout) == "A provider notice"
    assert kernel.resolve(Notifications).recent()[0].kind is NoticeKind.PLUGIN


def test_only_active_unchanged_installed_assets_are_served(make_kernel, provider):
    kernel = make_kernel(builtins=True, **{provider.name: provider})
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    assert client.get("/api/system/ui/views").status_code == 401
    entry = client.get("/api/system/ui/views", headers=AUTH).json()[0]
    assert client.get(entry["bundle"]).headers["content-type"].startswith("text/javascript")
    assert client.get(entry["styles"][0]).headers["content-type"].startswith("text/css")
    assert client.get(entry["bundle"], headers={"Host": "elsewhere.example"}).status_code == 403
    assert client.get("/plugin-sdk/v1/react.js").status_code == 200
    assert client.get("/plugin-sdk/v1/missing.js").status_code == 404
    directory = next(
        let.directory for let in provider.lets if getattr(let, "source", None) == "read" and hasattr(let, "directory")
    )
    (directory / "index.js").write_text("changed after discovery")
    assert client.get(entry["bundle"]).status_code == 404
    absent = TestClient(
        web.build(make_kernel(builtins=True), port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000"
    )
    assert absent.get(entry["bundle"]).status_code == 404
    disabled = make_kernel(
        builtins=True,
        settings_=kernel.settings.model_copy(update={"disabled_modules": [provider.name]}),
        **{provider.name: provider},
    )
    assert disabled.resolve(UiService).views() == []


def test_incompatible_view_does_not_break_the_python_plugin(make_kernel, provider):
    view = next(let for let in provider.lets if hasattr(let, "abi"))
    provider._lets.remove(view)
    provider.view(view.directory, abi=999, name=view.name, source=view.source, title=view.title, fallback=view.fallback)
    kernel = make_kernel(builtins=True, **{provider.name: provider})
    entry = kernel.resolve(UiService).views()[0]
    assert entry.problem
    assert entry.bundle is None
    assert entry.source is None
    output = CliRunner().invoke(build(kernel), [provider.name, "read", "--json"])
    assert json.loads(output.stdout) == 7


class ReadingContract(ServiceContract):
    """An implementation-free interface, as shipped by an SDK or neutral contract package."""

    def value(self) -> int:
        """Read a provider value."""
        raise NotImplementedError


@pytest.mark.parametrize("provider_name", ["source-a", "source-b"])
def test_plugins_consume_an_interface_without_importing_its_provider(make_kernel, provider_name):
    provider = Module(provider_name, "An interchangeable implementation.")
    consumer = Module("consumer", "Depends on a contract, never a provider.")

    class Reading(ReadingContract):
        def value(self) -> int:
            return 42

    provider.service(ReadingContract, Reading)

    @consumer.operation
    def read(source: ReadingContract) -> int:
        """Read through the public interface."""
        return source.value()

    kernel = make_kernel(builtins=True, consumer=consumer, **{provider_name: provider})
    result = CliRunner().invoke(build(kernel), ["consumer", "read", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == 42
    # The same core works with neither implementation nor its consumer installed.
    assert make_kernel(builtins=True).modules


@pytest.mark.parametrize("route", ["/settings", "/today", "/plugins/another/inspector", "/plugins/{owner}/inspector"])
def test_page_views_can_only_claim_their_owned_route(make_kernel, provider, route):
    view = next(let for let in provider.lets if hasattr(let, "abi"))
    provider._lets.remove(view)
    route = route.format(owner=provider.name)
    provider.view(
        view.directory,
        abi=1,
        name=view.name,
        source=view.source,
        title=view.title,
        fallback=view.fallback,
        slot="page",
        route=route,
    )
    kernel = make_kernel(builtins=True, **{provider.name: provider})
    entry = kernel.resolve(UiService).views()[0]
    if route == f"/plugins/{provider.name}/inspector":
        assert entry.problem is None
        assert entry.bundle
    else:
        assert "owned" in entry.problem
        assert entry.bundle is None
    assert kernel.invoke(next(let for let in provider.lets if getattr(let, "name", "") == "read")) == 7


def _contract_consumer(indirect, annotated):
    from typing import Annotated

    from pydantic import Field

    contract_type = (
        Annotated[ReadingContract, Field(description="Required public interface.")] if annotated else ReadingContract
    )
    consumer = Module("consumer", "Uses an optional installed provider through its required public contract.")

    class ConsumerService:
        def __init__(self, source: contract_type):
            self.source = source

    consumer.service(ConsumerService)
    if indirect:

        @consumer.operation
        def dependent(service: ConsumerService) -> int:
            """Use a service that in turn needs the public contract."""
            return service.source.value()
    else:

        @consumer.operation
        def dependent(source: contract_type) -> int:
            """Use the required public contract directly."""
            return source.value()

    @consumer.operation
    def healthy() -> str:
        """Remain usable without a provider."""
        return "ready"

    return consumer, dependent


@pytest.mark.parametrize("state", ["absent", "disabled", "replacement"])
@pytest.mark.parametrize("indirect", [False, True])
@pytest.mark.parametrize("annotated", [False, True])
def test_missing_or_disabled_contract_only_removes_dependent_operations(make_kernel, state, indirect, annotated):
    from heinzel import HeinzelError, Settings

    consumer, dependent = _contract_consumer(indirect, annotated)
    provider = Module("provider", "Provides a public contract.")
    replacement = Module("replacement", "An interchangeable public-contract implementation.")

    class Reading(ReadingContract):
        def value(self) -> int:
            return 42

    provider.service(ReadingContract, Reading)
    replacement.service(ReadingContract, Reading)

    modules = {"consumer": consumer}
    if state != "absent":
        modules["provider"] = provider
    if state == "replacement":
        modules["replacement"] = replacement
    settings = Settings(disabled_modules=frozenset({"provider"}))
    kernel = make_kernel(builtins=True, settings_=settings, **modules)
    present = state == "replacement"
    assert kernel.available(dependent) is present
    http = TestClient(api.build(kernel), base_url="http://localhost")
    assert http.get("/consumer/healthy").json() == "ready"
    assert http.get("/consumer/dependent").status_code == (200 if present else 404)
    output = CliRunner().invoke(build(kernel), ["consumer", "healthy", "--json"])
    assert json.loads(output.stdout) == "ready"
    dependent_call = CliRunner().invoke(build(kernel), ["consumer", "dependent", "--json"])
    assert dependent_call.exit_code == (0 if present else 2)

    async def tools():
        async with Client(mcp.build(kernel)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert ("consumer_dependent" in names) is present
            result = await client.call_tool("consumer_healthy")
            assert not result.is_error
            assert result.content[0].text == "ready"

    anyio.run(tools)
    if not present:
        with pytest.raises(HeinzelError, match="No module provides ReadingContract"):
            kernel.call(dependent)
