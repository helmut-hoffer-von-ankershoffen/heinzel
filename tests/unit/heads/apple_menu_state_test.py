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
    assert (
        menu.index("guard acceptsMenuAction(in: workspace)")
        < menu.index("request.claim()")
        < menu.index("case .about:")
    )
    about = menu[menu.index("case .about:") : menu.index("case .reload:")]
    assert "!window.dispatchEvent(new CustomEvent('hz:menu', { detail: 'about', cancelable: true }))" in about
    assert "request.complete(value, error: error)" in about


def test_mac_workspaces_menu_and_profile_share_the_guarded_native_action() -> None:
    app = (ROOT / "apple/App/HeinzelApp.swift").read_text()
    navigation = (ROOT / "apple/App/Navigation.swift").read_text()
    bridge = (ROOT / "apple/App/WorkspaceView.swift").read_text()
    command = app.split('Button("Workspaces…")', 1)[1].split("CommandGroup(replacing: .newItem)", 1)[0]
    assert '.keyboardShortcut(",", modifiers: [.command, .shift])' in command
    assert "post(name: .showHeinzelConnection" in command
    handler = navigation.split('if message.name == "heinzelWorkspaces"', 1)[1].split(
        'if message.name == "heinzelWindow"', 1
    )[0]
    assert handler.index("acceptsMenuAction(in: workspace)") < handler.index("openWorkspaceChooser()")
    assert handler.index("let name = selectedWorkspaceName()") < handler.index("return (name, nil)")
    assert "workspace.window?.attachedSheet == nil" in handler
    assert "workspace.window?.isKeyWindow == true" in handler
    assert "revision == documentRevision" in handler
    assert "hasCommittedWorkspace" in handler
    callback = app.split("}, openWorkspaceChooser: {", 1)[1].split("}, selectedWorkspaceName:", 1)[0]
    assert callback.index("confirmedOrigin == endpoint.origin") < callback.index("openConnectionFromMenu()")
    assert "#else" in callback
    assert "showConnection = true" in callback
    assert "const workspaces = Object.freeze" in bridge
    assert "heinzelWorkspaces.postMessage('workspaces')" in bridge
    assert "MacMenuDelivery.allowed(available: isMenuWorkspaceCurrent()" in navigation


def test_mac_cold_launch_uses_local_attach_and_iphone_launch_lists_workspaces() -> None:
    app = (ROOT / "apple/App/HeinzelApp.swift").read_text()
    startup = app.split("private func openInitialWorkspace() async", 1)[1].split("private var requestedChat:", 1)[0]
    mac, iphone = startup.split("#if os(macOS)", 1)[1].split("#else", 1)
    assert "await startLocal(initialStartup: true)" in mac
    assert "refreshSyncedWorkspaces()" in iphone
    assert "startLocal(" not in iphone
