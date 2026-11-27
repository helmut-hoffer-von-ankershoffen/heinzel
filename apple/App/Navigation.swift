import CoreFoundation
import SwiftUI
import WebKit
#if DEBUG
import OSLog
#endif

@MainActor
final class Navigation: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply {
    private enum ProvisionalLoad {
        case idle
        case loading(WKNavigation?)
    }

    private let endpoint: Endpoint
    // This context is fixed for the lifetime of the WKWebView. Never replace it
    // in response to a later callback from the same view.
    private let widgetActivation: ActivityCatalog.Activation?
    private let ready: () -> Void
    private let reportHostName: (String) -> Void
    private let reportBackendVersion: (WorkspaceVersionObservation) -> Void
    private let reportFooterSlot: (CGRect?) -> Void
    #if os(iOS)
    private var footerWidth: CGFloat = 88
    private var footerBottom: CGFloat = 0
    private var footerGeneration = 0
    private var lastFooterMetrics: (CGFloat, CGFloat, Int)?
    private var pendingFooterMetrics: (CGFloat, CGFloat, Int)?
    private var footerDeliveryAttempts = 0
    private var footerDeliveryID = 0
    #endif
    private let versionRefresh = WorkspaceVersionRefresh()
    private var lookedUpHostName = false
    weak var workspace: WKWebView?
    #if os(iOS)
    private var backgroundObservation: NSKeyValueObservation?
    #endif
    private var widgetActivity: Task<Void, Never>?
    private var enrollmentTask: Task<Void, Never>?
    private var attemptedEnrollment = false
    private var widgetRefreshRevision = 0
    private var lastChatRequest: HeinzelChatRequest?

