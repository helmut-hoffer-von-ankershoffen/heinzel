import AppKit
import SwiftUI
import WebKit

@MainActor @Observable private final class SwitchFixture {
    var current: WorkspacePage?
    var pending: WorkspacePage?
    @ObservationIgnored var tabs: [Int: WKWebView] = [:]
    @ObservationIgnored var loaded: Set<Int> = []
    @ObservationIgnored var failed: Set<Int> = []
    let store = WKWebsiteDataStore.nonPersistent()

    init(_ initial: WorkspacePage?) { current = initial }
}

@MainActor private struct FixtureTab: NSViewRepresentable {
    let page: WorkspacePage
    let fixture: SwitchFixture

    final class Loading: NSObject, WKNavigationDelegate {
        let fixture: SwitchFixture
        let id: Int
        let origin: URL
        init(_ fixture: SwitchFixture, id: Int, origin: URL) {
            self.fixture = fixture; self.id = id; self.origin = origin
        }
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
        func webView(_ view: WKWebView, didFinish navigation: WKNavigation!) { fixture.loaded.insert(id) }
        func webView(_ view: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            fixture.failed.insert(id)
        }
    }

    func makeCoordinator() -> Loading { Loading(fixture, id: page.id, origin: page.endpoint.origin) }
    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = fixture.store
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        precondition(fixture.tabs[page.id] == nil, "A loaded tab was recreated")
        fixture.tabs[page.id] = view
        view.load(URLRequest(url: page.endpoint.origin))
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {}
}

@MainActor private struct FixtureStack: View {
    let fixture: SwitchFixture
    var body: some View {
        WorkspaceSwitchPages(current: fixture.current, pending: fixture.pending, obscured: false) { page, _ in
            FixtureTab(page: page, fixture: fixture)
        }
    }
}

@MainActor private final class ScriptOnlyNavigation: NSObject, WKNavigationDelegate {
    func webView(_: WKWebView, decidePolicyFor _: WKNavigationAction) async -> WKNavigationActionPolicy {
        .cancel
    }
}

@main struct WorkspaceSwitchPagesTests {
    @MainActor static func wait(_ predicate: () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(8)
        while !predicate() {
            guard ContinuousClock.now < deadline else { throw URLError(.timedOut) }
            try await Task.sleep(for: .milliseconds(10))
        }
    }

    @MainActor static func verifyRemoteMonitor() async {
        var script: [WorkspaceProblem?] = [.unavailable, nil, .serviceUnavailable, .unavailable, .unavailable, nil]
        var labels: [String] = []
        var delays: [Duration] = []
        var active = true
        await WorkspaceRemoteMonitor.run(recovery: nil, network: true, isCurrent: { active },
            probe: { script.removeFirst() }, report: { event in
                switch event {
                case .healthy: labels.append("healthy")
                case .lost(let lost): labels.append(lost.label(network: true))
                }
            }, jitter: { 1 }, wait: { delay in
                delays.append(delay)
                active = !script.isEmpty
            })
        precondition(labels == ["healthy", "Reconnecting…", "Reconnecting…", "healthy"])
        precondition(delays == [.seconds(2), .seconds(12), .seconds(2), .zero, .seconds(5), .seconds(12)])
        for (problem, expected) in [(WorkspaceProblem.signIn, "Sign in"), (.denied, "Access denied")] {
            await WorkspaceRemoteMonitor.run(recovery: nil, network: true, isCurrent: { true },
                probe: { problem }, report: { event in
                    guard case .lost(let state) = event else { preconditionFailure("Authentication failure treated as healthy") }
                    precondition(state.label(network: true) == expected)
                }, wait: { _ in preconditionFailure("A rejected credential scheduled another attempt") })
        }
        var state = WorkspaceRemoteRecovery(issue: .unavailable)
        precondition(state.label(network: false) == "Waiting for network")
        for value in [0, 5, 10, 20, 40, 40] { precondition(state.nextDelay(jitter: 1) == Double(value)) }
        state.togglePause()
        precondition(state.label(network: true) == "Paused")
        for path in [true, false, true] {
            await WorkspaceRemoteMonitor.run(recovery: state, network: path, isCurrent: { true },
                probe: { preconditionFailure("Paused monitor issued a request") }, report: { _ in preconditionFailure() })
        }
        state.togglePause()
        await WorkspaceRemoteMonitor.run(recovery: state, network: false, isCurrent: { true },
            probe: { preconditionFailure("Missing path issued a request") }, report: { _ in preconditionFailure() })
        active = true
        await WorkspaceRemoteMonitor.run(recovery: state, network: true, isCurrent: { active },
            probe: { active = false; return nil }, report: { _ in preconditionFailure("Stale completion accepted") })
    }

