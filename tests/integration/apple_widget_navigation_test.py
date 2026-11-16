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
