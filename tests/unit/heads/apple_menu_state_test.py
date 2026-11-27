"""Keep the native menu probe in step with the menu state it exercises, without compiling Swift.

The compiled probe runs in tests/integration/apple_menu_state_test.py on macOS.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "apple/Shared/NativeRoute.swift"
PROBE = ROOT / "tests/fixtures/apple-menu-state.swift"
pytestmark = pytest.mark.unit


def test_menu_probe_covers_every_state_field_and_route() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    probe = PROBE.read_text(encoding="utf-8")
    state = source[source.index("struct MacMenuState") : source.index("enum MacMenuDelivery")]
    fields = re.findall(r"^\s*let (\w+): Bool$", state, re.MULTILINE)
    routes = re.search(r"enum ConnectionRoute: Equatable \{ case ([\w, ]+) \}", state)
    assert fields, "MacMenuState lost its stored flags"
    assert routes, "MacMenuState lost its ConnectionRoute cases"
    initializer = re.search(r"MacMenuState\(([^)]*)\)", probe, re.DOTALL)
    assert initializer, "the probe no longer builds a MacMenuState"
    assert re.findall(r"(\w+):", initializer.group(1)) == fields
    for route in (name.strip() for name in routes.group(1).split(",")):
        assert f"connectionRoute == .{route})" in probe, f"the probe never asserts route .{route}"
    delivery = re.search(r"static func allowed\(([^)]*)\)", source, re.DOTALL)
    assert delivery
    for parameter in re.findall(r"(\w+): Bool", delivery.group(1)):
        assert f"{parameter}:" in probe, f"the probe never passes MacMenuDelivery {parameter}"


def test_about_uses_the_guarded_workspace_menu_with_a_native_fallback() -> None:
    app = (ROOT / "apple/App/HeinzelApp.swift").read_text(encoding="utf-8")
    coordinator = (ROOT / "apple/App/Navigation.swift").read_text(encoding="utf-8")
    app_info = app[app.index("CommandGroup(replacing: .appInfo)") : app.index("CommandGroup(replacing: .appSettings)")]
    assert re.search(r"WorkspaceMenuRequest\.send\(\.about\) \{\s*NSApp\.orderFrontStandardAboutPanel\(nil\)", app_info)
    menu = coordinator[coordinator.index("@objc private func menuAction") : coordinator.index("func close(_ view")]
    assert menu.index("guard MacMenuDelivery.allowed(") < menu.index("request.claim()") < menu.index("case .about:")
    about = menu[menu.index("case .about:") : menu.index("case .reload:")]
    assert "!window.dispatchEvent(new CustomEvent('hz:menu', { detail: 'about', cancelable: true }))" in about
    assert "request.complete(value, error: error)" in about
