import SwiftUI
import WebKit

struct WorkspacePage: Identifiable {
    let endpoint: Endpoint
    let id: Int
}

/// A destination keeps its WebKit tab when it becomes the current workspace.
struct WorkspaceSwitchPages<PageContent: View>: View {
    let current: WorkspacePage?
    let pending: WorkspacePage?
    let obscured: Bool
    @ViewBuilder var content: (WorkspacePage, Bool) -> PageContent

    var body: some View {
        ZStack {
            ForEach([current, pending].compactMap { $0 }) { page in
                let hidden = obscured || page.id != current?.id
                content(page, hidden)
                    .opacity(hidden ? 0 : 1)
                    .allowsHitTesting(!hidden)
                    .accessibilityHidden(hidden)
            }
        }
    }
}

/// Older servers put Settings directly before Profile. Modern servers render their own
/// Workspaces item there. Handle only profile menu openings, including keyboard activation.
enum WorkspaceLegacyProfileMenu {
    static let script = """
        (() => {
            const workspaces = window.heinzelDesktop?.workspaces;
            if (!workspaces) return;
            document.addEventListener('click', event => {
                const trigger = event.target.closest?.('button[aria-haspopup="menu"]');
                if (!trigger) return;
                setTimeout(() => {
                    if (trigger.getAttribute('aria-expanded') !== 'true') return;
                    const scope = trigger.closest('dialog') || document;
                    for (const settings of scope.querySelectorAll('[role="menu"] > a[role="menuitem"]')) {
                        const profile = settings.nextElementSibling;
                        if (!new URL(settings.href).searchParams.has('settings') ||
                            !profile?.matches('a[role="menuitem"]') || new URL(profile.href).pathname !== '/profile') continue;
                        const item = document.createElement('button');
                        item.type = 'button';
                        item.className = settings.className;
                        item.setAttribute('role', 'menuitem');
                        item.setAttribute('aria-haspopup', 'dialog');
                        item.textContent = 'Workspaces';
                        settings.after(item);
                        workspaces.currentName().then(name => {
                            if (typeof name === 'string' && name.trim()) item.textContent = 'Workspaces · ' + name.trim();
                        }).catch(() => {});
                        item.onclick = async () => {
                            if (item.disabled) return;
                            item.disabled = true;
                            try {
                                if (await workspaces.open() && item.isConnected && trigger.getAttribute('aria-expanded') === 'true') {
                                    trigger.click();
                                    trigger.focus({ preventScroll: true });
                                }
                            } catch {} finally { item.disabled = false; }
                        };
                    }
                });
            }, true);
        })();
        """
}

/// Both the retained and staged pages can exist at once. Recovery probes the retained page only.
@MainActor final class WorkspacePageSessions {
    private let views = NSMapTable<NSNumber, WKWebView>.strongToWeakObjects()

    func register(_ view: WKWebView, page: Int) { views.setObject(view, forKey: NSNumber(value: page)) }
    func view(for page: Int) -> WKWebView? { views.object(forKey: NSNumber(value: page)) }
}

/// Poll only the active document. The owner invalidates the run on pause, foreground changes or a new selection.
@MainActor enum WorkspaceRemoteMonitor {
    enum Event {
        case healthy
        case lost(WorkspaceRemoteRecovery)
    }

    static func run(recovery: WorkspaceRemoteRecovery?, network: Bool?, isCurrent: () -> Bool,
                    probe: () async -> WorkspaceProblem?, report: (Event) -> Void,
                    jitter: () -> Double = { Double.random(in: 0.9...1.1) },
                    wait: (Duration) async throws -> Void = { try await Task.sleep(for: $0) }) async {
        var recovery = recovery
        var missed = 0
        while isCurrent() && !Task.isCancelled {
            if let recovery, recovery.paused || !recovery.issue.canReconnectAutomatically || network == false { return }
            let issue = await probe()
            guard isCurrent(), !Task.isCancelled else { return }
            let delay: Duration
            if let issue {
                missed += 1
                if missed == 1 && issue.canReconnectAutomatically && recovery == nil {
                    delay = .seconds(2)
                } else {
                    var lost = recovery ?? WorkspaceRemoteRecovery(issue: issue)
                    lost.issue = issue
                    let seconds = lost.nextDelay(jitter: jitter())
                    recovery = lost
                    report(.lost(lost))
                    guard network != false, issue.canReconnectAutomatically else { return }
                    delay = .seconds(seconds)
                }
            } else {
                missed = 0
                recovery = nil
                report(.healthy)
                delay = .seconds(12)
            }
            guard isCurrent(), !Task.isCancelled else { return }
            do { try await wait(delay) } catch { return }
        }
    }
}

