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

struct HeinzelChatRequest: Equatable {
    let id: String
    let workspaceScope: String
    let request = UUID()
}

struct NativePromptRequest: Codable, Equatable {
    let workspaceScope: String
    let recipientID: String
    let recipientName: String
    let text: String
    let requestID: String

    var isValid: Bool {
        workspaceScope.wholeMatch(of: /[a-f0-9]{64}/) != nil && NativeRoute.isSessionID(recipientID) &&
        UUID(uuidString: requestID) != nil && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        text.unicodeScalars.count <= 32_000
    }
}

enum NativePromptOutcome: Equatable {
    case accepted
    case unavailable
    case signIn
    case unconfirmed
    case rejected
}

/// Draft text stays in the app's private container, never in widget storage or a URL.
@MainActor @Observable
final class NativePromptComposer {
    var recipientID = ""
    var text = ""
    var roster: [WidgetHeinzel] = []
    var search = ""
    var page = 0
    private(set) var pageCount = 0
    private(set) var scope: String?
    private(set) var catalogAvailable = false
    private(set) var pending: NativePromptRequest?
    private(set) var sending = false
    private(set) var accepted = false
    private(set) var message: String?
    @ObservationIgnored weak var navigation: Navigation?
    @ObservationIgnored private var sendTask: Task<Void, Never>?

    var canSend: Bool {
        scope != nil && catalogAvailable && !sending && !accepted &&
        (pending != nil || roster.contains { $0.id == recipientID }) &&
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && text.unicodeScalars.count <= 32_000
    }

    func prepare(scope newScope: String?, roster: [WidgetHeinzel]) {
        guard !sending else { return }
        self.roster = roster
        guard scope != newScope else { return }
        scope = newScope
        pending = nil
        recipientID = ""
        text = ""
        message = nil
        accepted = false
        guard let newScope else { return }
        do {
            let directory = try draftDirectory(scope: newScope)
            let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            guard let url = files.filter({ $0.pathExtension == "json" })
                .sorted(by: { $0.lastPathComponent < $1.lastPathComponent }).first else { return }
            let saved = try JSONDecoder().decode(NativePromptRequest.self, from: Data(contentsOf: url))
            guard saved.isValid, saved.workspaceScope == newScope,
                  url.deletingPathExtension().lastPathComponent == saved.requestID else { throw CocoaError(.fileReadCorruptFile) }
            pending = saved
            recipientID = saved.recipientID
            text = saved.text
            message = "Delivery wasn’t confirmed. Retry this saved prompt safely, or check the chat."
        } catch {
            message = "The saved prompt could not be read. Open the chat to check its delivery before writing another."
            // Keep a corrupt pending file from being silently replaced by a new request.
            scope = nil
        }
    }

    func prepareCatalog(scope newScope: String?, snapshot: ActivityWidgetData?) {
        guard !sending else { return }
        if scope != newScope {
            catalogAvailable = false
            prepare(scope: newScope, roster: [])
        }
        guard let newScope, scope == newScope, let snapshot, snapshot.workspaceScope == newScope,
              let generation = snapshot.generation else {
            catalogAvailable = false
            return
        }
        var entries = ActivityWidgetData.search(scope: newScope, generation: generation, query: search, offset: page * 50)
        pageCount = entries.count
        if !recipientID.isEmpty, !entries.contains(where: { $0.id == recipientID }),
           let selected = ActivityWidgetData.find(scope: newScope, generation: generation, id: recipientID) { entries.append(selected) }
        let items = entries.map { entry in
            snapshot.roster.first(where: { $0.id == entry.id }) ?? entry.widgetHeinzel
        }
        prepare(scope: newScope, roster: items)
        catalogAvailable = true
    }

    func startSend() {
        guard sendTask == nil, canSend else { return }
        // The composer outlives the sheet, so closing it does not cancel an accepted turn.
        sendTask = Task { [self] in
            await send()
            sendTask = nil
        }
    }

