import Network
import WebKit

@MainActor
private final class GuestLookupReply {
    private var continuation: CheckedContinuation<Bool, Never>?
    var timeout: Task<Void, Never>?

    init(_ continuation: CheckedContinuation<Bool, Never>) { self.continuation = continuation }
    func finish(_ result: Bool) {
        guard let continuation else { return }
        self.continuation = nil
        timeout?.cancel()
        timeout = nil
        continuation.resume(returning: result)
    }
}

/// A stalled resolver cannot delay a handoff or accumulate more outstanding lookups.
@MainActor
final class BoundedGuestDNS {
    private(set) var busy = false

    func resolve(_ host: String, timeout: Duration = .seconds(1),
                 lookup: @escaping @Sendable (String) -> Bool) async -> Bool {
        guard !busy else { return false }
        busy = true
        return await withCheckedContinuation { continuation in
            let reply = GuestLookupReply(continuation)
            reply.timeout = Task {
                do { try await Task.sleep(for: timeout) } catch { return }
                reply.finish(false)
            }
            Task {
                let result = await Task.detached { lookup(host) }.value
                busy = false
                reply.finish(result)
            }
        }
    }
}

/// Serializes a guest wipe and blocks new openings until its data store is finished.
@MainActor
final class GuestDataClear<Store> {
    private(set) var clearing = false
    private var retained: Store?

    func run(detach: () -> Store?, erase: @MainActor (Store) async -> Bool) async -> Bool {
        guard !clearing else { return false }
        clearing = true
        defer { clearing = false }
        if let detached = detach() { retained = detached }
        guard let retained else { return true }
        let success = await erase(retained)
        if success { self.retained = nil }
        return success
    }
}

@MainActor
private final class GuestBlank: NSObject, WKNavigationDelegate {
    private var pending: CheckedContinuation<Bool, Never>?
    private var timeout: Task<Void, Never>?
    private var navigation: WKNavigation?

    func run(_ view: WKWebView) async -> Bool {
        view.navigationDelegate = self
        view.uiDelegate = nil
        defer { view.navigationDelegate = nil }
        return await withCheckedContinuation { continuation in
            pending = continuation
            navigation = view.loadHTMLString("", baseURL: nil)
            guard navigation != nil else { finish(false); return }
            timeout = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(3)) } catch { return }
                self?.finish(false)
            }
        }
    }

    private func finish(_ result: Bool) {
        guard let pending else { return }
        self.pending = nil
        timeout?.cancel()
        timeout = nil
        pending.resume(returning: result)
    }

    func webView(_: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        guard pending != nil, action.targetFrame?.isMainFrame == true,
              action.request.url?.absoluteString == "about:blank" else { return .cancel }
        return .allow
    }

    func webView(_: WKWebView, decidePolicyFor response: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        guard pending != nil, response.isForMainFrame,
              response.response.url?.absoluteString == "about:blank" else { return .cancel }
        return .allow
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if navigation === self.navigation { finish(true) }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if navigation === self.navigation { finish(false) }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if navigation === self.navigation { finish(false) }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { finish(false) }
}

/// Owns a separate, in-memory WebKit session for the shared workspace's Browser panel.
@MainActor
final class IntegratedBrowser: NSObject, WKNavigationDelegate, WKUIDelegate {
    struct ExternalTarget: Equatable {
        let url: URL
        let revision: Int
    }
    private final class Tab {
        let id = UUID().uuidString
        var view: WKWebView?
        var urlObservation: NSKeyValueObservation?
        var navigationIdentity = NavigationIdentity<WKNavigation>()
        var status = "empty"
    }

    private static let maxTabs = 8
    private var tabs: [Tab] = []
    private var selected: Tab?
    private var surface: WKWebView? { selected?.view }
    private var configuration: WKWebViewConfiguration?
    private var frame: CGRect?
    private var network: BrowserNetwork?
    private var opening = false
    private var generation = 0
    private var pageRevision = 0
    private var endpoint: Endpoint?
    private var emptyStatus = "empty"
    private var status: String { selected?.status ?? emptyStatus }
    private var clearFailed = false
    private let guestClear = GuestDataClear<([WKWebView], WKWebsiteDataStore)>()

