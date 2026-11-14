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
    var ready: () -> Void
    var reportError: (String) -> Void

    func makeCoordinator() -> Navigation { Navigation(endpoint: endpoint, ready: ready, reportError: reportError) }

    private func makeWebView(_ context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlaybackIfSupported()
        // Keep the native browser in debug builds until WebKit egress probes pass.
        #if DEBUG
        let browserEnabled = ProcessInfo.processInfo.environment["HZ_SPIKE_BROWSER"] == "1"
        #else
        let browserEnabled = false
        #endif
        if browserEnabled {
            configuration.userContentController.addScriptMessageHandler(
                context.coordinator, contentWorld: .page, name: "heinzelBrowser"
            )
        }
        var audioMember = ""
        #if canImport(ScreenCaptureKit)
        if #available(iOS 27, *) {
            configuration.userContentController.addScriptMessageHandler(
                context.coordinator, contentWorld: .page, name: "heinzelAudio"
            )
            audioMember = "systemAudio: () => audio('available'), nativeAudio: audio,"
        }
        #endif
        #if os(macOS)
        configuration.userContentController.addScriptMessageHandler(
            context.coordinator, contentWorld: .page, name: "heinzelWindow"
        )
        configuration.userContentController.addScriptMessageHandler(
            context.coordinator, contentWorld: .defaultClient, name: "heinzelDrag"
        )
        configuration.userContentController.addUserScript(WKUserScript(
            source: Self.windowDragScript, injectionTime: .atDocumentStart,
            forMainFrameOnly: true, in: .defaultClient
        ))
        let chrome = "titlebar: Object.freeze({ height: 44, left: 80, right: 0 }), onFullscreen,"
        #else
        let chrome = ""
        #endif
        let browserMember = browserEnabled ? "browser," : ""
        configuration.userContentController.addUserScript(WKUserScript(
            source: """
            (() => {
                const origin = \(Self.javascriptString(endpoint.origin.absoluteString));
                if (location.origin !== new URL(origin).origin) return;
                const onFullscreen = (callback) => {
                    let active = true;
                    let revision = 0;
                    const notify = async () => {
                        const request = ++revision;
                        try {
                            const full = await window.webkit.messageHandlers.heinzelWindow.postMessage("fullscreen");
                            if (active && request === revision) callback(full === true);
                        } catch {}
                    };
                    window.addEventListener("resize", notify);
                    window.addEventListener("heinzel-window-change", notify);
                    void notify();
                    return () => {
                        active = false;
                        window.removeEventListener("resize", notify);
                        window.removeEventListener("heinzel-window-change", notify);
                    };
                };
                const audio = action => {
                    if (action === "start" && !navigator.userActivation?.isActive) return Promise.resolve(false);
                    return window.webkit.messageHandlers.heinzelAudio.postMessage(action);
                };
                const browser = request => window.webkit.messageHandlers.heinzelBrowser.postMessage(request);
                Object.defineProperty(window, "heinzelDesktop", {
                    value: Object.freeze({ \(chrome) \(browserMember) \(audioMember) })
                });
            })();
            """,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        ))
        let view = WKWebView(frame: .zero, configuration: configuration)
        context.coordinator.workspace = view
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.allowsBackForwardNavigationGestures = true
        view.load(URLRequest(url: endpoint.url))
        return view
    }

    private static func javascriptString(_ value: String) -> String {
        guard let data = try? JSONEncoder().encode(value) else { return "null" }
        return String(decoding: data, as: UTF8.self)
    }

    #if os(macOS)
    private static let windowDragScript = """
    addEventListener("mousedown", event => {
        if (!event.isTrusted || event.button !== 0 || !document.documentElement.hasAttribute("data-desktop-titlebar")) return;
        const target = event.target;
        if (!(target instanceof Element)) return;
        if (!target.closest("[data-titlebar], .workspace-topbar, [data-placement='beside'] > .workspace-tabs, [data-placement='beside']:not(:has(> .workspace-tabs)) .chat-panel-header")) return;
        if (target.closest("a, button, input, select, textarea, [role='tab'], [role='menu'], [role='menuitem'], [tabindex], dialog, [role='dialog'], [aria-modal='true'], [role='listbox'], .viewport-popover, [data-pane], [data-sidebar-resizer]")) return;
        window.webkit.messageHandlers.heinzelDrag.postMessage("drag").catch(() => {});
    }, true);
    """

    func makeNSView(context: Context) -> WKWebView { makeWebView(context) }
    func updateNSView(_ view: WKWebView, context: Context) {}
    static func dismantleNSView(_ view: WKWebView, coordinator: Navigation) { coordinator.close(view) }
    #else
    func makeUIView(context: Context) -> WKWebView { makeWebView(context) }
    func updateUIView(_ view: WKWebView, context: Context) {}
    static func dismantleUIView(_ view: WKWebView, coordinator: Navigation) { coordinator.close(view) }
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
final class Navigation: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply {
    private let endpoint: Endpoint
    private let ready: () -> Void
    weak var workspace: WKWebView?
    private let browser = IntegratedBrowser()
    private var nativeAudio: AnyObject?
    #if os(macOS)
    private weak var observedWindow: NSWindow?
    #endif
    private let reportError: (String) -> Void

