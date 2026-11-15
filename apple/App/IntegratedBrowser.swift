import Network
import WebKit

/// Owns a separate, in-memory WebKit session for the shared workspace's Browser panel.
@MainActor
final class IntegratedBrowser: NSObject, WKNavigationDelegate, WKUIDelegate {
    private var surface: WKWebView?
    private var network: BrowserNetwork?
    private var opening = false
    private var generation = 0
    private var endpoint: Endpoint?
    private var status = "empty"

    func handle(_ request: [String: Any], in workspace: WKWebView, excluding endpoint: Endpoint) async -> [String: Any] {
        self.endpoint = endpoint
        switch request["action"] as? String {
        case "navigate":
            guard !opening else { break }
            guard let value = request["url"] as? String, value.utf8.count <= 4096,
                  let url = URL(string: value), permits(url) else {
                surface?.stopLoading()
                markUnavailable()
                return state
            }
            status = "loading"
            let revision = generation
            opening = true
            do {
                let view = try await browser(in: workspace, endpoint: endpoint, revision: revision)
                if revision == generation { view.load(URLRequest(url: url)) }
            } catch {
                if revision == generation { reset(status: "unavailable") }
            }
            if revision == generation { opening = false }
        case "layout":
            guard let bounds = request["bounds"] as? [String: CGFloat],
                  let x = bounds["x"], let y = bounds["y"], let width = bounds["width"], let height = bounds["height"],
                  width > 0, height > 0 else { surface?.isHidden = true; break }
            let frame = Self.surfaceFrame(
                CGRect(x: x, y: y, width: width, height: height), in: workspace
            )
            guard !frame.isEmpty, [frame.minX, frame.minY, frame.width, frame.height].allSatisfy(\.isFinite),
                  workspace.bounds.contains(frame) else { surface?.isHidden = true; break }
            surface?.frame = frame
            surface?.isHidden = status == "unavailable"
        case "hide": surface?.isHidden = true
        case "close": reset(status: "empty")
        case "back": surface?.goBack()
        case "forward": surface?.goForward()
        case "reload": surface?.reload()
        default: break
        }

        return state
    }

    static func surfaceFrame(_ css: CGRect, in workspace: WKWebView) -> CGRect {
        #if os(macOS)
        let zoom = workspace.pageZoom
        let frame = css.applying(CGAffineTransform(scaleX: zoom, y: zoom))
        let top = workspace.isFlipped ? frame.minY : workspace.bounds.height - frame.maxY
        return CGRect(x: frame.minX, y: top, width: frame.width, height: frame.height)
        #else
        return css
        #endif
    }

    private var state: [String: Any] {
        var result: [String: Any] = [
            "status": status, "canGoBack": surface?.canGoBack == true, "canGoForward": surface?.canGoForward == true,
        ]
        if status != "unavailable", let url = surface?.url { result["url"] = url.absoluteString }
        return result
    }

    func reset(status: String = "empty") {
        generation += 1
        opening = false
        surface?.stopLoading()
        surface?.removeFromSuperview()
        surface = nil
        network?.close()
        network = nil
        self.status = status
    }

