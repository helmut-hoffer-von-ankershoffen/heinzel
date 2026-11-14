import WebKit

/// Owns a separate, in-memory WebKit session for the shared workspace's Browser panel.
@MainActor
final class IntegratedBrowser: NSObject, WKNavigationDelegate, WKUIDelegate {
    private var surface: WKWebView?
    private var endpoint: Endpoint?
    private var status = "empty"

    func handle(_ request: [String: Any], in workspace: WKWebView, excluding endpoint: Endpoint) -> [String: Any] {
        self.endpoint = endpoint
        switch request["action"] as? String {
        case "navigate":
            guard let value = request["url"] as? String, value.utf8.count <= 4096,
                  let url = URL(string: value), permits(url) else { return ["status": "unavailable"] }
            status = "loading"
            browser(in: workspace).load(URLRequest(url: url))
        case "layout":
            guard let bounds = request["bounds"] as? [String: CGFloat],
                  let x = bounds["x"], let y = bounds["y"], let width = bounds["width"], let height = bounds["height"],
                  width > 0, height > 0 else { surface?.isHidden = true; break }
            #if os(macOS)
            let top = workspace.isFlipped ? y : workspace.bounds.height - y - height
            #else
            let top = y
            #endif
            let frame = CGRect(x: x, y: top, width: width, height: height)
            guard workspace.bounds.contains(frame) else { surface?.isHidden = true; break }
            surface?.frame = frame
            surface?.isHidden = false
        case "hide": surface?.isHidden = true
        case "close":
            surface?.stopLoading()
            surface?.removeFromSuperview()
            surface = nil
            status = "empty"
        case "back": surface?.goBack()
        case "forward": surface?.goForward()
        case "reload": surface?.reload()
        default: break
        }

        var state: [String: Any] = [
            "status": status, "canGoBack": surface?.canGoBack == true, "canGoForward": surface?.canGoForward == true,
        ]
        if let url = surface?.url { state["url"] = url.absoluteString }
        return state
    }

    private func browser(in workspace: WKWebView) -> WKWebView {
        if let surface { return surface }
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        #if os(iOS)
        configuration.allowsInlineMediaPlayback = true
        #endif
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        view.isHidden = true
        workspace.addSubview(view)
        surface = view
        return view
    }

    private func permits(_ url: URL) -> Bool {
        guard url.scheme == "https", url.user == nil, url.password == nil,
              url.port == nil || url.port == 443, endpoint?.owns(url) == false,
              let host = url.host()?.lowercased(),
              host.wholeMatch(of: /[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z][a-z0-9-]*/) != nil else { return false }
        let local = ["local", "localhost", "internal", "lan", "home", "arpa", "test", "ts.net"]
        return !local.contains { host == $0 || host.hasSuffix("." + $0) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        guard action.targetFrame != nil, action.request.url.map(permits) == true else { return .cancel }
        return .allow
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        response.response.url.map(permits) == true && response.canShowMIMEType ? .allow : .cancel
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { status = "loading" }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { status = "ready" }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { status = "unavailable" }
    }

    func webView(
        _ webView: WKWebView, decideMediaCapturePermissionsFor origin: WKSecurityOrigin,
        initiatedBy frame: WKFrameInfo, type: WKMediaCaptureType
    ) async -> WKPermissionDecision { .deny }
}