    init(endpoint: Endpoint, ready: @escaping () -> Void, reportError: @escaping (String) -> Void) {
        self.endpoint = endpoint
        self.ready = ready
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

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) async -> (Any?, String?) {
        guard message.frameInfo.isMainFrame, message.frameInfo.request.url.map(endpoint.owns) == true,
              let workspace, message.webView === workspace,
              workspace.url.map(endpoint.owns) == true else { return (nil, "Workspace command unavailable") }
        #if os(macOS)
        if message.name == "heinzelWindow", message.body as? String == "fullscreen" {
            observeWindow(workspace.window)
            return (workspace.window?.styleMask.contains(.fullScreen) == true, nil)
        }
        if message.name == "heinzelDrag", message.body as? String == "drag" {
            guard let window = workspace.window, !window.styleMask.contains(.fullScreen),
                  let event = NSApp.currentEvent, event.type == .leftMouseDown,
                  event.window === window else { return (false, nil) }
            window.performDrag(with: event)
            return (true, nil)
        }
        #endif
        #if canImport(ScreenCaptureKit)
        if #available(iOS 27, *), message.name == "heinzelAudio" {
            switch message.body as? String {
            case "available": return (SystemAudio.available, nil)
            case "start":
                guard SystemAudio.available, nativeAudio == nil else { return (false, nil) }
                let capture = SystemAudio()
                nativeAudio = capture
                let started = await capture.start()
                if !started, nativeAudio === capture { nativeAudio = nil }
                return (started, nil)
            case "read":
                return ((nativeAudio as? SystemAudio)?.read() as Any? ?? NSNull(), nil)
            case "stop":
                stopAudio()
                return (true, nil)
            default: return (nil, "Unknown audio command")
            }
        }
        #endif
        guard let request = message.body as? [String: Any] else { return (nil, "Browser command unavailable") }
        return (await browser.handle(request, in: workspace, excluding: endpoint), nil)
    }

    private func stopAudio() {
        #if canImport(ScreenCaptureKit)
        if #available(iOS 27, *) { (nativeAudio as? SystemAudio)?.stop() }
        #endif
        nativeAudio = nil
    }

    #if os(macOS)
    private func observeWindow(_ window: NSWindow?) {
        guard observedWindow !== window else { return }
        let center = NotificationCenter.default
        center.removeObserver(self)
        observedWindow = window
        guard let window else { return }
        for name in [NSWindow.didEnterFullScreenNotification, NSWindow.didExitFullScreenNotification] {
            center.addObserver(self, selector: #selector(windowChanged), name: name, object: window)
        }
    }

    @objc private func windowChanged() {
        workspace?.evaluateJavaScript("window.dispatchEvent(new Event('heinzel-window-change'))")
    }
    #endif

    func close(_ view: WKWebView) {
        #if os(macOS)
        observeWindow(nil)
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelWindow", contentWorld: .page)
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelDrag", contentWorld: .defaultClient)
        #endif
        stopAudio()
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelAudio", contentWorld: .page)
        browser.reset()
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelBrowser", contentWorld: .page)
        view.stopLoading()
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        stopAudio()
        browser.reset()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        ready()
        #if DEBUG
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
        #endif
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        pageFailed(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        pageFailed(error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        stopAudio()
        reportError("The workspace closed unexpectedly. Reconnect to open it again.")
    }

    private func pageFailed(_ error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        ready()
        reportError("The workspace could not load. Check that the server is running and reachable.")
    }
}
