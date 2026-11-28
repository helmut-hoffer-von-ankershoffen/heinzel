"""Guard request-scoped widget authorization wiring; WebKit runtime is a separate gate."""

import plistlib
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_webview_auth_context_is_immutable_and_main_frame_scoped() -> None:
    source = "\n".join(
        (ROOT / "apple/App" / f"{n}.swift").read_text(encoding="utf-8")
        for n in ["WorkspaceView", "NativePrompt", "TitleBarDragLayout", "Navigation"]
    )
    navigation = source.split("final class Navigation:", 1)[1]
    assert "private let widgetActivation: ActivityCatalog.Activation?" in navigation
    assert (
        "self.widgetActivation = activation?.endpoint == endpoint.origin.absoluteString ? activation : nil"
        in navigation
    )
    response = navigation.split("decidePolicyFor response:", 1)[1].split("func webView(", 1)[0]
    assert "webView === workspace" in response
    assert "response.isForMainFrame" in response
    assert "navigationFailed(problem, in: webView)" in response
    failed_navigation = navigation.split("private func navigationFailed", 1)[1].split("func webView(", 1)[0]
    assert "await WorkspaceWebSession.issue(in: view" in failed_navigation
    assert "revision == documentRevision" in failed_navigation
    assert "if issue.revokesAccess, let widgetActivation" in failed_navigation
    assert "WidgetCredentialStore.invalidateShared(widgetActivation)" in failed_navigation
    assert "ActivityWidgetData.currentActivation()" not in failed_navigation
    finished = navigation.split("didFinish navigation:", 1)[1].split("private func resolveWorkspaceName", 1)[0]
    assert "currentNavigation === navigation" in finished
    assert "revision == documentRevision" in finished
    assert "if needsSignIn == true" in finished
    assert "WidgetCredentialStore.invalidateShared(widgetActivation)" in finished
    failed = navigation.split("private func pageFailed", 1)[1]
    assert "navigationFailed(WorkspaceProblem(navigationError: error), in: view)" in failed


def test_widget_rotation_recreates_only_the_current_workspace_view() -> None:
    app = (ROOT / "apple/App/HeinzelApp.swift").read_text(encoding="utf-8")
    handler = app.split(
        ".onReceive(NotificationCenter.default.publisher(for: WidgetCredentialStore.activationChanged)", 1
    )[1]
    handler = handler.split("\n        }", 1)[0]
    assert "receive(on: RunLoop.main)" in handler
    assert "widgetViewRotation.shouldReload" in handler
    assert "currentID: activation?.id" in handler
    assert "currentEndpoint: activation?.endpoint" in handler
    assert "visibleEndpoint: endpoint?.origin.absoluteString" in handler
    assert "retryWorkspace()" in handler
    assert ".id(revision)" in app
    composer = app.split("private var promptComposer: NativePromptComposer {", 1)[1].split("\n    }", 1)[0]
    assert "guard let scope = widgetWorkspaceScope else { return disconnectedPromptComposer }" in composer
    assert "NativePromptDrafts.shared.composer(for: scope)" in composer
    assert "promptComposer: promptComposer" in app


def test_enrollment_is_opt_in_and_uses_view_lifecycle_not_widget_scope() -> None:
    source = "\n".join(
        (ROOT / "apple/App" / f"{n}.swift").read_text(encoding="utf-8")
        for n in ["WorkspaceView", "NativePrompt", "TitleBarDragLayout", "Navigation"]
    )
    enrollment = source.split("private func scheduleWidgetEnrollment", 1)[1].split("\n    func webView(", 1)[0]
    assert "WidgetCredentialStore.configured()" in enrollment
    assert "self.documentRevision == revision" in enrollment
    assert "self.isEnrollmentViewCurrent()" in enrollment
    assert "WidgetEnrollmentTransport.pairedDeviceFingerprint" in enrollment
    assert "ActivityCatalog.enrollmentPairingMatches" in enrollment
    assert "guard matchesPairing && enrollment.canResume(pending)" in enrollment
    assert "else if let expired = enrollment.finishExpiredReceipt(pending)" in enrollment
    assert "enrollment.canResume(pending)" in enrollment
    recovery = enrollment.split("guard matchesPairing && enrollment.canResume(pending) else", 1)[1].split("return", 1)[
        0
    ]
    assert recovery.index("pending.previous.endpoint == endpoint.origin.absoluteString") < recovery.index(
        "enrollment.abandonInterruptedPreparation(pending)"
    )
    assert "isWidgetWorkspaceCurrent()" not in enrollment
    for target in ("App", "Widgets"):
        mac_info = plistlib.loads((ROOT / f"apple/{target}/Info.plist").read_bytes())
        ios_info = plistlib.loads((ROOT / f"apple/{target}/iOS-Info.plist").read_bytes())
        assert mac_info["HeinzelWidgetEnrollmentEnabled"] == "$(HEINZEL_MAC_WIDGET_REFRESH)"
        assert ios_info["HeinzelWidgetEnrollmentEnabled"] is True

    project = (ROOT / "apple/Heinzel.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
    configurations = re.findall(
        r"isa = XCBuildConfiguration; name = (Debug|Release); buildSettings = \{(.*?)\};",
        project,
        re.DOTALL,
    )
    assert len(configurations) == 6
    for name in ("Debug", "Release"):
        settings = [body for config, body in configurations if config == name]
        assert len(settings) == 3  # Project, app and extension; both targets inherit NO.
        defaults = [body for body in settings if "HEINZEL_MAC_WIDGET_REFRESH" in body]
        assert len(defaults) == 1
        assert "HEINZEL_MAC_WIDGET_REFRESH = NO;" in defaults[0]
        assert defaults[0].count("HEINZEL_MAC_WIDGET_REFRESH") == 1
        targets = [body for body in settings if "INFOPLIST_FILE =" in body]
        assert len(targets) == 2
        for target in ("Widgets", "App"):
            matching = [body for body in targets if f"INFOPLIST_FILE = {target}/Info.plist;" in body]
            assert len(matching) == 1
            body = matching[0]
            assert f"INFOPLIST_FILE = {target}/Info.plist;" in body
            assert f'"INFOPLIST_FILE[sdk=iphone*]" = {target}/iOS-Info.plist;' in body
            assert "HEINZEL_MAC_WIDGET_REFRESH" not in body

    store = (ROOT / "apple/Shared/WidgetCredentialStore.swift").read_text(encoding="utf-8")
    opt_in = store.split("static var enrollmentEnabled: Bool {", 1)[1].split("\n    }", 1)[0]
    assert "#if os(macOS)" in opt_in
    mac, ios = opt_in.split("#else", 1)
    assert (
        'return Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetEnrollmentEnabled") as? String == "YES"' in mac
    )
    assert 'return Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetEnrollmentEnabled") as? Bool == true' in ios
    configured = store.split("static func configured() -> WidgetCredentialStore? {", 1)[1].split("\n    }", 1)[0]
    assert "guard enrollmentEnabled," in configured
    assert "let url = ActivityWidgetData.sharedCatalogURL()" in configured
    assert 'let group = Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetKeychainGroup") as? String' in configured
    assert "let keychain = SharedWidgetKeychain(accessGroup: group) else { return nil }" in configured
