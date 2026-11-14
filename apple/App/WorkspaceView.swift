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
        configuration.userContentController.addScriptMessageHandler(
            context.coordinator, contentWorld: .defaultClient, name: "heinzelExternalLink"
        )
        configuration.userContentController.addUserScript(WKUserScript(
            source: Self.externalLinkScript, injectionTime: .atDocumentStart,
            forMainFrameOnly: true, in: .defaultClient
        ))
        configuration.userContentController.addScriptMessageHandler(
            context.coordinator, contentWorld: .page, name: "heinzelBrowser"
        )
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
        // The bundle path belongs to this Mac and is only shared with the local service it launched.
        let about = endpoint.origin.scheme == "http"
            ? "about: () => window.webkit.messageHandlers.heinzelWindow.postMessage('about'),"
            : ""
        let chrome = "titlebar: Object.freeze({ height: 44, left: 80, right: 0 }), onFullscreen, \(about)"
        #else
        let chrome = ""
        #endif
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
                    value: Object.freeze({ \(chrome) browser, \(audioMember) })
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

    // An isolated listener can distinguish an actual input event from a page calling click().
    private static let externalLinkScript = """
    const openExternal = event => {
        if (!event.isTrusted || ![0, 1].includes(event.button) || event.defaultPrevented) return;
        const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
        if (!(anchor instanceof HTMLAnchorElement) || anchor.hasAttribute("download")) return;
        const target = new URL(anchor.href);
        if (target.origin === location.origin || !["https:", "http:", "mailto:"].includes(target.protocol)) return;
        event.preventDefault();
        window.webkit.messageHandlers.heinzelExternalLink.postMessage(target.href).catch(() => {});
    };
    addEventListener("click", openExternal);
    addEventListener("auxclick", openExternal);
    """

    private static func javascriptString(_ value: String) -> String {
        guard let data = try? JSONEncoder().encode(value) else { return "null" }
        return String(decoding: data, as: UTF8.self)
    }

    #if os(macOS)
    private static let windowDragScript = """
    (() => {
        const bars = "[data-titlebar], .workspace-topbar, [data-placement='beside'] > .workspace-tabs, [data-placement='beside']:not(:has(> .workspace-tabs)) .chat-panel-header";
        const controls = "a, button, input, select, textarea, [role='tab'], [role='menuitem'], [tabindex]";
        const overlays = "[role='dialog'], [role='menu'], [role='listbox'], .viewport-overlay, .viewport-popover, [data-pane], [data-sidebar-resizer]";
        const rectangles = elements => Array.from(elements).flatMap(element =>
            Array.from(element.getClientRects(), ({ x, y, width, height }) => [x, y, width, height]));
        let last = "";
        let frame = 0;
        let observed = [];
        const update = () => {
            frame = 0;
            const modal = Array.from(document.querySelectorAll("dialog[open], [aria-modal='true']"))
                .some(element => element.getClientRects().length && !element.closest("[hidden], [inert]"));
            const draggable = !modal && document.documentElement.hasAttribute("data-desktop-titlebar")
                ? Array.from(document.querySelectorAll(bars)) : [];
            if (draggable.length !== observed.length || draggable.some((bar, index) => bar !== observed[index])) {
                sizes.disconnect();
                draggable.forEach(bar => sizes.observe(bar));
                observed = draggable;
            }
            const excluded = draggable.flatMap(bar => Array.from(bar.querySelectorAll(controls)));
            if (draggable.length) excluded.push(...document.querySelectorAll(overlays));
            const layout = {
                width: document.documentElement.clientWidth,
                regions: rectangles(draggable),
                excluded: rectangles(excluded),
            };
            const snapshot = JSON.stringify(layout);
            if (last === snapshot) return;
            last = snapshot;
            window.webkit.messageHandlers.heinzelDrag.postMessage(layout).catch(() => {});
        };
        const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
        const sizes = new ResizeObserver(schedule);
        new MutationObserver(schedule).observe(document, { subtree: true, attributes: true, childList: true });
        addEventListener("resize", schedule);
        addEventListener("scroll", schedule, true);
        addEventListener("transitionend", schedule, true);
        addEventListener("transitioncancel", schedule, true);
        document.fonts.ready.then(schedule);
        schedule();
    })();
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

#if os(macOS)
private struct TitleBarDragLayout {
    let width: CGFloat
    let regions: [CGRect]
    let excluded: [CGRect]

    init?(_ message: Any) {
        guard let layout = message as? [String: Any],
              let width = layout["width"] as? Double, width.isFinite, width > 0,
              let regions = layout["regions"] as? [[Double]],
              let excluded = layout["excluded"] as? [[Double]] else { return nil }
        self.width = width
        self.regions = regions.compactMap(Self.rectangle)
        self.excluded = excluded.compactMap(Self.rectangle)
    }

    private static func rectangle(_ values: [Double]) -> CGRect? {
        guard values.count == 4, values.allSatisfy(\.isFinite), values[2] > 0, values[3] > 0 else { return nil }
        return CGRect(x: values[0], y: values[1], width: values[2], height: values[3])
    }

    @MainActor
    func contains(_ event: NSEvent, in view: WKWebView) -> Bool {
        guard let window = view.window, view.bounds.width > 0 else { return false }
        for role in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] {
            if let button = window.standardWindowButton(role), !button.isHiddenOrHasHiddenAncestor,
               button.bounds.contains(button.convert(event.locationInWindow, from: nil)) { return false }
        }
        let local = view.convert(event.locationInWindow, from: nil)
        guard view.bounds.contains(local) else { return false }
        let scale = width / view.bounds.width
        let y = view.isFlipped ? local.y - view.bounds.minY : view.bounds.maxY - local.y
        let point = CGPoint(x: (local.x - view.bounds.minX) * scale, y: y * scale)
        return regions.contains { $0.contains(point) } && !excluded.contains { $0.contains(point) }
    }
}
#endif

@MainActor
final class Navigation: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply {
    private let endpoint: Endpoint
    private let ready: () -> Void
    weak var workspace: WKWebView?
    private let browser = IntegratedBrowser()
    private var nativeAudio: AnyObject?
    private var lastExternalOpen: ContinuousClock.Instant?
    #if os(macOS)
    private weak var observedWindow: NSWindow?
    private var mouseMonitor: Any?
    private var dragLayout: TitleBarDragLayout?
    #endif
    private let reportError: (String) -> Void

    init(endpoint: Endpoint, ready: @escaping () -> Void, reportError: @escaping (String) -> Void) {
        self.endpoint = endpoint
        self.ready = ready
        self.reportError = reportError
        super.init()
        #if os(macOS)
        NotificationCenter.default.addObserver(
            self, selector: #selector(menuAction), name: WorkspaceMenuAction.notification, object: nil
        )
        #endif
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
        if message.name == "heinzelExternalLink" {
            guard let value = message.body as? String, let target = ExternalLink.destination(value),
                  !endpoint.owns(target) else { return (false, nil) }
            let now = ContinuousClock.now
            if let lastExternalOpen, lastExternalOpen.duration(to: now) < .seconds(2) { return (false, nil) }
            lastExternalOpen = now
            #if os(macOS)
            return (NSWorkspace.shared.open(target), nil)
            #else
            return (await UIApplication.shared.open(target), nil)
            #endif
        }
        #if os(macOS)
        if message.name == "heinzelWindow", message.body as? String == "fullscreen" {
            observeWindow(workspace.window)
            return (workspace.window?.styleMask.contains(.fullScreen) == true, nil)
        }
        if message.name == "heinzelWindow", message.body as? String == "about" {
            guard endpoint.origin.scheme == "http",
                  let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
            else { return (nil, "App details unavailable") }
            return (["version": version, "path": Bundle.main.bundleURL.path], nil)
        }
        if message.name == "heinzelDrag" {
            observeWindow(workspace.window)
            dragLayout = TitleBarDragLayout(message.body)
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
        guard observedWindow !== window || (window == nil && mouseMonitor != nil) else { return }
        let center = NotificationCenter.default
        if let observedWindow { center.removeObserver(self, name: nil, object: observedWindow) }
        if let mouseMonitor { NSEvent.removeMonitor(mouseMonitor) }
        mouseMonitor = nil
        observedWindow = window
        guard let window else { return }
        for name in [NSWindow.didEnterFullScreenNotification, NSWindow.didExitFullScreenNotification] {
            center.addObserver(self, selector: #selector(windowChanged), name: name, object: window)
        }
        // Use WebKit's last layout while AppKit is still handling the original mouse-down.
        mouseMonitor = NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) { [weak self] event in
            guard let self, let window = self.observedWindow, event.window === window,
                  !window.styleMask.contains(.fullScreen), window.attachedSheet == nil,
                  let view = self.workspace, view.window === window,
                  let layout = self.dragLayout, layout.contains(event, in: view) else { return event }
            window.performDrag(with: event)
            return nil
        }
    }

    @objc private func windowChanged() {
        workspace?.evaluateJavaScript("window.dispatchEvent(new Event('heinzel-window-change'))")
    }

    @objc private func menuAction(_ notification: Notification) {
        guard let action = notification.object as? WorkspaceMenuAction,
              let workspace, workspace.window?.isKeyWindow == true,
              workspace.url.map(endpoint.owns) == true else { return }
        switch action {
        case .reload: workspace.reload()
        case .zoomIn: workspace.pageZoom = min(workspace.pageZoom * 1.2, 3)
        case .zoomOut: workspace.pageZoom = max(workspace.pageZoom / 1.2, 0.5)
        case .resetZoom: workspace.pageZoom = 1
        default:
            workspace.evaluateJavaScript("window.dispatchEvent(new CustomEvent('hz:menu', { detail: '\(action.rawValue)' }))")
        }
    }
    #endif

    func close(_ view: WKWebView) {
        view.configuration.userContentController.removeScriptMessageHandler(
            forName: "heinzelExternalLink", contentWorld: .defaultClient
        )
        #if os(macOS)
        observeWindow(nil)
        NotificationCenter.default.removeObserver(self, name: WorkspaceMenuAction.notification, object: nil)
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
        #if os(macOS)
        dragLayout = nil
        #endif
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
        #if os(macOS)
        dragLayout = nil
        #endif
        stopAudio()
        reportError("The workspace closed unexpectedly. Reconnect to open it again.")
    }

    private func pageFailed(_ error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        ready()
        reportError("The workspace could not load. Check that the server is running and reachable.")
    }
}