    @MainActor static func verifyLegacyProfileMenu() async throws {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: configuration)
        let navigation = ScriptOnlyNavigation()
        view.navigationDelegate = navigation
        defer { withExtendedLifetime(navigation) {} }
        _ = try await view.evaluateJavaScript("""
            window.calls = 0;
            window.allowOpen = false;
            window.heinzelDesktop = {workspaces: {
                currentName: async () => 'Studio',
                open: async () => { calls++; return allowOpen; }
            }};
            document.body.innerHTML = '<dialog open><button aria-haspopup="menu" aria-expanded="false">Profile</button></dialog>';
            window.trigger = document.querySelector('button');
            trigger.onclick = () => {
                const shown = trigger.getAttribute('aria-expanded') === 'true';
                trigger.setAttribute('aria-expanded', String(!shown));
                if (shown) document.querySelector('[role=menu]').remove();
                else trigger.insertAdjacentHTML('afterend',
                    '<div role="menu"><a role="menuitem" href="https://test.invalid/?settings=">Settings</a>' +
                    (window.modern ? '<button role="menuitem">Workspaces</button>' : '') +
                    '<a role="menuitem" href="https://test.invalid/profile">Profile</a></div>');
            };
            void 0;
            """)
        _ = try await view.evaluateJavaScript(WorkspaceLegacyProfileMenu.script)
        let result: Any? = try await view.callAsyncJavaScript("""
            const tick = () => new Promise(resolve => setTimeout(resolve, 10));
            trigger.click();
            await tick();
            const item = document.querySelector('[role=menu] button');
            if (item?.textContent !== 'Workspaces · Studio' || item.getAttribute('aria-haspopup') !== 'dialog') return document.body.innerHTML;
            item.click(); item.click();
            await tick();
            if (calls !== 1 || item.disabled || !item.isConnected) return 'Denied open calls: ' + calls;
            allowOpen = true;
            item.click(); await tick();
            if (calls !== 2 || trigger.getAttribute('aria-expanded') !== 'false') return 'Accepted open calls: ' + calls;
            window.modern = true;
            trigger.click(); await tick();
            return document.querySelectorAll('[role=menu] button').length === 1 &&
                document.querySelector('[role=menu] button').textContent === 'Workspaces';
            """, arguments: [:], in: nil, contentWorld: .page)
        precondition(result as? Bool == true, "Invalid profile menu: \(String(describing: result))")
    }

    @MainActor static func verifySupersededLifecycle(in view: WKWebView, for endpoint: Endpoint) async throws {
        for status in [401, 403, 404, 410, 503, 200, 0] {
            _ = try await view.callAsyncJavaScript("""
                window.originalFetch = window.fetch;
                sessionStorage.setItem('hz.token', 'previous');
                window.probes = 0;
                window.fetch = async (_, options) => {
                    window.probes++;
                    if (options.headers.Authorization === 'Bearer previous') {
                        sessionStorage.setItem('hz.token', 'current');
                        if (!status) throw new TypeError('Fixture offline');
                        return new Response('invalid', {status});
                    }
                    return Response.json({instance: 'current-instance', version: '0.9.58'});
                };
                """, arguments: ["status": status], in: nil, contentWorld: .defaultClient)
            let issue = await WorkspaceWebSession.issue(in: view, for: endpoint)
            let count = try await view.callAsyncJavaScript("""
                window.fetch = window.originalFetch;
                return window.probes;
                """, arguments: [:], in: nil, contentWorld: .defaultClient) as? Int
            precondition(issue == nil && count == 2, "A superseded lifecycle reply changed the active workspace")
        }
    }

    @MainActor static func main() async throws {
        if CommandLine.arguments[1] == "remote-monitor" {
            await verifyRemoteMonitor()
            return
        }
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        if CommandLine.arguments[1] == "legacy-menu" {
            try await verifyLegacyProfileMenu()
            return
        }
        let emptyShell = CommandLine.arguments[1] == "empty-shell"
        let pages = CommandLine.arguments.dropFirst(2).enumerated().map { index, address in
            WorkspacePage(endpoint: Endpoint(address + "/#token=" + String(repeating: "x", count: 40), loopback: true)!, id: index)
        }
        let recover = CommandLine.arguments[1] == "recover-draft"
        let rotate = CommandLine.arguments[1] == "rotated-lifecycle"
        precondition(pages.count == (recover || rotate ? 1 : 8))
        let firstOpen = CommandLine.arguments[1] == "first-open"
        let fixture = SwitchFixture(firstOpen ? nil : pages[0])
        let host = NSHostingView(rootView: FixtureStack(fixture: fixture))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 320),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = host
        if rotate {
            try await wait { fixture.loaded.contains(0) }
            try await verifySupersededLifecycle(in: fixture.tabs[0]!, for: pages[0].endpoint)
            return
        }
        if recover {
            try await wait { fixture.loaded.contains(0) }
            let view = fixture.tabs[0]!
            _ = try await view.evaluateJavaScript("""
                sessionStorage.setItem('hz.token', 'fixture-value');
                history.pushState(null, '', '/draft');
                document.querySelector('textarea').value = 'An unsent note';
                window.restoration = new Promise(resolve => addEventListener('hz:workspace-restored', () => resolve(true), { once: true }));
                void 0;
                """)
            var recovery: WorkspaceRemoteRecovery?
            var active = true
            var events: [String] = []
            await WorkspaceRemoteMonitor.run(recovery: nil, network: true, isCurrent: { active }, probe: {
                await WorkspaceWebSession.issue(in: view, for: pages[0].endpoint, requireRenderedShell: recovery != nil)
            }, report: { result in
                switch result {
                case .healthy:
                    events.append("healthy")
                    if recovery != nil {
                        WorkspaceWebSession.reportRestored(in: view, for: pages[0].endpoint)
                        active = false
                    }
                case .lost(let state):
                    recovery = state
                    events.append(state.label(network: true))
                }
            }, jitter: { 1 }, wait: { _ in })
            precondition(events == ["healthy", "Unavailable · Reconnecting…", "healthy"])
            let restored = try await view.callAsyncJavaScript("""
                return await Promise.race([window.restoration, new Promise(resolve => setTimeout(() => resolve(false), 2000))]);
                """, arguments: [:], in: nil, contentWorld: .page) as? Bool
            precondition(restored == true)
            let draft = try await view.evaluateJavaScript("location.pathname + ' ' + document.querySelector('textarea').value") as? String
            precondition(fixture.tabs[0] === view && draft == "/draft An unsent note")
            return
        }
        host.layoutSubtreeIfNeeded()
        if firstOpen {
            // HTTP can reach a live host which refuses access or whose service is unhealthy.
            for (index, expected) in [(0, nil), (3, WorkspaceProblem.serviceUnavailable), (7, .denied)] {
                let probe = await Endpoint.probeHost(at: pages[index].endpoint.origin)
                precondition(probe.reachability == .online && probe.issue == expected)
                precondition(probe.address == "127.0.0.1" && probe.clientAddress == "127.0.0.1")
                if expected == .serviceUnavailable { precondition(probe.issue?.title == "Workspace service unavailable") }
            }
            fixture.pending = pages[1]
            try await wait { fixture.loaded.contains(1) }
            let candidate = fixture.tabs[1]!
            let rejected = await WorkspaceWebSession.issue(in: candidate, for: pages[1].endpoint)
            precondition(rejected == .signIn && fixture.current == nil)
            fixture.pending = nil
            try await wait { !candidate.isDescendant(of: host) }
            fixture.pending = pages[6]
            try await wait { fixture.loaded.contains(6) }
            let signedIn = fixture.tabs[6]!
            _ = try await signedIn.evaluateJavaScript("sessionStorage.setItem('hz.token', 'fixture-value')")
            await WorkspaceWebSession.verify(in: signedIn, for: pages[6].endpoint) {
                fixture.pending?.id == pages[6].id
            } completed: { issue in
                precondition(issue == nil && fixture.current == nil)
                fixture.current = fixture.pending
                fixture.pending = nil
            }
            host.layoutSubtreeIfNeeded()
            try await wait { fixture.current?.id == 6 && signedIn.isDescendant(of: host) }
            precondition(fixture.tabs[6] === signedIn)
            print("PASS: first open rejects unsigned workspace and promotes the signed-in tab in place")
            return
        }
        try await wait { fixture.loaded.contains(0) }
        let original = fixture.tabs[0]!
        _ = try await original.evaluateJavaScript("document.querySelector('textarea').value = 'Keep draft';")

        for id in (emptyShell ? [5] : [1, 2, 3, 4, 7, 6]) {
            if id == 6 {
                // Supersede a signed-in tab before its application shell is ready.
                let delayed = WorkspacePage(endpoint: pages[5].endpoint, id: pages.count)
                fixture.pending = delayed
                try await wait { fixture.loaded.contains(delayed.id) }
                let delayedView = fixture.tabs[delayed.id]!
                _ = try await delayedView.evaluateJavaScript("localStorage.setItem('hz.device', 'fixture-value');")
                var started = false
                var receivedObsoleteResult = false
                let verification = Task { @MainActor in
                    started = true
                    await WorkspaceWebSession.verify(in: delayedView, for: delayed.endpoint) {
                        fixture.pending?.id == delayed.id
                    } completed: { _ in receivedObsoleteResult = true }
                }
                try await wait { started }
                fixture.pending = pages[id]
                _ = try await delayedView.evaluateJavaScript("document.getElementById('root').innerHTML = '<main data-testid=app-shell></main>';")
                await verification.value
                precondition(!receivedObsoleteResult && fixture.current?.id == pages[0].id)
                let draft = try await original.evaluateJavaScript("document.querySelector('textarea').value")
                precondition(draft as? String == "Keep draft")
            }
            fixture.pending = pages[id]
            try await wait { fixture.loaded.contains(id) || fixture.failed.contains(id) }
            let candidate = fixture.tabs[id]!
            if id == 4 {
                precondition(fixture.failed.contains(id))
            } else {
                if id != 1 {
                    _ = try await candidate.evaluateJavaScript("localStorage.setItem('hz.device', 'fixture-value');")
                }
                if id == 6 {
                    _ = try await candidate.evaluateJavaScript("sessionStorage.setItem('hz.token', 'expired-fixture');")
                }
                if emptyShell {
                    // Advance the render deadline when the real verifier starts observing the empty root.
                    // Hidden WebKit tabs can suspend wall-clock JS timers while the test window is offscreen.
                    _ = try await candidate.evaluateJavaScript("""
                        const deadline = new AbortController();
                        AbortSignal.timeout = () => deadline.signal;
                        const observe = MutationObserver.prototype.observe;
                        MutationObserver.prototype.observe = function(...args) {
                            observe.apply(this, args);
                            queueMicrotask(() => deadline.abort());
                        };
                        """, in: nil, in: .defaultClient)
                }
                let issue = await WorkspaceWebSession.issue(in: candidate, for: pages[id].endpoint)
                switch id {
                case 1, 2: precondition(issue == .signIn)
                case 3: precondition(issue == .serviceUnavailable && issue?.revokesAccess == false)
                case 5: precondition(issue == .unavailable && issue?.revokesAccess == false)
                case 7: precondition(issue == .denied && issue?.revokesAccess == true)
                default: precondition(issue == nil)
                }
            }
            if id == 6 { fixture.current = pages[id] }
            fixture.pending = nil
            host.layoutSubtreeIfNeeded()
            let activeView = id == 6 ? candidate : original
            let removedView = id == 6 ? original : candidate
            try await wait { activeView.isDescendant(of: host) && !removedView.isDescendant(of: host) }
            if id != 6 {
                let draft = try await original.evaluateJavaScript("document.querySelector('textarea').value")
                precondition(draft as? String == "Keep draft")
            }
        }
        print("PASS: missing or revoked sign-in, unavailable host, failed navigation, empty shell and a superseded destination retain the draft; the signed-in destination keeps its tab")
    }
}
