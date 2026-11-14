import SwiftUI
import WebKit
#if DEBUG
import OSLog
#endif

#if os(macOS)
typealias WebRepresentable = NSViewRepresentable
#else
typealias WebRepresentable = UIViewRepresentable
#endif

struct WorkspaceView: WebRepresentable {
    let endpoint: Endpoint
    var reportError: (String) -> Void

    func makeCoordinator() -> Navigation { Navigation(endpoint: endpoint, reportError: reportError) }

    private func makeWebView(_ context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlaybackIfSupported()
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.allowsBackForwardNavigationGestures = true
        view.load(URLRequest(url: endpoint.url))
        return view
    }

    #if os(macOS)
    func makeNSView(context: Context) -> WKWebView { makeWebView(context) }
    func updateNSView(_ view: WKWebView, context: Context) {}
    #else
    func makeUIView(context: Context) -> WKWebView { makeWebView(context) }
    func updateUIView(_ view: WKWebView, context: Context) {}
    #endif
}

private extension WKWebViewConfiguration {
    func allowsInlineMediaPlaybackIfSupported() {
        #if os(iOS)
        allowsInlineMediaPlayback = true
        #endif
    }
}

@MainActor
final class Navigation: NSObject, WKNavigationDelegate, WKUIDelegate {
    private let endpoint: Endpoint
    private let reportError: (String) -> Void

    init(endpoint: Endpoint, reportError: @escaping (String) -> Void) {
        self.endpoint = endpoint
        self.reportError = reportError
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        guard let url = action.request.url, let frame = action.targetFrame else { return .cancel }
        if endpoint.owns(url) || (!frame.isMainFrame && url.absoluteString == "about:blank") { return .allow }

        return .cancel
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        response.response.url.map(endpoint.owns) == true ? .allow : .cancel
    }

    func webView(
        _ webView: WKWebView, decideMediaCapturePermissionsFor origin: WKSecurityOrigin,
        initiatedBy frame: WKFrameInfo, type: WKMediaCaptureType
    ) async -> WKPermissionDecision {
        let requestIsOwned = frame.request.url.map(endpoint.owns) == true
        return frame.isMainFrame && requestIsOwned && type == .microphone ? .prompt : .deny
    }

    #if DEBUG
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        Task {
            for _ in 0..<30 {
                let ready = try? await webView.evaluateJavaScript(
                    "Boolean(document.getElementById('root')?.children.length && sessionStorage.getItem('hz.token'))"
                ) as? Bool
                if ready == true {
                    Logger(subsystem: "team.heinzel.apple", category: "workspace").notice("Authenticated workspace rendered")
                    return
                }
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }
    #endif

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        pageFailed(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        pageFailed(error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        reportError("The workspace closed unexpectedly. Reconnect to open it again.")
    }

    private func pageFailed(_ error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        reportError("The workspace could not load. Check that the server is running and reachable.")
    }
}
