import CoreFoundation
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
    let isObscured: Bool
    var reportsWidgetActivity = false
    var isWidgetWorkspaceCurrent: () -> Bool = { false }
    var isMenuWorkspaceCurrent: () -> Bool = { false }
    var isEnrollmentViewCurrent: () -> Bool = { false }
    var chatRequest: HeinzelChatRequest?
    var promptComposer: NativePromptComposer?
    var widgetRefreshRevision = 0
    var versionRefreshRevision = 0
    var footerWidth: CGFloat = 88
    var footerBottom: CGFloat = 0
    var footerGeneration = 0
    var reportFooterSlot: (CGRect?) -> Void = { _ in }
    var ready: () -> Void
    var reportHostName: (String) -> Void = { _ in
        // Hosts without a hostname display do not need this update.
    }
    var reportBackendVersion: (WorkspaceVersionObservation) -> Void = { _ in }
    var reportError: (WorkspaceProblem) -> Void
    var appearance: @MainActor @Sendable (Color) -> Void = { _ in
        // Hosts without native appearance integration ignore this update.
    }

    func makeCoordinator() -> Navigation {
        Navigation(endpoint: endpoint, ready: ready, reportHostName: reportHostName, reportBackendVersion: reportBackendVersion,
                   reportFooterSlot: reportFooterSlot, reportError: reportError,
                   isWidgetWorkspaceCurrent: isWidgetWorkspaceCurrent, isMenuWorkspaceCurrent: isMenuWorkspaceCurrent,
                   isEnrollmentViewCurrent: isEnrollmentViewCurrent)
    }

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
        configuration.userContentController.addScriptMessageHandler(
            context.coordinator, contentWorld: .page, name: "heinzelNotices"
        )
        #if os(iOS)
        configuration.userContentController.addScriptMessageHandler(
            context.coordinator, contentWorld: .page, name: "heinzelFooter"
        )
        #endif
        var audioMember = ""
        #if os(macOS) || canImport(ScreenCaptureKit)
        if #available(iOS 27, *), offersSystemAudio {
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
        let localCommands = endpoint.origin.scheme == "http"
            ? """
                about: () => window.webkit.messageHandlers.heinzelWindow.postMessage('about'),
                lifecycle: () => window.webkit.messageHandlers.heinzelWindow.postMessage('lifecycle'),
                """
            : ""
        let chrome = "titlebar: Object.freeze({ height: 44, left: 80, right: 0 }), onFullscreen, \(localCommands)"
        #else
        let chrome = "mobileWorkspace: true, nativeFooter: footer,"
        #endif
        let connection = endpoint.origin.scheme == "https"
            ? "remoteWorkspace: true,"
            : "workspaceLinkStyle: '\(Endpoint.sharesHTTPSLinks ? "https" : "scheme")',"
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
                const notices = request => window.webkit.messageHandlers.heinzelNotices.postMessage(request);
                let footerMetrics = { width: 88, bottom: 0, revision: -1, generation: -1 };
                let pageHandlesFooter = false;
                let legacyStyle = null;
                let legacyFooter = null;
                let legacyLast = '';
                let legacyCandidate = '';
                let legacyAttempts = 0;
                let legacySequence = 0;
                let legacyFrame = 0;
                const legacySizes = new ResizeObserver(() => scheduleLegacy());
                const scheduleLegacy = () => {
                    if (!legacyFrame) legacyFrame = requestAnimationFrame(updateLegacy);
                };
                const updateLegacy = () => {
                    legacyFrame = 0;
                    if (pageHandlesFooter || document.querySelector('[data-native-footer-slot][data-native-footer-version="2"]')) {
                        legacySequence += 1;
                        pageHandlesFooter = true; legacyMutations.disconnect();
                        legacyStyle?.remove(); legacyStyle = null; legacySizes.disconnect(); legacyFooter = null;
                        return;
                    }
                    const shell = document.querySelector('[data-testid="app-shell"]');
                    const bar = shell?.querySelector('footer[data-footer-width]');
                    if (!shell || !bar || !document.head || footerMetrics.revision < 0) return;
                    if (!legacyStyle) { legacyStyle = document.createElement('style'); document.head.appendChild(legacyStyle); }
                    const css = `[data-testid="app-shell"]{padding-bottom:0!important} footer[data-footer-width]{height:${48 + footerMetrics.bottom}px!important;padding-bottom:${footerMetrics.bottom}px!important;padding-left:${footerMetrics.width}px!important;justify-content:flex-end} footer[data-footer-width] [data-footer-part="@version"]{display:none!important}`;
                    if (legacyStyle.textContent !== css) { legacyStyle.textContent = css; window.dispatchEvent(new Event('resize')); }
                    if (legacyFooter !== bar) { legacySizes.disconnect(); legacySizes.observe(bar); legacyFooter = bar; }
                    const rect = bar.getBoundingClientRect();
                    const { width, revision, generation } = footerMetrics;
                    const slot = {x:rect.x, y:rect.y, width, height:48,
                                  viewportWidth:innerWidth, viewportHeight:innerHeight, revision, generation};
                    const value = JSON.stringify(slot);
                    if (value !== legacyCandidate) { legacyCandidate = value; legacyLast = ''; legacyAttempts = 0; }
                    if (value !== legacyLast && legacyAttempts < 10) {
                        legacyAttempts += 1;
                        legacyLast = value;
                        const request = ++legacySequence;
                        const expired = setTimeout(() => {
                            if (request === legacySequence && legacyAttempts < 10) { legacySequence += 1; legacyLast = ''; scheduleLegacy(); }
                        }, 1000);
                        window.webkit.messageHandlers.heinzelFooter.postMessage(slot).then(ok => {
                            clearTimeout(expired);
                            if (request === legacySequence && ok !== true && legacyAttempts < 10) { legacyLast = ''; setTimeout(scheduleLegacy, 200); }
                        }).catch(() => { clearTimeout(expired); if (request === legacySequence && legacyAttempts < 10) { legacyLast = ''; setTimeout(scheduleLegacy, 200); } });
                    }
                };
                const legacyMutations = new MutationObserver(scheduleLegacy);
                legacyMutations.observe(document, { childList:true, subtree:true, attributes:true,
                    attributeFilter:['data-native-footer-version'] });
                window.addEventListener('resize', scheduleLegacy);
                window.addEventListener('scroll', scheduleLegacy, true);
                window.addEventListener('hz:footer-relocated', scheduleLegacy);
                const footer = Object.freeze({
                    metrics: () => ({ ...footerMetrics }),
                    reportSlot: slot => {
                        // Older pages report without a stamp. The compatibility reporter measures their footer itself.
                        if (slot.revision !== footerMetrics.revision || slot.generation !== footerMetrics.generation ||
                            slot.revision < 0 || slot.generation < 0) return Promise.resolve(false);
                        return window.webkit.messageHandlers.heinzelFooter.postMessage(slot);
                    },
                });
                Object.defineProperty(window, "__heinzelUpdateFooter", { value: (width, bottom, revision, generation) => {
                    footerMetrics = { width, bottom, revision, generation };
                    legacyLast = '';
                    legacyCandidate = '';
                    legacyAttempts = 0;
                    legacySequence += 1;
                    scheduleLegacy();
                    window.dispatchEvent(new Event("heinzel-native-footer-metrics"));
                }});
                Object.defineProperty(window, "heinzelDesktop", {
                    value: Object.freeze({ clientFlavor: "native", \(chrome) \(connection) browser, notices, workspaceAudio: false, \(audioMember) })
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
        #if os(iOS)
        context.coordinator.observeBackground(of: view, onChange: appearance)
        #endif
        view.load(URLRequest(url: endpoint.url))
        return view
    }

    private var offersSystemAudio: Bool {
        #if os(macOS)
        // The Mac's system tap is only for the locally launched workspace.
        endpoint.origin.scheme == "http"
        #else
        true
        #endif
    }

    // An isolated listener can distinguish an actual input event from a page calling click().
    private static let externalLinkScript = """
    const activate = event => {
        if (event.isTrusted) window.webkit.messageHandlers.heinzelExternalLink.postMessage("activate").catch(() => {});
    };
    const openExternal = event => {
        if (!event.isTrusted || ![0, 1].includes(event.button) || event.defaultPrevented) return;
        const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
        if (!(anchor instanceof HTMLAnchorElement) || anchor.hasAttribute("download")) return;
        const target = new URL(anchor.href);
        if (target.origin === location.origin || !["https:", "http:", "mailto:"].includes(target.protocol)) return;
        event.preventDefault();
        window.webkit.messageHandlers.heinzelExternalLink.postMessage(target.href).catch(() => {});
    };
    let guestOpenPending = false;
    const openGuest = event => {
        if (!event.isTrusted || event.button !== 0 || event.defaultPrevented) return;
        const button = event.target instanceof Element ? event.target.closest("button[data-heinzel-browser-external]") : null;
        if (!(button instanceof HTMLButtonElement) || button.disabled) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (guestOpenPending) return;
        guestOpenPending = true;
        window.dispatchEvent(new Event("heinzel-guest-external-started"));
        const failed = () => {
            guestOpenPending = false;
            window.dispatchEvent(new Event("heinzel-guest-external-failed"));
        };
        try {
            window.webkit.messageHandlers.heinzelExternalLink.postMessage("browser-external")
                .then(result => { if (result === true) guestOpenPending = false; else failed(); }, failed);
        } catch { failed(); }
    };
    addEventListener("pointerdown", activate, true);
    addEventListener("keydown", activate, true);
    addEventListener("click", activate, true);
    addEventListener("click", openGuest, true);
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
    func updateNSView(_ view: WKWebView, context: Context) {
        view.setAccessibilityHidden(isObscured)
        updateWidgetActivity(context.coordinator)
    }
    static func dismantleNSView(_ view: WKWebView, coordinator: Navigation) { coordinator.close(view) }
    #else
    func makeUIView(context: Context) -> WKWebView { makeWebView(context) }
    func updateUIView(_ view: WKWebView, context: Context) {
        view.accessibilityElementsHidden = isObscured
        view.isUserInteractionEnabled = !isObscured
        context.coordinator.updateFooterMetrics(in: view, width: footerWidth, bottom: footerBottom, generation: footerGeneration)
        updateWidgetActivity(context.coordinator)
    }
    static func dismantleUIView(_ view: WKWebView, coordinator: Navigation) { coordinator.close(view) }
    #endif

    private func updateWidgetActivity(_ coordinator: Navigation) {
        coordinator.refreshWorkspaceVersion(revision: versionRefreshRevision)
        promptComposer?.navigation = coordinator
        coordinator.setWidgetActivityEnabled(reportsWidgetActivity, revision: widgetRefreshRevision)
        if let chatRequest { coordinator.openChat(chatRequest) }
    }
}

private extension WKWebViewConfiguration {
    func allowsInlineMediaPlaybackIfSupported() {
        #if os(iOS)
        allowsInlineMediaPlayback = true
        #endif
    }
}