    private func send() async {
        guard !sending, !accepted else { return }
        guard canSend, let scope, let navigation else {
            message = "Open the workspace and sign in before sending. Your prompt is still here."
            return
        }
        let request = pending ?? NativePromptRequest(
            workspaceScope: scope, recipientID: recipientID,
            recipientName: roster.first { $0.id == recipientID }?.name ?? "Heinzel",
            text: text, requestID: UUID().uuidString
        )
        guard request.isValid else { return }
        // Retain the identity even if an atomic write succeeds but a later file operation fails.
        pending = request
        do {
            let url = try draftDirectory(scope: scope).appendingPathComponent(request.requestID + ".json")
            let data = try JSONEncoder().encode(request)
            #if os(iOS)
            try data.write(to: url, options: [.atomic, .completeFileProtection])
            #else
            try data.write(to: url, options: .atomic)
            #endif
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch {
            message = "Your prompt couldn’t be saved safely. Nothing was sent. Try again."
            return
        }
        sending = true
        message = nil
        let outcome = await navigation.sendPrompt(request)
        sending = false
        switch outcome {
        case .accepted:
            accepted = true
            message = "Response received. Open the chat to read it."
            if let directory = try? draftDirectory(scope: scope) {
                try? FileManager.default.removeItem(at: directory.appendingPathComponent(request.requestID + ".json"))
            }
        case .unavailable:
            message = "The workspace isn’t ready. Open it, then retry this saved prompt."
        case .signIn:
            message = "Sign in to the workspace, then retry this saved prompt."
        case .unconfirmed:
            message = "The response wasn’t confirmed. Retry this saved prompt or open the chat to check its status."
        case .rejected:
            if removePendingFile() {
                pending = nil
                message = "The host rejected this prompt. Check the recipient and text, then send again."
            }
        }
    }

    func discardPending() {
        guard !sending, removePendingFile() else { return }
        pending = nil
        text = ""
        accepted = false
        message = nil
    }

    @discardableResult
    private func removePendingFile() -> Bool {
        guard let pending else { return true }
        do {
            let url = try draftDirectory(scope: pending.workspaceScope).appendingPathComponent(pending.requestID + ".json")
            if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
            return true
        } catch {
            message = "The saved prompt couldn’t be removed. It is still available to retry."
            return false
        }
    }

    func newPrompt() {
        guard accepted else { return }
        let previousScope = scope
        scope = nil
        accepted = false
        pending = nil
        text = ""
        message = nil
        prepare(scope: previousScope, roster: roster)
    }

    private func draftDirectory(scope: String) throws -> URL {
        guard scope.wholeMatch(of: /[a-f0-9]{64}/) != nil else { throw CocoaError(.fileWriteInvalidFileName) }
        var directory = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                    appropriateFor: nil, create: true)
            .appendingPathComponent("HeinzelPromptDrafts", isDirectory: true)
            .appendingPathComponent(scope, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try directory.setResourceValues(values)
        return directory
    }
}

struct WorkspaceView: WebRepresentable {
    let endpoint: Endpoint
    let isObscured: Bool
    var reportsWidgetActivity = false
    var isWidgetWorkspaceCurrent: () -> Bool = { false }
    var isMenuWorkspaceCurrent: () -> Bool = { false }
    var chatRequest: HeinzelChatRequest?
    var promptComposer: NativePromptComposer?
    var widgetRefreshRevision = 0
    var ready: () -> Void
    var reportHostName: (String) -> Void = { _ in
        // Hosts without a hostname display do not need this update.
    }
    var reportError: (WorkspaceProblem) -> Void
    var appearance: @MainActor @Sendable (Color) -> Void = { _ in
        // Hosts without native appearance integration ignore this update.
    }

