"""Generic explicit toggles use owned private calls and cannot forge credential-copy permission."""

import pytest

from heinzel import Module, RunOperation, Settings, StatusItem, ToggleControl
from heinzel.modules.system.ui import UiService

pytestmark = pytest.mark.unit
ORIGIN = "https://workstation.tail-example.ts.net"


def provider(*, url=ORIGIN, checked=True, enable_operation="enable", forged=ORIGIN):
    module = Module("example", "A generic private service.")

    @module.operation
    def status() -> bool:
        """Show whether the service is ready."""
        return checked

    @module.operation(private=True, read_only=False)
    def enable() -> bool:
        """Enable the service."""
        return True

    @module.operation(private=True, read_only=False)
    def disable() -> bool:
        """Disable the service."""
        return False

    @module.status(source="status")
    def control(ready: bool) -> StatusItem:
        """Control the service through an explicit gesture."""
        return StatusItem(
            text="Example",
            toggle=ToggleControl(
                label="Share this service",
                checked=ready,
                enable=RunOperation(operation=enable_name),
                disable=RunOperation(operation="disable"),
                url=url,
                sign_in_url=forged,
            ),
        )

    enable_name = enable_operation
    return module


def test_toggle_calls_are_built_from_the_owning_private_operations(make_kernel):
    kernel = make_kernel(builtins=True, example=provider(), settings_=Settings(ui_allowed_origins={ORIGIN}))
    entry = kernel.resolve(UiService).status("example:control")[0]
    assert entry.problem is None
    toggle = entry.item.toggle
    assert toggle.enable.call.path == "/example/enable"
    assert toggle.enable.call.body == {}
    assert toggle.disable.call.path == "/example/disable"
    assert toggle.disable.call.body == {}
    assert not toggle.enable.call.read_only
    assert not toggle.disable.call.read_only
    assert toggle.sign_in_url == ORIGIN


@pytest.mark.parametrize(("url", "checked"), [("https://unapproved.example", True), (ORIGIN, False)])
def test_a_provider_cannot_forge_sign_in_link_permission(make_kernel, url, checked):
    kernel = make_kernel(
        builtins=True, example=provider(url=url, checked=checked), settings_=Settings(ui_allowed_origins={ORIGIN})
    )
    entry = kernel.resolve(UiService).status("example:control")[0]
    assert entry.problem is None
    assert entry.item.toggle.sign_in_url is None


def test_a_toggle_cannot_call_another_modules_operation(make_kernel):
    kernel = make_kernel(builtins=True, example=provider(enable_operation="system:info"))
    entry = kernel.resolve(UiService).status("example:control")[0]
    assert entry.item is None
    assert entry.problem
