import AppKit
import WebKit

@MainActor private final class FactsNavigation: NSObject, WKNavigationDelegate {
    let origin: URL
    init(origin: URL) { self.origin = origin }

    private func permits(_ url: URL?) -> Bool {
        guard let url else { return false }
        return url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port &&
            url.user == nil && url.password == nil
    }

    func webView(_: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        permits(action.request.url) ? .allow : .cancel
    }

    func webView(_: WKWebView, decidePolicyFor response: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        permits(response.response.url) ? .allow : .cancel
    }
}

@main @MainActor struct WorkspaceDocumentFactsTests {
    static func wait(line: UInt = #line, _ ready: () async throws -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(8)
        while try await !ready() {
            if ContinuousClock.now >= deadline { throw NSError(domain: "WorkspaceDocumentFactsTests.wait", code: Int(line)) }
            try await Task.sleep(for: .milliseconds(20))
        }
    }

    static func main() async throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        let origin = URL(string: CommandLine.arguments[1])!
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: configuration)
        let navigation = FactsNavigation(origin: origin)
        view.navigationDelegate = navigation
        defer { withExtendedLifetime(navigation) {} }
        view.load(URLRequest(url: origin))
        try await wait { view.url?.port == origin.port && !view.isLoading }
        func script(_ source: String) async throws -> Any? {
            try await view.callAsyncJavaScript(source, arguments: [:], in: nil, contentWorld: .defaultClient)
        }
        let _: Any? = try await script("""
            sessionStorage.setItem('hz.token', 'first');
            globalThis.held = '';
            globalThis.legacyHost = false;
            globalThis.reads = [];
            globalThis.fetch = async (path, options) => {
                const name = options.headers.Authorization.slice(7);
                reads.push([path, name]);
                if (legacyHost && path === '/api/workspace-version') return new Response(null, {status: 404});
                if (name === 'expired') return new Response(null, {status: 401});
                if (name === 'denied') return new Response(null, {status: 403});
                if (name === held) await new Promise(resolve => { globalThis.finishReply = resolve; });
                if (path === '/api/system/info') return new Response(JSON.stringify({version: name}));
                return new Response(JSON.stringify({
                    installed: {version: name + '-installed'}, observed_at: '2026-01-02T03:04:05Z',
                    web: {version: name, backend_ip: name === 'first' ? '192.0.2.7' : '2001:db8::7',
                          running_since: name === 'first' ? '2026-01-02T02:04:05Z' : '2026-01-02T03:03:05Z'}
                }), {status: 200});
            };
            """)
        let authority = await WorkspaceDocumentFacts.authority(in: view, origin: origin)
        precondition(authority?.count == 64 && authority != "first")
        let model = WorkspaceInformationFacts()
        let target = WorkspaceInformationRequest(origin: origin, page: 1, networkRevision: 1, remoteAddress: nil, clientAddress: nil)
        var selected: WKWebView? = view
        let showing = Task { await model.observe(target, view: { selected }) }
        defer { showing.cancel() }
        try await wait { model.snapshot?.version.installedVersion == "first-installed" }
        precondition(model.snapshot?.version.uptimeAtObservation == 3600 && model.snapshot?.version.backendIP == "192.0.2.7")

        let _: Any? = try await script("held = 'first';")
        let delayed = Task { await WorkspaceDocumentFacts.version(in: view, origin: origin, authority: authority) }
        try await wait { try await script("return typeof finishReply === 'function';") as? Bool == true }
        let _: Any? = try await script("""
            globalThis.firstReply = finishReply; delete globalThis.finishReply;
            held = 'second'; sessionStorage.setItem('hz.token', 'second');
            """)
        try await wait { try await script("return typeof finishReply === 'function';") as? Bool == true }
        precondition(model.snapshot == nil, "Information showed the old IP, uptime or version while the replacement was pending")
        let _: Any? = try await script("firstReply();")
        let stale = await delayed.value
        precondition(stale == nil, "A held reply crossed the authority change")
        let _: Any? = try await script("finishReply();")
        try await wait { model.snapshot?.version.version == "second" }
        precondition(model.snapshot?.version.installedVersion == "second-installed" &&
                     model.snapshot?.version.backendIP == "2001:db8::7" && model.snapshot?.version.uptimeAtObservation == 60)

        let _: Any? = try await script("sessionStorage.removeItem('hz.token');")
        try await wait { model.snapshot == nil }
        let _: Any? = try await script("""
            sessionStorage.setItem('hz.token', 'expired');
            localStorage.setItem('hz.device', 'paired');
            """)
        try await wait { model.snapshot?.version.version == "paired" }
        let retained = try await script("return sessionStorage.getItem('hz.token') === 'expired';") as? Bool
        precondition(retained == true, "Reading Information must not modify the idle page's session")

        let _: Any? = try await script("""
            held = 'paired'; delete globalThis.finishReply;
            """)
        let oldPair = Task { await WorkspaceDocumentFacts.version(in: view, origin: origin) }
        try await wait { try await script("return typeof finishReply === 'function';") as? Bool == true }
        let _: Any? = try await script("""
            held = ''; legacyHost = true; localStorage.setItem('hz.device', 'legacy'); finishReply();
            """)
        let supersededPair = await oldPair.value
        precondition(supersededPair == nil, "The stale primary must not mask a paired credential change")
        try await wait { model.snapshot?.version.version == "legacy" }
        precondition(model.snapshot?.version.installedVersion == nil && model.snapshot?.version.backendIP == nil)

        let _: Any? = try await script("legacyHost = false; reads = []; sessionStorage.setItem('hz.token', 'denied');")
        try await wait { model.snapshot == nil }
        _ = await WorkspaceDocumentFacts.version(in: view, origin: origin)
        let retriedDenial = try await script("return reads.some(([, name]) => name === 'legacy');") as? Bool
        precondition(retriedDenial == false, "A forbidden response must not fall through to another credential")
        let _: Any? = try await script("sessionStorage.removeItem('hz.token'); legacyHost = true;")
        try await wait { model.snapshot?.version.version == "legacy" }
        selected = nil
        try await wait { model.snapshot == nil }
        selected = view
        try await wait { model.snapshot?.version.version == "legacy" }
        let paired = await WorkspaceDocumentFacts.authority(in: view, origin: origin)
        view.reload()
        try await wait { !view.isLoading }
        try await wait { await WorkspaceDocumentFacts.authority(in: view, origin: origin) != paired }
        try await wait { model.snapshot == nil }
        print("PASS: delayed facts, open Information, sign-out, pairing and same-origin document replacement")
    }
}
