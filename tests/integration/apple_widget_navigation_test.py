"""Guard request-scoped widget authorization wiring; WebKit runtime is a separate gate."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_webview_auth_context_is_immutable_and_main_frame_scoped() -> None:
    source = (ROOT / "apple/App/WorkspaceView.swift").read_text(encoding="utf-8")
    navigation = source.split("final class Navigation:", 1)[1]
    assert "private let widgetActivation: ActivityCatalog.Activation?" in navigation
    assert (
        "self.widgetActivation = activation?.endpoint == endpoint.origin.absoluteString ? activation : nil"
        in navigation
    )
    response = navigation.split("decidePolicyFor response:", 1)[1].split("func webView(", 1)[0]
    assert "webView === workspace" in response
    assert "response.isForMainFrame" in response
    assert "http.statusCode == 401 || http.statusCode == 403" in response
    assert "WidgetCredentialStore.invalidateShared(widgetActivation)" in response
    assert "ActivityWidgetData.currentActivation()" not in response
    finished = navigation.split("didFinish navigation:", 1)[1].split("private func resolveWorkspaceName", 1)[0]
    assert "currentNavigation === navigation" in finished
    assert "revision == documentRevision" in finished
    assert "if needsSignIn == true" in finished
    assert "WidgetCredentialStore.invalidateShared(widgetActivation)" in finished
    failed = navigation.split("private func pageFailed", 1)[1]
    assert "failure.code == NSURLErrorUserAuthenticationRequired" in failed
    assert "WidgetCredentialStore.invalidateShared(widgetActivation)" in failed


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
    assert "@State private var promptComposer = NativePromptComposer()" in app
    assert "promptComposer: promptComposer" in app


def test_enrollment_is_opt_in_and_uses_view_lifecycle_not_widget_scope() -> None:
    source = (ROOT / "apple/App/WorkspaceView.swift").read_text(encoding="utf-8")
    enrollment = source.split("private func scheduleWidgetEnrollment", 1)[1].split("\n    func webView(", 1)[0]
    assert "WidgetCredentialStore.configured()" in enrollment
    assert "self.documentRevision == revision" in enrollment
    assert "self.isEnrollmentViewCurrent()" in enrollment
    assert "WidgetEnrollmentTransport.hasPairedDevice" in enrollment
    assert "enrollment.canResume(pending)" in enrollment
    assert "isWidgetWorkspaceCurrent()" not in enrollment
    info = (ROOT / "apple/App/Info.plist").read_text(encoding="utf-8")
    assert "HeinzelWidgetEnrollmentEnabled" not in info