    func handle(_ request: [String: Any], in workspace: WKWebView, excluding endpoint: Endpoint) async -> [String: Any] {
        self.endpoint = endpoint
        switch request["action"] as? String {
        case "new-tab":
            guard !guestClear.clearing, !clearFailed, tabs.count < Self.maxTabs else { break }
            addTab()
        case "select-tab":
            guard let id = request["tab"] as? String, let tab = tabs.first(where: { $0.id == id }) else { break }
            select(tab)
        case "close-tab":
            guard let id = request["tab"] as? String, let index = tabs.firstIndex(where: { $0.id == id }) else { break }
            let tab = tabs.remove(at: index)
            detach(tab)
            if selected === tab {
                select(tabs.isEmpty ? nil : tabs[min(index, tabs.count - 1)])
            }
        case "navigate":
            guard !opening, !guestClear.clearing, !clearFailed else { break }
            guard let value = request["url"] as? String, value.utf8.count <= 4096,
                  let url = URL(string: value), permits(url) else {
                surface?.stopLoading()
                markUnavailable()
                return state
            }
            let tab = selected ?? addTab()
            pageRevision &+= 1
            tab.status = "loading"
            let attempt = tab.navigationIdentity.begin(target: url)
            let revision = generation
            opening = true
            do {
                let view = try await browser(in: workspace, endpoint: endpoint, revision: revision, tab: tab)
                if revision == generation, tabs.contains(where: { $0 === tab }) {
                    let navigation = view.load(URLRequest(url: url))
                    tab.navigationIdentity.attach(navigation, to: attempt)
                    if navigation == nil {
                        tab.navigationIdentity.cancel(attempt)
                        markUnavailable(tab)
                    }
                }
            } catch {
                if revision == generation {
                    tab.navigationIdentity.cancel(attempt)
                    markUnavailable(tab)
                    updateVisibility()
                }
            }
            if revision == generation { opening = false }
        case "layout":
            guard let bounds = request["bounds"] as? [String: CGFloat],
                  let x = bounds["x"], let y = bounds["y"], let width = bounds["width"], let height = bounds["height"],
                  width > 0, height > 0 else { hide(); break }
            let frame = Self.surfaceFrame(
                CGRect(x: x, y: y, width: width, height: height), in: workspace
            )
            guard !frame.isEmpty, [frame.minX, frame.minY, frame.width, frame.height].allSatisfy(\.isFinite),
                  workspace.bounds.contains(frame) else { hide(); break }
            self.frame = frame
            updateVisibility()
        case "hide": hide()
        case "close": reset()
        case "clear":
            let workspaceStore = workspace.configuration.websiteDataStore
            let cleared = await guestClear.run {
                let guests = tabs.compactMap(\.view)
                let store = configuration?.websiteDataStore
                // Stop every guest and revoke the proxy before touching their store.
                reset()
                return store.map { (guests, $0) }
            } erase: { pair in
                await Self.eraseGuests(pair, excluding: workspaceStore)
            }
            clearFailed = !cleared
        case "back":
            guard let tab = selected, let surface, surface.canGoBack else { break }
            pageRevision &+= 1
            let attempt = tab.navigationIdentity.begin(target: surface.backForwardList.backItem?.url)
            tab.status = "loading"
            let navigation = surface.goBack()
            tab.navigationIdentity.attach(navigation, to: attempt)
        case "forward":
            guard let tab = selected, let surface, surface.canGoForward else { break }
            pageRevision &+= 1
            let attempt = tab.navigationIdentity.begin(target: surface.backForwardList.forwardItem?.url)
            tab.status = "loading"
            let navigation = surface.goForward()
            tab.navigationIdentity.attach(navigation, to: attempt)
        case "reload":
            pageRevision &+= 1
            guard let tab = selected, let surface else { break }
            tab.status = "loading"
            let attempt = tab.navigationIdentity.begin(target: surface.url)
            // `reload()` can return before WKWebView calls didStart; expose progress in this IPC reply.
            // Keep the returned navigation identity so a delayed callback from the replaced page cannot win.
            guard let navigation = surface.reload() else {
                tab.navigationIdentity.cancel(attempt)
                markUnavailable(tab)
                break
            }
            tab.navigationIdentity.attach(navigation, to: attempt)
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

    private static func eraseGuests(_ pair: ([WKWebView], WKWebsiteDataStore),
                                    excluding workspaceStore: WKWebsiteDataStore) async -> Bool {
        let (guests, store) = pair
        // Never clear a persistent store or the workspace's own session.
        guard !store.isPersistent, store !== workspaceStore else { return false }
        let blanks = guests.map { guest in Task { await GuestBlank().run(guest) } }
        var blanked = true
        for blank in blanks {
            let finished = await blank.value
            blanked = blanked && finished
        }
        let types = WKWebsiteDataStore.allWebsiteDataTypes()
        await store.removeData(ofTypes: types, modifiedSince: .distantPast)
        let remaining = await store.dataRecords(ofTypes: types)
        return blanked && remaining.isEmpty
    }

    private var state: [String: Any] {
        var result: [String: Any] = [
            "status": status, "revision": pageRevision,
            "canGoBack": surface?.canGoBack == true, "canGoForward": surface?.canGoForward == true,
        ]
        result["tabs"] = tabs.map { tab in
            [
                "id": tab.id, "owner": "person", "selected": tab === selected, "status": tab.status,
                "url": tab.status == "unavailable" ? "" : tab.view?.url?.absoluteString ?? "",
                "title": tab.status == "unavailable" ? "" : String((tab.view?.title ?? "").prefix(200)),
            ] as [String: Any]
        }
        if let selected { result["selected"] = selected.id }
        if status != "unavailable", let url = surface?.url { result["url"] = url.absoluteString }
        if clearFailed { result["notice"] = "clear-failed" }
        return result
    }

    func reset() {
        generation += 1
        pageRevision &+= 1
        opening = false
        for tab in tabs { detach(tab) }
        tabs.removeAll()
        selected = nil
        frame = nil
        network?.close()
        network = nil
        configuration = nil
        emptyStatus = "empty"
    }

    @discardableResult
    private func addTab() -> Tab {
        let tab = Tab()
        tabs.append(tab)
        select(tab)
        return tab
    }

    private func select(_ tab: Tab?) {
        surface?.isHidden = true
        selected = tab
        pageRevision &+= 1
        emptyStatus = "empty"
        updateVisibility()
    }

    private func detach(_ tab: Tab) {
        guard let view = tab.view else { return }
        view.isHidden = true
        view.navigationDelegate = nil
        view.uiDelegate = nil
        view.stopLoading()
        view.removeFromSuperview()
        tab.urlObservation = nil
        tab.view = nil
    }

    private func hide() {
        frame = nil
        surface?.isHidden = true
    }

    private func updateVisibility() {
        guard let surface else { return }
        if let frame { surface.frame = frame }
        surface.isHidden = frame == nil || status == "unavailable"
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

    private func browser(in workspace: WKWebView, endpoint: Endpoint, revision: Int, tab: Tab) async throws -> WKWebView {
        if let view = tab.view { return view }
        if configuration == nil {
            guard try await Self.authenticated(in: workspace, for: endpoint) else {
                throw URLError(.userAuthenticationRequired)
            }
            guard revision == generation else { throw URLError(.cancelled) }
            let host = endpoint.origin.host()?.lowercased()
            let rules = try await Self.requestRules(excluding: host)
            guard revision == generation else { throw URLError(.cancelled) }
            let broker = try BrowserNetwork(deniedHost: host)
            network = broker
            do {
                let proxy = try await broker.start()
                guard revision == generation else { throw URLError(.cancelled) }
                configuration = Self.guestConfiguration(proxy: proxy, rules: rules)
            } catch {
                broker.close()
                if revision == generation { network = nil }
                throw error
            }
        }
        guard let configuration, tabs.contains(where: { $0 === tab }) else { throw URLError(.cancelled) }
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        view.isHidden = true
        // WebKit may omit navigation delegate completions for history restorations; URL KVO settles a stopped traversal.
        tab.urlObservation = view.observe(\.url, options: [.new]) { [weak self, tabID = tab.id] _, change in
            let changedURL = change.newValue ?? nil
            Task { @MainActor [weak self] in
                guard let self, let tab = self.tabs.first(where: { $0.id == tabID }), let view = tab.view else { return }
                if !view.isLoading, tab.navigationIdentity.finishSameDocument(url: changedURL ?? view.url) {
                    tab.status = "ready"
                }
                if tab === self.selected { self.pageRevision &+= 1 }
            }
        }
        workspace.addSubview(view)
        tab.view = view
        updateVisibility()
        return view
    }

    private static func authenticated(in workspace: WKWebView, for endpoint: Endpoint) async throws -> Bool {
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
            return Array.isArray(report?.settings);
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
        guard let endpoint else { return false }
        return Self.permits(url, excluding: endpoint)
    }

    static func permits(_ url: URL, excluding endpoint: Endpoint) -> Bool {
        guard url.scheme == "https", url.user == nil, url.password == nil,
              url.port == nil || url.port == 443, !endpoint.owns(url),
              let host = url.host(), host.lowercased() != endpoint.origin.host()?.lowercased(),
              BrowserAddress.host(host + ":443") != nil else { return false }
        return true
    }

    static func externalTarget(url: URL?, revision: Int, ready: Bool, endpoint: Endpoint) -> ExternalTarget? {
        guard ready, let url, permits(url, excluding: endpoint),
              ExternalLink.destination(url.absoluteString) == url else { return nil }
        return ExternalTarget(url: url, revision: revision)
    }

    func externalTarget() -> ExternalTarget? {
        guard let surface, let endpoint else { return nil }
        return Self.externalTarget(url: surface.url, revision: pageRevision,
                                   ready: status == "ready" && !guestClear.clearing && !clearFailed
                                       && !surface.isHidden && !surface.isLoading, endpoint: endpoint)
    }

    func isCurrent(_ target: ExternalTarget) -> Bool { externalTarget() == target }

    private func tab(for view: WKWebView) -> Tab? {
        tabs.first { $0.view === view }
    }

    private func markUnavailable(_ tab: Tab? = nil) {
        if let target = tab ?? selected {
            target.status = "unavailable"
            target.view?.isHidden = true
            if target === selected { pageRevision &+= 1 }
        } else {
            emptyStatus = "unavailable"
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        guard let tab = tab(for: webView), let frame = action.targetFrame else { return .cancel }
        guard action.request.url.map(permits) == true else {
            if frame.isMainFrame { markUnavailable(tab) }
            return .cancel
        }
        if frame.isMainFrame, let url = action.request.url {
            let pageInitiated = action.navigationType == .linkActivated || action.navigationType == .formSubmitted ||
                action.navigationType == .formResubmitted
            let newImplicitNavigation = !tab.navigationIdentity.expects(url) && !tab.navigationIdentity.inFlight
            if pageInitiated || newImplicitNavigation {
                let attempt = tab.navigationIdentity.begin(target: url)
                tab.navigationIdentity.attach(nil, to: attempt)
                if tab === selected {
                    pageRevision &+= 1
                    tab.status = "loading"
                }
            }
        }
        return .allow
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        guard let tab = tab(for: webView) else { return .cancel }
        guard response.response.url.map(permits) == true, response.canShowMIMEType else {
            if response.isForMainFrame { markUnavailable(tab) }
            return .cancel
        }
        return .allow
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        guard let tab = tab(for: webView) else { return }
        // Ignore starts from a replaced navigation; the current action token also identifies nil WebKit callbacks.
        guard tab.navigationIdentity.start(navigation, url: webView.url) else { return }
        if tab === selected { pageRevision &+= 1 }
        tab.status = "loading"
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let tab = tab(for: webView),
              tab.navigationIdentity.finish(navigation, url: webView.url) else { return }
        if tab.status == "loading" { tab.status = "ready" }
        if tab === selected { pageRevision &+= 1 }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, navigation: navigation, error: error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        navigationFailed(webView, navigation: navigation, error: error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        guard let tab = tab(for: webView) else { return }
        markUnavailable(tab)
        detach(tab)
        if tab === selected { pageRevision &+= 1 }
    }

    private func navigationFailed(_ view: WKWebView, navigation: WKNavigation!, error: Error) {
        guard let tab = tab(for: view),
              tab.navigationIdentity.finish(navigation, url: view.url) else { return }
        if (error as NSError).code == NSURLErrorCancelled {
            tab.status = "ready"
            if tab === selected { pageRevision &+= 1 }
            return
        }
        markUnavailable(tab)
    }

    func webView(
        _ webView: WKWebView, decideMediaCapturePermissionsFor origin: WKSecurityOrigin,
        initiatedBy frame: WKFrameInfo, type: WKMediaCaptureType
    ) async -> WKPermissionDecision { .deny }
}