@MainActor enum WorkspaceWebSession {
    /// Navigation can finish after the user goes back, chooses a third workspace, or the document reloads.
    static func verify(in view: WKWebView, for endpoint: Endpoint, isCurrent: () -> Bool,
                       completed: (WorkspaceProblem?) -> Void) async {
        let issue = await issue(in: view, for: endpoint)
        if isCurrent() { completed(issue) }
    }

    /// Only a verified recovery emits this signal. The page may refresh failed read queries without navigating.
    static func reportRestored(in view: WKWebView, for endpoint: Endpoint) {
        guard view.url.map(endpoint.owns) == true else { return }
        view.callAsyncJavaScript("""
            if (location.origin === new URL(origin).origin) window.dispatchEvent(new Event('hz:workspace-restored'));
            """, arguments: ["origin": endpoint.origin.absoluteString], in: nil, in: .page, completionHandler: nil)
    }

    /// A bounded, read-only check of the credentials and document in this particular tab.
    static func issue(in view: WKWebView, for endpoint: Endpoint,
                      requireRenderedShell: Bool = true) async -> WorkspaceProblem? {
        guard view.url.map(endpoint.owns) == true else { return .wrongAddress }
        let verdict = try? await view.callAsyncJavaScript("""
            if (location.origin !== new URL(origin).origin || (requireRenderedShell && !document.getElementById('root'))) return 'wrongAddress';
            const signal = AbortSignal.timeout(5000);
            const shell = () => document.querySelector('#root [data-testid="app-shell"]');
            const rendered = async () => {
                if (shell()) return true;
                await new Promise(resolve => {
                    const observer = new MutationObserver(() => { if (shell()) finish(); });
                    function finish() {
                        observer.disconnect();
                        signal.removeEventListener('abort', finish);
                        resolve();
                    }
                    observer.observe(document.getElementById('root'), {childList: true, subtree: true});
                    if (signal.aborted || shell()) finish();
                    else signal.addEventListener('abort', finish, {once: true});
                });
                return Boolean(shell());
            };
            const credentials = () => [...new Set([new URLSearchParams(location.hash.slice(1)).get('token'),
                sessionStorage.getItem('hz.token'), localStorage.getItem('hz.device')].filter(Boolean))];
            for (let attempt = 0; attempt < 2; attempt++) {
                const keys = credentials();
                let result = 'signIn';
                try {
                    for (const key of keys) {
                        const response = await fetch(new URL('/api/lifecycle', location.origin), {
                            headers: {Authorization: 'Bearer ' + key}, mode: 'same-origin',
                            credentials: 'omit', cache: 'no-store', redirect: 'error', signal
                        });
                        if (response.status === 401) continue;
                        if (response.status === 200) {
                            const lifecycle = await response.json();
                            result = typeof lifecycle.instance === 'string' && lifecycle.instance.length > 0 &&
                                typeof lifecycle.version === 'string' && lifecycle.version.length > 0 ? 'ready' : 'unavailable';
                        } else if (response.status === 403) result = 'denied';
                        else if (response.status === 404 || response.status === 410) result = 'wrongAddress';
                        else if (response.status >= 500) result = 'serviceUnavailable';
                        else result = 'unavailable';
                        break;
                    }
                    if (result === 'ready' && requireRenderedShell && !await rendered()) result = 'unavailable';
                } catch { result = 'unavailable'; }
                const current = credentials();
                if (keys.length === current.length && keys.every((key, index) => key === current[index])) return result;
            }
            return 'unavailable';
            """, arguments: ["origin": endpoint.origin.absoluteString, "requireRenderedShell": requireRenderedShell],
            in: nil, contentWorld: .defaultClient) as? String
        switch verdict {
        case "ready": return nil
        case "signIn": return .signIn
        case "denied": return .denied
        case "serviceUnavailable": return .serviceUnavailable
        case "wrongAddress": return .wrongAddress
        default: return .unavailable
        }
    }
}