    /// WebKit may bypass even a non-failover proxy for loopback subresources.
    static func requestRules(excluding host: String?) async throws -> WKContentRuleList {
        // WebKit uses a restricted regular-expression grammar. Start denied and admit only
        // conventional HTTPS DNS authorities. The proxy then validates resolved addresses.
        var rules = [
            ["trigger": ["url-filter": ".*"], "action": ["type": "block"]],
            ["trigger": ["url-filter": #"^https://([a-z0-9-]+\.)+[a-z][a-z0-9-]*(:443)?/"#],
             "action": ["type": "ignore-previous-rules"]],
        ]
        for suffix in BrowserAddress.localSuffixes {
            let escaped = suffix.replacingOccurrences(of: ".", with: #"\."#)
            rules.append([
                "trigger": ["url-filter": "^https://([a-z0-9-]+\\.)*\(escaped)(:443)?/"],
                "action": ["type": "block"],
            ])
        }
        if let host, host.wholeMatch(of: /[a-zA-Z0-9.-]+/) != nil {
            let escaped = host.replacingOccurrences(of: ".", with: #"\."#)
            rules.append(["trigger": ["url-filter": "^https://\(escaped)(:443)?/"], "action": ["type": "block"]])
        }
        let data = try JSONEncoder().encode(rules)
        guard let store = WKContentRuleListStore.default() else { throw URLError(.cannotLoadFromNetwork) }
        let identifier = "heinzel-guest-" + UUID().uuidString
        let compiled = try await store.compileContentRuleList(
            forIdentifier: identifier, encodedContentRuleList: String(decoding: data, as: UTF8.self)
        )
        try? await store.removeContentRuleList(forIdentifier: identifier)
        guard let compiled else { throw URLError(.cannotLoadFromNetwork) }
        return compiled
    }

    private func browser(in workspace: WKWebView, endpoint: Endpoint, revision: Int) async throws -> WKWebView {
        if let surface { return surface }
        guard try await Self.enabled(in: workspace, for: endpoint) else { throw URLError(.userAuthenticationRequired) }
        guard revision == generation else { throw URLError(.cancelled) }
        let host = endpoint.origin.host()?.lowercased()
        let rules = try await Self.requestRules(excluding: host)
        guard revision == generation else { throw URLError(.cancelled) }
        let broker = try BrowserNetwork(deniedHost: host)
        network = broker
        let proxy = try await broker.start()
        guard revision == generation else { throw URLError(.cancelled) }
        let configuration = Self.guestConfiguration(proxy: proxy, rules: rules)
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        view.isHidden = true
        workspace.addSubview(view)
        surface = view
        return view
    }

    private static func enabled(in workspace: WKWebView, for endpoint: Endpoint) async throws -> Bool {
        // Use the owning document's current session or pairing credential. The guest never receives it.
        try await workspace.callAsyncJavaScript("""
            if (location.origin !== new URL(origin).origin) return false;
            const token = sessionStorage.getItem("hz.token") ?? localStorage.getItem("hz.device");
            if (!token) return false;
            const response = await fetch(new URL("/api/system/config/show", origin), {
                headers: { Authorization: "Bearer " + token },
                cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
            });
            if (!response.ok) return false;
            const report = await response.json();
            const features = report.settings?.find(row => row.section === "hz" && row.key === "features")?.value;
            return Array.isArray(features) && features.includes("sidebar_browser");
            """, arguments: ["origin": endpoint.origin.absoluteString], contentWorld: .defaultClient) as? Bool == true
    }

    static func guestConfiguration(proxy: ProxyConfiguration, rules: WKContentRuleList) -> WKWebViewConfiguration {
        let configuration = WKWebViewConfiguration()
        // WebRTC can open direct UDP sockets outside WebKit's HTTPS proxy.
        configuration.defaultWebpagePreferences.isLockdownModeEnabled = true
        configuration.websiteDataStore = .nonPersistent()
        configuration.websiteDataStore.proxyConfigurations = [proxy]
        configuration.userContentController.add(rules)
        #if os(iOS)
        configuration.allowsInlineMediaPlayback = true
        #endif
        return configuration
    }

    private func permits(_ url: URL) -> Bool {
        guard url.scheme == "https", url.user == nil, url.password == nil,
              url.port == nil || url.port == 443, endpoint?.owns(url) == false,
              let host = url.host(), BrowserAddress.host(host + ":443") != nil else { return false }
        return true
    }

    private func markUnavailable() {
        status = "unavailable"
        surface?.isHidden = true
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        guard webView === surface, let frame = action.targetFrame else { return .cancel }
        guard action.request.url.map(permits) == true else {
            if frame.isMainFrame { markUnavailable() }
            return .cancel
        }
        return .allow
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        guard webView === surface else { return .cancel }
        guard response.response.url.map(permits) == true, response.canShowMIMEType else {
            if response.isForMainFrame { markUnavailable() }
            return .cancel
        }
        return .allow
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        if webView === surface { status = "loading" }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === surface, status == "loading" { status = "ready" }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, error: error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, error: error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if webView === surface { reset(status: "unavailable") }
    }

    private func navigationFailed(_ view: WKWebView, error: Error) {
        guard view === surface, (error as NSError).code != NSURLErrorCancelled else { return }
        markUnavailable()
    }

    func webView(
        _ webView: WKWebView, decideMediaCapturePermissionsFor origin: WKSecurityOrigin,
        initiatedBy frame: WKFrameInfo, type: WKMediaCaptureType
    ) async -> WKPermissionDecision { .deny }
}