    func makeCoordinator() -> Navigation {
        Navigation(endpoint: endpoint, ready: ready, reportHostName: reportHostName, reportError: reportError,
                   isWidgetWorkspaceCurrent: isWidgetWorkspaceCurrent, isMenuWorkspaceCurrent: isMenuWorkspaceCurrent)
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
        let chrome = "mobileWorkspace: true,"
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
                Object.defineProperty(window, "heinzelDesktop", {
                    value: Object.freeze({ \(chrome) \(connection) browser, notices, workspaceAudio: false, \(audioMember) })
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
        updateWidgetActivity(context.coordinator)
    }
    static func dismantleUIView(_ view: WKWebView, coordinator: Navigation) { coordinator.close(view) }
    #endif

    private func updateWidgetActivity(_ coordinator: Navigation) {
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
    private enum ProvisionalLoad {
        case idle
        case loading(WKNavigation?)
    }

    private let endpoint: Endpoint
    private let ready: () -> Void
    private let reportHostName: (String) -> Void
    private var lookedUpHostName = false
    weak var workspace: WKWebView?
    #if os(iOS)
    private var backgroundObservation: NSKeyValueObservation?
    #endif
    private var widgetActivity: Task<Void, Never>?
    private var widgetRefreshRevision = 0
    private var lastChatRequest: HeinzelChatRequest?

    func sendPrompt(_ request: NativePromptRequest) async -> NativePromptOutcome {
        guard request.isValid, let workspace,
              request.workspaceScope == ActivityWidgetData.scope(for: endpoint.origin), isWidgetWorkspaceCurrent(),
              hasCommittedWorkspace, case .idle = provisionalLoad,
              workspace.url.map(endpoint.owns) == true else { return .unavailable }
        let revision = documentRevision
        do {
            let result = try await inspectWorkspace(in: workspace, arguments: [
                "sessionID": request.recipientID, "requestID": request.requestID, "prompt": request.text
            ], body: """
                const response = await fetch('/api/heinzel/chat', {
                    ...options, method: 'POST',
                    headers: { ...options.headers, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionID, request_id: requestID, message: prompt, when_busy: 'follow_up' })
                });
                if (response.status === 401 || response.status === 403) return 'sign-in';
                if (response.status === 404 || response.status === 422) return 'rejected';
                if (!response.ok) return 'unconfirmed';
                const answer = await response.json();
                if (answer.session_id !== sessionID || answer.request_id !== requestID ||
                    !['started', 'queued', 'steered'].includes(answer.disposition) ||
                    !Number.isInteger(answer.inbox_position) || answer.inbox_position <= 0) return 'unconfirmed';
                // Acceptance only writes the inbox. The answer operation runs or safely recovers this exact turn.
                const reply = await fetch('/api/heinzel/answer-chat', {
                    ...options, method: 'POST', signal: AbortSignal.timeout(300000),
                    headers: { ...options.headers, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionID, request_id: requestID })
                });
                if (!reply.ok) return 'unconfirmed';
                const result = await reply.json();
                return result.session_id === sessionID && result.request_id === requestID &&
                    result.message?.role === 'assistant' ? 'accepted' : 'unconfirmed';
                """) as? String
            guard revision == documentRevision, isWidgetWorkspaceCurrent(),
                  workspace.url.map(endpoint.owns) == true else { return .unconfirmed }
            if result == "accepted" { return .accepted }
            if result == "sign-in" || result == "" { return .signIn }
            if result == "rejected" { return .rejected }
            return .unconfirmed
        } catch {
            return .unconfirmed
        }
    }

    func openChat(_ request: HeinzelChatRequest) {
        guard lastChatRequest != request, NativeRoute.isSessionID(request.id), let workspace,
              request.workspaceScope == ActivityWidgetData.scope(for: endpoint.origin), isWidgetWorkspaceCurrent(),
              hasCommittedWorkspace, case .idle = provisionalLoad,
              workspace.url.map(endpoint.owns) == true,
              var url = URLComponents(url: endpoint.origin, resolvingAgainstBaseURL: false) else { return }
        url.path = "/heinzel"
        url.queryItems = [URLQueryItem(name: "chat", value: request.id)]
        guard let destination = url.url else { return }
        lastChatRequest = request
        if workspace.url != destination { workspace.load(URLRequest(url: destination)) }
    }

    func setWidgetActivityEnabled(_ enabled: Bool, revision: Int = 0) {
        guard enabled else {
            widgetActivity?.cancel()
            widgetActivity = nil
            return
        }
        guard widgetActivity == nil || widgetRefreshRevision != revision else { return }
        widgetRefreshRevision = revision
        widgetActivity?.cancel()
        widgetActivity = Task { [weak self] in
            while !Task.isCancelled, self != nil {
                await self?.refreshWidgetActivity()
                do { try await Task.sleep(for: .seconds(45)) } catch { return }
            }
        }
    }

    private func refreshWidgetActivity() async {
        guard isWidgetWorkspaceCurrent(), let workspace, workspace.url.map(endpoint.owns) == true else { return }
        let revision = documentRevision
        let invalidation = ActivityWidgetData.invalidation
        guard let refreshToken = ActivityWidgetData.refreshToken() else { return }
        // The session and device tokens stay inside WebKit.
        let scope = ActivityWidgetData.scope(for: endpoint.origin)
        let details = ActivityWidgetData.requestedDetails(scope: scope)
        let artwork = Self.widgetArtwork
        let result = try? await inspectWorkspace(in: workspace, arguments: ["detailIDs": details], body: """
            \(artwork)
            const response = await fetch('/api/heinzel/sessions', options);
            if (response.status === 401 || response.status === 403) return '';
            if (!response.ok) return null;
            const { sessions } = await response.json();
            if (!Array.isArray(sessions) || sessions.some(session => typeof session?.state !== 'string')) return null;
            const poses = {
                paused: 'paused', stuck: 'blocked', waiting_for_you: 'waiting', overloaded: 'overloaded',
                working: 'hammering', waiting_on_others: 'watching', proud: 'done', asleep: 'asleep'
            };
            const seconds = value => typeof value === 'string' && Number.isFinite(Date.parse(value))
                ? Date.parse(value) / 1000 : null;
            const makePortrait = async (appearance, activity) => {
                if (typeof heinzelSvg !== 'function') return null;
                const svg = heinzelSvg({ ...appearance, state: poses[activity] ?? 'dozing' }, 'bust');
                const image = new Image();
                image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
                await image.decode();
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = 100;
                const context = canvas.getContext('2d');
                if (!context) return null;
                context.drawImage(image, 0, 0, 100, 100);
                return canvas.toDataURL('image/png').split(',')[1] ?? null;
            };
            const eligible = sessions.filter(item =>
                typeof item.session_id === 'string' && /^hz-[0-9A-HJKMNP-TV-Z]{26}$/.test(item.session_id) &&
                typeof item.name === 'string' && item.state !== 'retired');
            const catalog = eligible.map(item => ({
                id: item.session_id, name: item.name.slice(0, 128), state: item.state.slice(0, 32),
                activity: typeof item.activity === 'string' ? item.activity.slice(0, 32) : null,
                asks: Number.isInteger(item.waiting_count) && item.waiting_count >= 0 ? item.waiting_count : null,
                lastApproved: seconds(item.last_approved_at)
            }));
            const chosen = eligible.filter(item => detailIDs.includes(item.session_id));
            const selected = new Set(chosen.map(item => item.session_id));
            const rich = [...chosen, ...eligible.filter(item => !selected.has(item.session_id))].slice(0, 40);
            const roster = await Promise.all(rich.map(async item => {
                const details = detailIDs.includes(item.session_id) ? fetch(
                    '/api/heinzel/session?session_id=' + encodeURIComponent(item.session_id) + '&limit=1', options
                ).then(response => response.ok ? response.json() : null).catch(() => null) : null;
                const [portrait, detail] = await Promise.all([
                    makePortrait(item.appearance ?? {}, item.activity).catch(() => null), details
                ]);
                return {
                    id: item.session_id, name: item.name.slice(0, 128), state: item.state,
                    activity: typeof item.activity === 'string' ? item.activity : null,
                    asks: Number.isInteger(item.waiting_count) && item.waiting_count >= 0 ? item.waiting_count : null,
                    lastApproved: seconds(item.last_approved_at),
                    nextWake: detail?.schedule?.status === 'active' ? seconds(detail.schedule.next_wake) : null,
                    portrait
                };
            }));
            return JSON.stringify({
                awake: sessions.filter(session => session.state === 'awake').length,
                waiting: sessions.filter(session => session.state === 'waiting').length,
                roster, catalog
            });
            """) as? String
        guard !Task.isCancelled, revision == documentRevision, invalidation == ActivityWidgetData.invalidation, self.workspace === workspace,
              let result, isWidgetWorkspaceCurrent() else { return }
        let changed: Bool
        if result.isEmpty {
            _ = ActivityWidgetData.clear()
            ActivityWidgetData.reload()
            return
        }
        else if let snapshot = try? JSONDecoder().decode(WidgetSnapshotResponse.self, from: Data(result.utf8)) {
            changed = ActivityWidgetData.save(awake: snapshot.awake, waiting: snapshot.waiting, heinzels: snapshot.roster,
                                              entries: snapshot.catalog, workspaceScope: scope, expectedToken: refreshToken)
        } else { return }
        if changed { ActivityWidgetData.reload() }
    }


    private static let widgetArtwork: String = {
        guard let url = Bundle.main.url(forResource: "HeinzelArtwork", withExtension: "js") else { return "" }
        return (try? String(contentsOf: url, encoding: .utf8)) ?? ""
    }()

    private struct WidgetSnapshotResponse: Decodable {
        let awake: Int
        let waiting: Int
        let roster: [WidgetHeinzel]
        let catalog: [CatalogHeinzel]
    }

    #if os(iOS)
    func observeBackground(of view: WKWebView, onChange: @escaping @MainActor @Sendable (Color) -> Void) {
        backgroundObservation = view.observe(\.underPageBackgroundColor, options: [.initial, .new]) { [weak view] _, _ in
            Task { @MainActor in
                guard let view else { return }
                onChange(Color(uiColor: view.underPageBackgroundColor))
            }
        }
    }
    #endif
    private let browser = IntegratedBrowser()
    private var nativeAudio: AnyObject?
    private var externalLinks = ExternalLinkHandoff()
    private var guestExternalLinks = ExternalLinkHandoff()
    private let guestDNS = BoundedGuestDNS()
    private var provisionalLoad = ProvisionalLoad.idle
    private var hasCommittedWorkspace = false
    private var documentRevision = 0
    #if os(macOS)
    private weak var observedWindow: NSWindow?
    private var mouseMonitor: Any?
    private var dragLayout: TitleBarDragLayout?
    #else
    private var noticeConfirmation: CheckedContinuation<Bool, Never>?
    private weak var noticeAlert: UIAlertController?
    #endif
    private let reportError: (WorkspaceProblem) -> Void
    private let isWidgetWorkspaceCurrent: () -> Bool
    private let isMenuWorkspaceCurrent: () -> Bool

    init(endpoint: Endpoint, ready: @escaping () -> Void, reportHostName: @escaping (String) -> Void,
         reportError: @escaping (WorkspaceProblem) -> Void, isWidgetWorkspaceCurrent: @escaping () -> Bool,
         isMenuWorkspaceCurrent: @escaping () -> Bool) {
        self.endpoint = endpoint
        self.ready = ready
        self.reportHostName = reportHostName
        self.reportError = reportError
        self.isWidgetWorkspaceCurrent = isWidgetWorkspaceCurrent
        self.isMenuWorkspaceCurrent = isMenuWorkspaceCurrent
        super.init()
        NotificationCenter.default.addObserver(
            self, selector: #selector(nativeNoticeOpened), name: WorkspaceNotifications.opened, object: nil
        )
        NotificationCenter.default.addObserver(
            self, selector: #selector(nativeNoticeChanged), name: WorkspaceNotifications.changed, object: nil
        )
        #if os(macOS)
        NotificationCenter.default.addObserver(
            self, selector: #selector(menuAction), name: WorkspaceMenuAction.notification, object: nil
        )
        #endif
    }

    func webView(
        _ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
        decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void
    ) {
        // Keep the decision ordered with WebKit's input messages and navigation callbacks.
        if let url = action.request.url, let frame = action.targetFrame,
           endpoint.permitsNavigation(to: url, inMainFrame: frame.isMainFrame) {
            decisionHandler(.allow)
            return
        }
        if action.targetFrame?.isMainFrame == true && !hasCommittedWorkspace {
            reportError(.denied)
        } else if action.targetFrame?.isMainFrame != false {
            handOffExternalNavigation(action, from: webView)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        guard let url = response.response.url,
              endpoint.permitsNavigation(to: url, inMainFrame: response.isForMainFrame) else { return .cancel }
        if response.isForMainFrame, let http = response.response as? HTTPURLResponse,
           let problem = WorkspaceProblem(response: http.statusCode) {
            reportError(problem)
            return .cancel
        }
        return .allow
    }

    func webView(
        _ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
        for action: WKNavigationAction, windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if action.targetFrame == nil { handOffExternalNavigation(action, from: webView) }
        return nil
    }

    private func handOffExternalNavigation(_ action: WKNavigationAction, from webView: WKWebView) {
        // A button can use window.open or assign location. Redirects from a provisional load may
        // call the same delegate, so allow handoffs only from the committed workspace.
        guard case .idle = provisionalLoad, webView === workspace, webView.url.map(endpoint.owns) == true,
              action.navigationType == .linkActivated || action.navigationType == .other,
              action.sourceFrame.isMainFrame,
              action.sourceFrame.request.url.map(endpoint.owns) == true,
              !action.shouldPerformDownload, action.request.httpMethod == "GET",
              let value = action.request.url?.absoluteString,
              let target = externalLinks.take(value, from: endpoint, needsActivation: true) else { return }
        Task { [weak self, weak webView] in
            guard let self, let webView, case .idle = provisionalLoad,
                  webView === workspace, webView.url.map(endpoint.owns) == true else { return }
            _ = await openExternal(target)
        }
    }

    func webView(
        _ webView: WKWebView, decideMediaCapturePermissionsFor origin: WKSecurityOrigin,
        initiatedBy frame: WKFrameInfo, type: WKMediaCaptureType
    ) async -> WKPermissionDecision {
        let requestIsOwned = frame.request.url.map(endpoint.owns) == true
        return frame.isMainFrame && requestIsOwned && type == .microphone ? .prompt : .deny
    }

    func userContentController(
        _ controller: WKUserContentController, didReceive message: WKScriptMessage,
        replyHandler: @escaping @MainActor (Any?, String?) -> Void
    ) {
        if message.name == "heinzelExternalLink", message.body as? String == "browser-external" {
            guard case .idle = provisionalLoad, let workspace = verifiedWorkspace(for: message),
                  workspace.window?.isKeyWindow == true, let target = browser.externalTarget(),
                  guestExternalLinks.take(target.url.absoluteString, from: endpoint, needsActivation: false) != nil,
                  let host = target.url.host() else {
                replyHandler(false, nil)
                return
            }
            let revision = documentRevision
            let requestedAt = ContinuousClock.now
            Task { [weak self, weak workspace] in
                guard let self else { replyHandler(false, nil); return }
                let resolved = await guestDNS.resolve(host) { BrowserAddress.resolve($0) != nil }
                guard let workspace, resolved, requestedAt.duration(to: .now) <= .seconds(1), self.workspace === workspace,
                      revision == documentRevision, case .idle = provisionalLoad,
                      workspace.window?.isKeyWindow == true, browser.isCurrent(target) else {
                    replyHandler(false, nil)
                    return
                }
                replyHandler(await openExternal(target.url), nil)
            }
            return
        }
        // Record trusted input before WebKit's synchronous new-window callback can run.
        if message.name == "heinzelExternalLink", message.body as? String == "activate" {
            guard case .idle = provisionalLoad, verifiedWorkspace(for: message) != nil else {
                replyHandler(nil, "Workspace command unavailable")
                return
            }
            externalLinks.activate()
            replyHandler(true, nil)
            return
        }
        let startsAudio = message.name == "heinzelAudio" && message.body as? String == "start"
        let enablesNotices = message.name == "heinzelNotices"
            && (message.body as? [String: Any])?["action"] as? String == "enable"
        if startsAudio || enablesNotices {
            guard case .idle = provisionalLoad, verifiedWorkspace(for: message) != nil,
                  externalLinks.consumeActivation() else {
                if startsAudio { replyHandler(false, nil) }
                else { replyHandler(nil, "Choose notifications in workspace settings") }
                return
            }
        }
        let revision = documentRevision
        Task {
            let (value, error) = await handle(message, at: revision)
            replyHandler(value, error)
        }
    }

    private func verifiedWorkspace(for message: WKScriptMessage) -> WKWebView? {
        guard message.frameInfo.isMainFrame, message.frameInfo.request.url.map(endpoint.owns) == true,
              let workspace, message.webView === workspace,
              workspace.url.map(endpoint.owns) == true else { return nil }
        return workspace
    }

    private func handle(_ message: WKScriptMessage, at revision: Int) async -> (Any?, String?) {
        guard let workspace = verifiedWorkspace(for: message) else { return (nil, "Workspace command unavailable") }
        if message.name == "heinzelNotices", let request = message.body as? [String: Any] {
            guard revision == documentRevision, case .idle = provisionalLoad else {
                return (nil, "Workspace command unavailable")
            }
            return (await handleNotice(request, in: workspace), nil)
        }
        if message.name == "heinzelExternalLink" {
            guard let value = message.body as? String,
                  let target = externalLinks.take(value, from: endpoint, needsActivation: false) else { return (false, nil) }
            return (await openExternal(target), nil)
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
        if message.name == "heinzelWindow", message.body as? String == "lifecycle" {
            guard endpoint.origin.scheme == "http", revision == documentRevision, case .idle = provisionalLoad,
                  let state = try? await endpoint.readLifecycle(),
                  self.workspace === workspace, revision == documentRevision else {
                return (nil, "Local service unavailable")
            }
            return (["instance": state.instance, "version": state.version, "restartable": state.restartable == true], nil)
        }
        if message.name == "heinzelDrag" {
            observeWindow(workspace.window)
            dragLayout = TitleBarDragLayout(message.body)
            return (true, nil)
        }
        #endif
        #if os(macOS) || canImport(ScreenCaptureKit)
        if #available(iOS 27, *), message.name == "heinzelAudio" {
            let action = message.body as? String
            guard revision == documentRevision, case .idle = provisionalLoad else {
                return (action == "read" ? NSNull() : false, nil)
            }
            #if os(macOS)
            guard endpoint.origin.scheme == "http" else { return (nil, "System audio unavailable") }
            #endif
            switch action {
            case "available": return (SystemAudio.available, nil)
            case "start":
                guard SystemAudio.available, nativeAudio == nil,
                      let window = workspace.window, window.isKeyWindow else { return (false, nil) }
                #if os(iOS)
                guard window.windowScene?.activationState == .foregroundActive else { return (false, nil) }
                #endif
                let capture = SystemAudio()
                nativeAudio = capture
                #if os(macOS)
                guard await capture.confirm(in: window),
                      revision == documentRevision, nativeAudio === capture else {
                    if nativeAudio === capture { nativeAudio = nil }
                    return (false, nil)
                }
                #endif
                let started = await capture.start()
                guard started, revision == documentRevision, nativeAudio === capture else {
                    capture.stop()
                    if nativeAudio === capture { nativeAudio = nil }
                    return (false, nil)
                }
                return (capture.sampleRate, nil)
            case "read":
                return ((nativeAudio as? SystemAudio)?.read() as Any? ?? NSNull(), nil)
            case "stop":
                stopAudio()
                return (true, nil)
            default: return (nil, "Unknown audio command")
            }
        }
        #endif
        guard message.name == "heinzelBrowser", revision == documentRevision, case .idle = provisionalLoad,
              let request = message.body as? [String: Any] else { return (nil, "Browser command unavailable") }
        return (await browser.handle(request, in: workspace, excluding: endpoint), nil)
    }

    private func stopAudio() {
        #if os(macOS) || canImport(ScreenCaptureKit)
        if #available(iOS 27, *) { (nativeAudio as? SystemAudio)?.stop() }
        #endif
        nativeAudio = nil
    }

    private func handleNotice(_ request: [String: Any], in view: WKWebView) async -> Any? {
        let notices = WorkspaceNotifications.shared
        let revision = documentRevision
        let current = { self.workspace === view && self.documentRevision == revision }
        switch request["action"] as? String {
        case "status": return await notices.status(for: endpoint)
        case "enable":
            return await notices.setEnabled(true, for: endpoint, workspaceIsCurrent: current) {
                await confirmNotices(in: view)
            }
        case "disable":
            return await notices.setEnabled(false, for: endpoint, workspaceIsCurrent: current) { false }
        case "show": return await notices.show(request, for: endpoint, workspaceIsCurrent: current)
        case "take": return notices.takeOpenedPath(for: endpoint)
        default: return nil
        }
    }

    private func confirmNotices(in view: WKWebView) async -> Bool {
        let title = "Allow workspace notifications?"
        let source = endpoint.origin.scheme == "http" ? "this Mac workspace" : endpoint.origin.absoluteString
        let message = "Show notifications from \(source) on this device while Heinzel is open."
        #if os(macOS)
        guard let window = view.window, window.isKeyWindow, window.attachedSheet == nil else { return false }
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: "Cancel")
        alert.addButton(withTitle: "Enable notifications")
        return await alert.beginSheetModal(for: window) == .alertSecondButtonReturn
        #else
        guard let window = view.window, window.isKeyWindow,
              window.windowScene?.activationState == .foregroundActive,
              let presenter = window.rootViewController, presenter.presentedViewController == nil else { return false }
        let alert = UIAlertController(title: title, message: message, preferredStyle: .alert)
        return await withCheckedContinuation { continuation in
            noticeConfirmation = continuation
            noticeAlert = alert
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { [weak self] _ in
                self?.finishNoticeConfirmation(false)
            })
            alert.addAction(UIAlertAction(title: "Enable notifications", style: .default) { [weak self] _ in
                self?.finishNoticeConfirmation(true)
            })
            presenter.present(alert, animated: true)
        }
        #endif
    }

    #if os(iOS)
    private func finishNoticeConfirmation(_ choice: Bool) {
        let confirmation = noticeConfirmation
        noticeConfirmation = nil
        noticeAlert = nil
        confirmation?.resume(returning: choice)
    }
    #endif

    @objc private func nativeNoticeChanged() {
        guard let workspace, workspace.url.map(endpoint.owns) == true else { return }
        workspace.evaluateJavaScript("window.dispatchEvent(new Event('heinzel-notification-permissions-changed'))")
    }

    @objc private func nativeNoticeOpened() {
        guard let workspace, workspace.url.map(endpoint.owns) == true else { return }
        workspace.evaluateJavaScript("window.dispatchEvent(new Event('hz:native-notice'))") { _, _ in
            // The event is advisory; a disconnected page needs no completion action.
        }
    }

    private func openExternal(_ target: URL) async -> Bool {
        #if os(macOS)
        NSWorkspace.shared.open(target)
        #else
        await UIApplication.shared.open(target)
        #endif
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
              let workspace else { return }
        let loading: Bool
        if case .idle = provisionalLoad { loading = false } else { loading = true }
        guard MacMenuDelivery.allowed(available: isMenuWorkspaceCurrent(),
                                      visible: !workspace.isHiddenOrHasHiddenAncestor,
                                      keyWindow: workspace.window?.isKeyWindow == true,
                                      ownsOrigin: workspace.url.map(endpoint.owns) == true,
                                      committed: hasCommittedWorkspace, loading: loading) else { return }
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
        if workspace === view { workspace = nil }
        documentRevision &+= 1
        NotificationCenter.default.removeObserver(self, name: WorkspaceNotifications.opened, object: nil)
        NotificationCenter.default.removeObserver(self, name: WorkspaceNotifications.changed, object: nil)
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelNotices", contentWorld: .page)
        view.configuration.userContentController.removeScriptMessageHandler(
            forName: "heinzelExternalLink", contentWorld: .defaultClient
        )
        #if os(macOS)
        observeWindow(nil)
        NotificationCenter.default.removeObserver(self, name: WorkspaceMenuAction.notification, object: nil)
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelWindow", contentWorld: .page)
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelDrag", contentWorld: .defaultClient)
        #else
        backgroundObservation?.invalidate()
        noticeAlert?.dismiss(animated: false)
        finishNoticeConfirmation(false)
        #endif
        setWidgetActivityEnabled(false)
        stopAudio()
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelAudio", contentWorld: .page)
        browser.reset()
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelBrowser", contentWorld: .page)
        view.stopLoading()
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        documentRevision &+= 1
        #if os(iOS)
        noticeAlert?.dismiss(animated: false)
        finishNoticeConfirmation(false)
        #endif
        provisionalLoad = .loading(navigation)
        externalLinks.clearActivation()
        #if os(macOS)
        dragLayout = nil
        #endif
        stopAudio()
        browser.reset()
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        endProvisionalLoad(navigation)
        hasCommittedWorkspace = true
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        endProvisionalLoad(navigation)
        if endpoint.origin.scheme == "https", endpoint.token == nil {
            let revision = documentRevision
            Task {
                // A host installed before native onboarding may show its desktop command on a fresh phone.
                // Inspect only whether this workspace has credentials. Keep their values inside WebKit.
                let needsSignIn = try? await webView.evaluateJavaScript("""
                    document.getElementById('root') !== null &&
                    !sessionStorage.getItem('hz.token') && !localStorage.getItem('hz.device') &&
                    !new URLSearchParams(location.hash.slice(1)).has('token')
                    """) as? Bool
                guard workspace === webView, revision == documentRevision else { return }
                if needsSignIn == true { reportError(.signIn) }
                else {
                    ready()
                    await resolveWorkspaceName(in: webView)
                }
            }
        } else {
            ready()
            Task { await resolveWorkspaceName(in: webView) }
        }
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

    private func resolveWorkspaceName(in view: WKWebView) async {
        guard !lookedUpHostName, endpoint.origin.scheme == "https", view.url.map(endpoint.owns) == true else { return }
        lookedUpHostName = true
        let revision = documentRevision
        let name = try? await inspectWorkspace(in: view, body: """
            const response = await fetch('/api/system/doctor', options);
            if (!response.ok && response.status !== 503) return null;
            const { checks } = await response.json();
            if (!checks || typeof checks !== 'object') return null;
            for (const items of Object.values(checks)) {
                if (!Array.isArray(items)) continue;
                const match = items.find(item => item?.name === 'hostname' && typeof item.detail === 'string');
                if (match) return match.detail;
            }
            return null;
            """) as? String
        guard workspace === view, revision == documentRevision, let name else { return }
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty && trimmed.count <= 128 && trimmed.rangeOfCharacter(from: .controlCharacters) == nil {
            reportHostName(trimmed)
        }
    }

    private func inspectWorkspace(in view: WKWebView, arguments: [String: Any] = [:], body: String) async throws -> Any? {
        var arguments = arguments
        arguments["origin"] = endpoint.origin.absoluteString
        return try await view.callAsyncJavaScript("""
            if (location.origin !== new URL(origin).origin) return null;
            const token = sessionStorage.getItem('hz.token') ?? localStorage.getItem('hz.device');
            if (!token) return '';
            const options = {
                headers: { Authorization: `Bearer ${token}` }, redirect: 'error',
                credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(5000)
            };
            \(body)
            """, arguments: arguments, in: nil, contentWorld: .defaultClient)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        endProvisionalLoad(navigation)
        pageFailed(error)
        nativeNoticeOpened()
    }

    private func endProvisionalLoad(_ navigation: WKNavigation?) {
        if case .loading(let pending) = provisionalLoad, pending === navigation { provisionalLoad = .idle }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        pageFailed(error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        documentRevision &+= 1
        provisionalLoad = .idle
        hasCommittedWorkspace = false
        externalLinks.clearActivation()
        #if os(macOS)
        dragLayout = nil
        #endif
        stopAudio()
        browser.reset()
        reportError(.interrupted)
    }

    private func pageFailed(_ error: Error) {
        let failure = error as NSError
        if failure.domain == NSURLErrorDomain, failure.code == NSURLErrorCancelled { return }
        // WebKit reports a cancelled policy decision as a frame load interruption. The previous
        // workspace remains visible when a cross-origin redirect or external handoff is denied.
        if failure.domain == "WebKitErrorDomain", failure.code == 102 { return }
        reportError(WorkspaceProblem(navigationError: error))
    }
}