    func sendPrompt(_ request: NativePromptRequest) async -> NativePromptOutcome {
        guard request.isValid, let workspace,
              request.workspaceScope == ActivityWidgetData.currentScope(for: endpoint.origin), isWidgetWorkspaceCurrent(), widgetContextCurrent,
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
            guard revision == documentRevision, isWidgetWorkspaceCurrent(), widgetContextCurrent,
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
              request.workspaceScope == ActivityWidgetData.currentScope(for: endpoint.origin), isWidgetWorkspaceCurrent(), widgetContextCurrent,
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
        guard isWidgetWorkspaceCurrent(), widgetContextCurrent, let workspace, workspace.url.map(endpoint.owns) == true else { return }
        let revision = documentRevision
        let invalidation = ActivityWidgetData.invalidation
        guard let refreshToken = widgetActivation == nil ? ActivityWidgetData.legacyRefreshToken() : ActivityWidgetData.refreshToken() else { return }
        guard widgetContextCurrent else { return }
        let refreshActivation = widgetActivation
        // The session and device tokens stay inside WebKit.
        guard let scope = ActivityWidgetData.currentScope(for: endpoint.origin) else { return }
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
                awake: Math.min(9999, sessions.filter(session => session.state === 'awake').length),
                waiting: Math.min(9999, sessions.filter(session => session.state !== 'retired' && Number.isInteger(session.waiting_count) && session.waiting_count > 0).length),
                roster, catalog
            });
            """) as? String
        guard !Task.isCancelled, revision == documentRevision, invalidation == ActivityWidgetData.invalidation, self.workspace === workspace,
              let result, isWidgetWorkspaceCurrent(), widgetContextCurrent else { return }
        let changed: Bool
        if result.isEmpty {
            if let refreshActivation {
                _ = WidgetCredentialStore.invalidateShared(refreshActivation)
            } else {
                _ = WidgetCredentialStore.clearShared(expectedToken: refreshToken)
            }
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
    private var currentNavigation: WKNavigation?
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
    private let isEnrollmentViewCurrent: () -> Bool

    init(endpoint: Endpoint, ready: @escaping () -> Void, reportHostName: @escaping (String) -> Void,
         reportBackendVersion: @escaping (WorkspaceVersionObservation) -> Void,
         reportFooterSlot: @escaping (CGRect?) -> Void,
         reportError: @escaping (WorkspaceProblem) -> Void, isWidgetWorkspaceCurrent: @escaping () -> Bool,
         isMenuWorkspaceCurrent: @escaping () -> Bool, isEnrollmentViewCurrent: @escaping () -> Bool) {
        self.endpoint = endpoint
        let activation = ActivityWidgetData.currentActivation()
        self.widgetActivation = activation?.endpoint == endpoint.origin.absoluteString ? activation : nil
        self.ready = ready
        self.reportHostName = reportHostName
        self.reportBackendVersion = reportBackendVersion
        self.reportFooterSlot = reportFooterSlot
        self.reportError = reportError
        self.isWidgetWorkspaceCurrent = isWidgetWorkspaceCurrent
        self.isMenuWorkspaceCurrent = isMenuWorkspaceCurrent
        self.isEnrollmentViewCurrent = isEnrollmentViewCurrent
        super.init()
        NotificationCenter.default.addObserver(
            self, selector: #selector(nativeNoticeOpened), name: WorkspaceNotifications.opened, object: nil
        )
        NotificationCenter.default.addObserver(
            self, selector: #selector(nativeNoticeChanged), name: WorkspaceNotifications.changed, object: nil
        )
        #if os(macOS)
        NotificationCenter.default.addObserver(
            self, selector: #selector(menuAction), name: WorkspaceMenuRequest.notification, object: nil
        )
        #endif
    }

    private var widgetContextCurrent: Bool {
        ActivityWidgetData.currentActivation() == widgetActivation
    }

    private func scheduleWidgetEnrollment(in view: WKWebView, revision: Int) {
        guard !attemptedEnrollment, endpoint.origin.scheme == "https",
              let store = WidgetCredentialStore.configured() else { return }
        attemptedEnrollment = true
        enrollmentTask = Task { [weak self, weak view] in
            guard let self, let view else { return }
            let current = { [weak self, weak view] in
                guard let self, let view else { return false }
                return !Task.isCancelled && self.workspace === view && self.documentRevision == revision &&
                    self.isEnrollmentViewCurrent() && view.url.map(self.endpoint.owns) == true
            }
            guard current() else { return }
            let fingerprint = await WidgetEnrollmentTransport.pairedDeviceFingerprint(in: view, endpoint: endpoint.origin, isCurrent: current)
            guard current() else { return }
            let enrollment = WidgetFencedEnrollment(store: store)
            let coordinator = WidgetEnrollmentCoordinator(enrollment: enrollment)
            let result: WidgetEnrollmentOutcome
            if let pending = ActivityCatalog.pendingEnrollment(url: store.catalogURL) {
                let recorded = pending.previous.endpoint == endpoint.origin.absoluteString && current()
                    ? enrollment.finishRecordedRemoteRecovery(pending) : ActivityCatalog.ClearResult(revoked: false, purged: false, stale: true)
                if recorded.revoked {
                    result = .identityMismatch(recorded)
                } else if let fingerprint, ActivityCatalog.cancelledRecoveryMatches(url: store.catalogURL, expected: pending, fingerprint: fingerprint) {
                    result = await coordinator.recover(pending, in: view, endpoint: endpoint.origin, fingerprint: fingerprint, isCurrent: current)
                } else {
                    let matchesPairing = fingerprint.map { ActivityCatalog.enrollmentPairingMatches(url: store.catalogURL, expected: pending, fingerprint: $0) } ?? false
                    guard matchesPairing && enrollment.canResume(pending) else {
                        if pending.previous.endpoint == endpoint.origin.absoluteString, current() {
                            if matchesPairing, let installed = enrollment.finishInterruptedInstallation(pending), current(),
                               ActivityCatalog.activation(url: store.catalogURL) == installed {
                                NotificationCenter.default.post(name: WidgetCredentialStore.activationChanged, object: installed.id)
                            } else if let expired = enrollment.finishExpiredReceipt(pending), expired.revoked {
                                ActivityWidgetData.didInvalidate(); ActivityWidgetData.reload()
                                if !expired.complete { NotificationCenter.default.post(name: ActivityWidgetData.clearFailed, object: nil) }
                            } else if enrollment.abandonInterruptedPreparation(pending), current(),
                                      ActivityCatalog.activation(url: store.catalogURL) == pending.previous {
                                NotificationCenter.default.post(name: WidgetCredentialStore.preparationRecovered, object: pending.previous.id)
                            }
                        }
                        return
                    }
                    guard let fingerprint else { return }
                    result = await coordinator.resume(pending, in: view, endpoint: endpoint.origin, fingerprint: fingerprint, isCurrent: current)
                }
            } else {
                guard let fingerprint else { return }
                let active = ActivityCatalog.activation(url: store.catalogURL)
                if let active, active.endpoint != endpoint.origin.absoluteString { return }
                if let active, active.credentialGeneration != nil {
                    guard let existing = store.renewalCredential(expected: active) else { return }
                    if existing.expiresAt.timeIntervalSinceNow > 3600 { return }
                }
                guard store.keychain.preflight(), current() else { return }
                guard let expected = active ?? ActivityCatalog.activate(url: store.catalogURL, endpoint: endpoint.origin.absoluteString) else { return }
                result = await coordinator.begin(expected: expected, in: view, endpoint: endpoint.origin, fingerprint: fingerprint, isCurrent: current)
            }
            if case .identityMismatch(let clear) = result {
                if clear.revoked { ActivityWidgetData.didInvalidate(); ActivityWidgetData.reload() }
                if !clear.stale && !clear.complete { NotificationCenter.default.post(name: ActivityWidgetData.clearFailed, object: nil) }
            }
        }
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
        guard webView === workspace, let url = response.response.url,
              endpoint.permitsNavigation(to: url, inMainFrame: response.isForMainFrame) else { return .cancel }
        if response.isForMainFrame, let http = response.response as? HTTPURLResponse,
           let problem = WorkspaceProblem(response: http.statusCode) {
            if (http.statusCode == 401 || http.statusCode == 403), let widgetActivation {
                _ = WidgetCredentialStore.invalidateShared(widgetActivation)
                ActivityWidgetData.reload()
            }
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
        #if os(iOS)
        if message.name == "heinzelFooter" {
            guard case .idle = provisionalLoad, let view = verifiedWorkspace(for: message),
                  let values = message.body as? [String: Any] else {
                replyHandler(false, nil)
                return
            }
            let accepted = WorkspaceFooterGeometry.receive(values, viewport: view.bounds.size,
                width: footerWidth, bottom: footerBottom, revision: documentRevision,
                generation: footerGeneration, report: reportFooterSlot)
            replyHandler(accepted, nil)
            return
        }
        #endif
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

    #if os(iOS)
    func updateFooterMetrics(in view: WKWebView, width: CGFloat, bottom: CGFloat, generation: Int) {
        guard view === workspace, width.isFinite, bottom.isFinite else { return }
        let newWidth = min(180, max(44, width))
        let newBottom = min(80, max(0, bottom))
        if footerWidth != newWidth || footerBottom != newBottom || footerGeneration != generation {
            footerDeliveryAttempts = 0
            footerDeliveryID &+= 1
            lastFooterMetrics = nil
            pendingFooterMetrics = nil
        }
        footerWidth = newWidth
        footerBottom = newBottom
        footerGeneration = generation
        guard view === workspace, view.url.map(endpoint.owns) == true, case .idle = provisionalLoad else { return }
        let metrics = (footerWidth, footerBottom, footerGeneration)
        if let lastFooterMetrics, lastFooterMetrics == metrics { return }
        if let pendingFooterMetrics, pendingFooterMetrics == metrics { return }
        guard footerDeliveryAttempts < 10 else { return }
        footerDeliveryAttempts += 1
        footerDeliveryID &+= 1
        let delivery = footerDeliveryID
        pendingFooterMetrics = metrics
        let revision = documentRevision
        let script = "typeof window.__heinzelUpdateFooter === 'function' ? (window.__heinzelUpdateFooter(\(footerWidth), \(footerBottom), \(revision), \(footerGeneration)), true) : false"
        view.evaluateJavaScript(script) { [weak self, weak view] value, error in
            guard let self, let view, self.workspace === view, self.footerDeliveryID == delivery,
                  WorkspaceFooterGeometry.deliveryIsCurrent(revision: revision, currentRevision: self.documentRevision,
                    requested: metrics, current: (self.footerWidth, self.footerBottom, self.footerGeneration)) else { return }
            self.pendingFooterMetrics = nil
            if error == nil, value as? Bool == true {
                self.lastFooterMetrics = metrics
            } else {
                Task { @MainActor [weak self, weak view] in
                    do { try await Task.sleep(for: .milliseconds(200)) } catch { return }
                    guard let self, let view, self.workspace === view,
                          WorkspaceFooterGeometry.deliveryIsCurrent(revision: revision, currentRevision: self.documentRevision,
                            requested: metrics, current: (self.footerWidth, self.footerBottom, self.footerGeneration)) else { return }
                    self.updateFooterMetrics(in: view, width: metrics.0, bottom: metrics.1, generation: metrics.2)
                }
            }
        }
        Task { @MainActor [weak self, weak view] in
            do { try await Task.sleep(for: .seconds(1)) } catch { return }
            guard let self, let view, self.workspace === view,
                  self.footerDeliveryID == delivery, self.pendingFooterMetrics != nil,
                  WorkspaceFooterGeometry.deliveryIsCurrent(revision: revision, currentRevision: self.documentRevision,
                    requested: metrics, current: (self.footerWidth, self.footerBottom, self.footerGeneration)) else { return }
            self.footerDeliveryID &+= 1
            self.pendingFooterMetrics = nil
            self.updateFooterMetrics(in: view, width: metrics.0, bottom: metrics.1, generation: metrics.2)
        }
    }
    #endif

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
        guard let request = notification.object as? WorkspaceMenuRequest,
              let workspace else { return }
        let loading: Bool
        if case .idle = provisionalLoad { loading = false } else { loading = true }
        guard MacMenuDelivery.allowed(available: isMenuWorkspaceCurrent(),
                                      visible: !workspace.isHiddenOrHasHiddenAncestor,
                                      keyWindow: workspace.window?.isKeyWindow == true,
                                      ownsOrigin: workspace.url.map(endpoint.owns) == true,
                                      committed: hasCommittedWorkspace, loading: loading),
              request.claim() else { return }
        let action = request.action
        switch action {
        case .about:
            // Only the mounted About receiver cancels this event. An acknowledgment is the inverse of dispatchEvent's result.
            workspace.evaluateJavaScript("!window.dispatchEvent(new CustomEvent('hz:menu', { detail: 'about', cancelable: true }))") { value, error in
                request.complete(value, error: error)
            }
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
        if workspace === view { workspace = nil; reportFooterSlot(nil) }
        documentRevision &+= 1
        versionRefresh.cancel()
        enrollmentTask?.cancel()
        NotificationCenter.default.removeObserver(self, name: WorkspaceNotifications.opened, object: nil)
        NotificationCenter.default.removeObserver(self, name: WorkspaceNotifications.changed, object: nil)
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelNotices", contentWorld: .page)
        view.configuration.userContentController.removeScriptMessageHandler(
            forName: "heinzelExternalLink", contentWorld: .defaultClient
        )
        #if os(macOS)
        observeWindow(nil)
        NotificationCenter.default.removeObserver(self, name: WorkspaceMenuRequest.notification, object: nil)
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelWindow", contentWorld: .page)
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelDrag", contentWorld: .defaultClient)
        #else
        view.configuration.userContentController.removeScriptMessageHandler(forName: "heinzelFooter", contentWorld: .page)
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
        guard webView === workspace, let navigation else { return }
        #if os(iOS)
        reportFooterSlot(nil)
        lastFooterMetrics = nil
        pendingFooterMetrics = nil
        footerDeliveryAttempts = 0
        footerDeliveryID &+= 1
        #endif
        currentNavigation = navigation
        documentRevision &+= 1
        versionRefresh.cancel()
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
        guard webView === workspace, let navigation, currentNavigation === navigation else { return }
        endProvisionalLoad(navigation)
        hasCommittedWorkspace = true
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView === workspace, let navigation, currentNavigation === navigation else { return }
        endProvisionalLoad(navigation)
        #if os(iOS)
        lastFooterMetrics = nil
        pendingFooterMetrics = nil
        footerDeliveryAttempts = 0
        footerDeliveryID &+= 1
        updateFooterMetrics(in: webView, width: footerWidth, bottom: footerBottom, generation: footerGeneration)
        #endif
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
                guard workspace === webView, revision == documentRevision, currentNavigation === navigation else { return }
                if needsSignIn == true {
                    if let widgetActivation { _ = WidgetCredentialStore.invalidateShared(widgetActivation); ActivityWidgetData.reload() }
                    reportError(.signIn)
                } else if needsSignIn == false {
                    ready()
                    scheduleWidgetEnrollment(in: webView, revision: revision)
                    refreshWorkspaceVersion()
                    await resolveWorkspaceName(in: webView)
                }
            }
        } else {
            ready()
            scheduleWidgetEnrollment(in: webView, revision: documentRevision)
            refreshWorkspaceVersion()
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

    func refreshWorkspaceVersion(revision: Int? = nil) {
        guard let view = workspace, hasCommittedWorkspace, case .idle = provisionalLoad,
              view.url.map(endpoint.owns) == true else { return }
        let document = documentRevision
        versionRefresh.request(revision: revision, read: { [weak self, weak view] in
            guard let self, let view else { return nil }
            return await self.resolveWorkspaceVersion(in: view)
        }, isCurrent: { [weak self, weak view] in
            guard let self, let view else { return false }
            return self.workspace === view && self.documentRevision == document &&
                view.url.map(self.endpoint.owns) == true
        }, report: { [weak self] observation in self?.reportBackendVersion(observation) })
    }

    private func resolveWorkspaceVersion(in view: WKWebView) async -> WorkspaceVersionObservation? {
        guard view.url.map(endpoint.owns) == true else { return nil }
        let revision = documentRevision
        for attempt in 0..<5 {
            guard !Task.isCancelled, workspace === view, revision == documentRevision,
                  view.url.map(endpoint.owns) == true else { return nil }
            let data = try? await inspectWorkspace(in: view, body: """
                const date = value => {
                    if (typeof value !== 'string') return null;
                    const m = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:[.][0-9]{1,6})?(?:Z|[+-]([0-9]{2}):([0-9]{2}))$/.exec(value);
                    if (!m) return null;
                    const [, year, month, day, hour, minute, second, offsetHour, offsetMinute] = m.map(Number);
                    if (year < 1970 || month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate() ||
                        hour > 23 || minute > 59 || second > 59 || (offsetHour || 0) > 23 || (offsetMinute || 0) > 59) return null;
                    const ms = Date.parse(value); return Number.isFinite(ms) ? ms : null;
                };
                const response = await fetch('/api/workspace-version', options);
                if (response.ok) {
                    const info = await response.json();
                    return {version:info?.web?.version ?? null, installedVersion:info?.installed?.version ?? null,
                            installedUpdatedAt:date(info?.installed?.updated_at), daemonLastVersion:info?.daemon?.last_observed_version ?? null,
                            daemonLastObservedAt:date(info?.daemon?.last_observed_at),
                            daemonObservationStale:typeof info?.daemon?.observation_stale === 'boolean' ? info.daemon.observation_stale : null};
                }
                if (response.status !== 404) return null;
                const fallback = await fetch('/api/system/info', options);
                if (!fallback.ok) return null;
                const info = await fallback.json();
                return {version:info?.version ?? null};
                """) as? [String: Any]
            guard !Task.isCancelled, workspace === view, revision == documentRevision, view.url.map(endpoint.owns) == true else { return nil }
            if let data, let observation = WorkspaceVersionObservation.fromPayload(data, now: .now) {
                return observation
            }
            if attempt < 4 { do { try await Task.sleep(for: .seconds(1)) } catch { return nil } }
        }
        return nil
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
        guard webView === workspace, let navigation, currentNavigation === navigation else { return }
        endProvisionalLoad(navigation)
        pageFailed(error)
        nativeNoticeOpened()
    }

    private func endProvisionalLoad(_ navigation: WKNavigation?) {
        if case .loading(let pending) = provisionalLoad, pending === navigation { provisionalLoad = .idle }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        guard webView === workspace, let navigation, currentNavigation === navigation else { return }
        pageFailed(error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        guard webView === workspace else { return }
        #if os(iOS)
        reportFooterSlot(nil)
        lastFooterMetrics = nil
        pendingFooterMetrics = nil
        footerDeliveryAttempts = 0
        footerDeliveryID &+= 1
        #endif
        documentRevision &+= 1
        versionRefresh.cancel()
        currentNavigation = nil
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
        if failure.domain == NSURLErrorDomain, failure.code == NSURLErrorUserAuthenticationRequired,
           let widgetActivation {
            _ = WidgetCredentialStore.invalidateShared(widgetActivation)
            ActivityWidgetData.reload()
        }
        reportError(WorkspaceProblem(navigationError: error))
    }
}
