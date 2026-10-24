import pytest

from heinzel import Kernel, Module
from heinzel.heads._exposure import exposed

pytestmark = pytest.mark.unit


def _modules() -> dict[str, Module]:
    tools = Module("tools", "Tools.")

    @tools.operation
    def hammer(kernel: Kernel, nail: str) -> str:
        """Hit a nail."""
        return nail

    @tools.operation(heads={"cli"})
    def wizard() -> None:
        """Ask questions."""

    ping = Module("ping", "Ping.")

    @ping.operation
    def ping_() -> str:
        """Pong."""
        return "pong"

    return {"tools": tools, "tools__ping": ping}


def test_paths_names_and_public_parameters(make_kernel):
    items = {item.name: item for item in exposed(make_kernel(**_modules()), "mcp")}
    assert set(items) == {"tools_hammer", "tools_ping"}
    assert items["tools_hammer"].path == ("tools", "hammer")
    assert [p.name for p in items["tools_hammer"].parameters] == ["nail"]
    assert items["tools_hammer"].returns is str
    assert items["tools_ping"].path == ("tools", "ping")  # a one-operation submodule collapses


def test_heads_restrict_exposure(make_kernel):
    kernel = make_kernel(**_modules())
    assert "tools_wizard" in {item.name for item in exposed(kernel, "cli")}
    assert "tools_wizard" not in {item.name for item in exposed(kernel, "api")}


def test_an_operation_named_like_its_module_beside_others_takes_the_module_path(make_kernel):
    mode = Module("mode", "A mode.")

    @mode.operation
    def mode_() -> str:
        """Show it."""
        return "off"

    @mode.operation(read_only=False)
    def on() -> str:
        """Turn it on."""
        return "on"

    paths = {item.name: item.path for item in exposed(make_kernel(mode=mode), "api")}
    assert paths == {"mode": ("mode",), "mode_on": ("mode", "on")}
