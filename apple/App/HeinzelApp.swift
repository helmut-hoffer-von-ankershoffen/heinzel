import AVFoundation
import Network
import SwiftUI
import UserNotifications
import WebKit
#if os(macOS)
import Carbon
#endif
#if os(iOS)
import UIKit
#endif

private let workspaceSwitchTitle = "Switch Workspace"
private let workspaceSwitchSymbol = "arrow.left.arrow.right"

private let heinzelTagline = """
    Durable agents accelerating product teams,
    like the little elves from Cologne.
    """

private var heinzelAppVersion: String {
    let bundle = Bundle.main
    let version = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
    let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown"
    return "Heinzel app \(version) · Build \(build)"
}

private var heinzelAppBuild: AppBuildObservation.Build? {
    let bundle = Bundle.main
    return AppBuildObservation.Build(
        version: bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
        number: bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String,
        revision: bundle.object(forInfoDictionaryKey: "HeinzelSourceRevision") as? String
    )
}

private enum ConnectionPalette {
    static let ink = Color(red: 0.149, green: 0.231, blue: 0.196)
    static let accent = Color(red: 0.188, green: 0.282, blue: 0.227)
    static let muted = Color(red: 0.30, green: 0.36, blue: 0.32)
    static let warning = Color(red: 0.50, green: 0.25, blue: 0.17)
    static let rule = ink.opacity(0.14)
    static let appSurface = Color(red: 0.94, green: 0.96, blue: 0.98)
}

private struct ConnectionFooter: View {
    var body: some View {
        Text(heinzelAppVersion).font(.caption2)
        .foregroundStyle(ConnectionPalette.muted)
        .multilineTextAlignment(.center)
        .frame(maxWidth: 460)
        .padding(.horizontal, 24)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
    }
}

private struct WorkspaceVersionDetails: View {
    let app: AppBuildObservation?
    let checks: [(String, String)]
    let internetClientAddress: String?
    let origin: URL?
    let name: String?
    let source: String?
    let target: WorkspaceInformationRequest?
    let document: @MainActor @Sendable () -> WKWebView?

    @Environment(\.workspaceInformationVisible) private var visible
    @State private var facts = WorkspaceInformationFacts()
    private var request: WorkspaceInformationRequest? { visible ? target : nil }
    private var current: WorkspaceInformationFacts.Snapshot? {
        guard let request, facts.snapshot?.target == request else { return nil }
        return facts.snapshot
    }

    var body: some View {
        let live = current?.version
        VStack(alignment: .leading, spacing: 10) {
            heading("Application")
            fact("Name", "Heinzelmännchen")
            fact("Type", "Native")
            fact("Version", heinzelAppBuild?.version ?? "Unknown")
            fact("Build", heinzelAppBuild?.number ?? "Unknown")
            fact("Revision", heinzelAppBuild?.revision)
            fact("Operating system", WorkspaceView.clientOS + " " + ProcessInfo.processInfo.operatingSystemVersionString)
            fact("IP address", (origin == nil ? internetClientAddress : current?.clientAddress) ?? "Unknown")
            fact("Build first observed", date(app?.observedSince))
            fact("Location", Bundle.main.bundleURL.path(percentEncoded: false))
            ForEach(checks, id: \.0) { check in fact(check.0, check.1) }
            if let origin, let name {
                Divider()
                heading("Workspace")
                fact("Name", name)
                fact("Version", live?.version ?? "Unknown")
                fact("Uptime", live?.uptimeAtObservation.map(uptime) ?? "Unavailable")
                fact("IP address", live?.backendIP ?? "Unavailable")
                fact("Source", source)
                fact("Address", origin.absoluteString)
                fact("Connection", origin.scheme == "http" ? "Local" : (live != nil ? "Online" : "Unknown"))
                fact("Backend running since", date(live?.webRunningSince))
                if let live { versionFacts(live) }
            }
        }
        .font(.footnote)
        .foregroundStyle(ConnectionPalette.muted)
        .task(id: request) { await facts.observe(request, view: document) }
    }

    @ViewBuilder private func versionFacts(_ version: WorkspaceVersionObservation) -> some View {
        fact("Version report", date(version.backendObservedAt ?? version.observedAt))
        fact("Web server initialized", date(version.webObservedSince))
        fact("Installed version", version.differentInstalledVersion)
        fact("Installation updated", date(version.installedUpdatedAt))
        fact("Daemon running version", version.daemonRunningVersion)
        fact("Daemon running since", date(version.daemonRunningSince))
        fact("Daemon observed version", version.daemonLastVersion)
        fact("Daemon tick observed", date(version.daemonLastObservedAt))
        fact("Tick stale at report", version.daemonObservationStale.map { $0 ? "Yes" : "No" })
        fact("Daemon registration recorded", date(version.daemonRegisteredAt))
        fact("Daemon loaded at report", version.daemonLoaded.map { $0 ? "Yes" : "No" })
        fact("Daemon ownership verified at report", version.daemonOwned.map { $0 ? "Yes" : "No" })
        fact("Daemon status observed", date(version.daemonStatusObservedAt))
        if version.version.range(of: #"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$"#, options: .regularExpression) != nil,
           let release = URL(string: "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag/v" + version.version) {
            Link("Release details", destination: release)
        }
    }

    private func heading(_ title: String) -> some View {
        Text(title).font(.headline).foregroundStyle(ConnectionPalette.ink).accessibilityAddTraits(.isHeader)
    }

    @ViewBuilder private func fact(_ label: String, _ value: String?) -> some View {
        if let value {
            LabeledContent {
                Text(verbatim: value).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.trailing)
            } label: { Text(label) }
        }
    }

    private func date(_ value: Date?) -> String? {
        value?.formatted(date: .abbreviated, time: .standard)
    }

    private func uptime(_ interval: TimeInterval) -> String {
        if interval < 60 { return "Under a minute" }
        let minutes = Int(interval) / 60
        let hours = minutes / 60
        if hours >= 24 { return "\(hours / 24)d \(hours % 24)h" }
        if hours > 0 { return "\(hours)h \(minutes % 60)m" }
        return "\(minutes)m"
    }
}

private func connectionIcon(_ symbol: String, label: String) -> some View {
    Image(systemName: symbol)
        .font(.body.weight(.medium))
        .foregroundStyle(ConnectionPalette.accent)
        .frame(width: 44, height: 44)
        .contentShape(Rectangle())
        .accessibilityLabel(label)
}

@MainActor
private func connectionIconButton(_ symbol: String, label: String, action: @escaping () -> Void) -> some View {
    Button(action: action) { connectionIcon(symbol, label: label) }
        .buttonStyle(.plain)
        .help(label)
}

@MainActor
private func connectionSheetHeader<Actions: View>(_ title: String, @ViewBuilder actions: () -> Actions) -> some View {
    let heading = Text(title)
        .font(.system(.title2, design: .serif, weight: .semibold))
        .accessibilityAddTraits(.isHeader)
    let buttons = HStack(spacing: 0, content: actions)
    return ViewThatFits(in: .horizontal) {
        HStack(spacing: 4) {
            heading.fixedSize()
            Spacer(minLength: 0)
            buttons
        }
        VStack(alignment: .leading, spacing: 0) {
            heading
            buttons.frame(maxWidth: .infinity, alignment: .trailing)
        }
    }
}

private extension View {
    func connectionPanel() -> some View {
        let shape = RoundedRectangle(cornerRadius: 16)
        return padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.white.opacity(0.63), in: shape)
            .overlay { shape.strokeBorder(ConnectionPalette.rule).allowsHitTesting(false) }
    }
}

@main
struct HeinzelApp: App {
    #if os(macOS)
    @NSApplicationDelegateAdaptor(LocalService.self) private var service
    @State private var menu = MacStatusMenu()
    #endif

    init() { WorkspaceNotifications.shared.install() }

    var body: some Scene {
        #if os(macOS)
        Window("Heinzelmännchen", id: "workspace") {
            MacWorkspaceHost(menu: menu, service: service)
                .focusedSceneValue(\.workspaceMenuAvailable, menu.available)
                .focusedSceneValue(\.connectionMenuAvailable, menu.connectionAvailable)
                .frame(minWidth: 550, minHeight: 440)
        }
        .defaultSize(width: 1100, height: 760)
        .windowStyle(.hiddenTitleBar)
        .defaultLaunchBehavior(MacLoginLaunch.isLoginItem ? .suppressed : .automatic)
        .commands {
            WorkspaceCommands()
        }
        MenuBarExtra {
            MacStatusMenuView(menu: menu)
        } label: {
            MacStatusLabel(menu: menu, service: service)
        }
        .menuBarExtraStyle(.window)
        Window("Quick Chat", id: "quick-chat") {
            MacStatusMenuView(menu: menu, quickChatWindow: true)
        }
        .defaultLaunchBehavior(.suppressed)
        .defaultSize(width: 400, height: 560)
        #else
        WindowGroup {
            ConnectionView()
        }
        #endif
    }
}

extension Notification.Name {
    static let showHeinzelConnection = Notification.Name("showHeinzelConnection")
}

#if os(macOS)
private enum MacLoginLaunch {
    static let isLoginItem = NSAppleEventManager.shared().currentAppleEvent?
        .paramDescriptor(forKeyword: AEKeyword(keyAEPropData))?.enumCodeValue == OSType(keyAELaunchedAsLogInItem)
}

/// One retained hosting view keeps the source and its Navigation alive while its window is closed.
private final class MacWorkspaceHostingView: NSHostingView<AnyView> {
    private let visibilityID = UUID().uuidString
    private var visibilityObservers: [NSObjectProtocol] = []
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        for observer in visibilityObservers { NotificationCenter.default.removeObserver(observer) }
        visibilityObservers = []
        if let window {
            for name in [NSWindow.didChangeOcclusionStateNotification, NSWindow.didBecomeKeyNotification,
                         NSWindow.didResignKeyNotification, NSWindow.didMiniaturizeNotification,
                         NSWindow.didDeminiaturizeNotification, NSWindow.willCloseNotification] {
                let closing = name == NSWindow.willCloseNotification
                visibilityObservers.append(NotificationCenter.default.addObserver(forName: name, object: window, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated {
                        self?.updateVisibility(closing: closing)
                    }
                })
            }
        }
        updateVisibility()
    }
    private func updateVisibility(closing: Bool = false) {
        MacMenuHostSource.scheduler.setVisible(!closing && window?.isVisible == true && window?.isMiniaturized == false,
                                              surface: visibilityID)
    }
}

private struct MacWorkspaceHost: NSViewRepresentable {
    let menu: MacStatusMenu
    let service: LocalService
    func makeNSView(context: Context) -> NSHostingView<AnyView> { menu.host(service: service) }
    func updateNSView(_ view: NSHostingView<AnyView>, context: Context) {}
    static func dismantleNSView(_ view: NSHostingView<AnyView>, coordinator: ()) {
        // Return the same source tree to an unordered window when the workspace closes.
        NotificationCenter.default.post(name: .init("parkHeinzelWorkspaceHost"), object: view)
    }
}

private struct MacStatusLabel: View {
    let menu: MacStatusMenu
    let service: LocalService
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Image(systemName: menu.status.symbol)
            .foregroundStyle(menu.status == .attention ? Color.orange : Color.primary)
            .symbolEffect(.pulse, isActive: menu.status == .working)
            .accessibilityLabel(menu.status.label)
            .task {
                _ = menu.host(service: service)
                menu.preferences.start {
                    menu.prepareQuickChat?()
                    openWindow(id: "quick-chat")
                    NSApp.activate(ignoringOtherApps: true)
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
                menu.preferences.refreshLogin()
            }
            .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in
                menu.preferences.stop()
            }
    }
}

@MainActor @Observable
private final class MacStatusMenu {
    let preferences = MenuPreferences()
    @ObservationIgnored private var workspaceHost: NSHostingView<AnyView>?
    @ObservationIgnored private var backgroundWindow: NSWindow?
    @ObservationIgnored private var parkingObserver: NSObjectProtocol?
    func host(service: LocalService) -> NSHostingView<AnyView> {
        if let workspaceHost { return workspaceHost }
        let host = MacWorkspaceHostingView(rootView: AnyView(ConnectionView(service: service, statusMenu: self)))
        host.frame = NSRect(x: 0, y: 0, width: 1100, height: 760)
        workspaceHost = host
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        backgroundWindow = window
        parkingObserver = NotificationCenter.default.addObserver(
            forName: .init("parkHeinzelWorkspaceHost"), object: host, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.backgroundWindow?.contentView = self.workspaceHost
            }
        }
        host.layoutSubtreeIfNeeded()
        return host
    }
    var snapshot: ActivityWidgetData?
    var scope: String?
    var available = false
    var status: MacMenuStatus = .offline

    func configure(scope: String?, available: Bool) {
        guard self.scope != scope || self.available != available else {
            readCache()
            return
        }
        if self.scope != scope { snapshot = nil }
        self.scope = scope
        self.available = available
        restart()
    }

    func restart() {
        MacMenuHostSource.scheduler.didRefresh = { [weak self] in self?.readCache() }
        readCache()
    }

    func readCache() {
        let cached = ActivityWidgetData.read()
        snapshot = available && cached?.visibleInStatusMenu(scope: scope, at: .now) == true ? cached : nil
        status = MacMenuStatus.resolve(cached, scope: scope, available: available, now: .now)
    }
    var pending: NativeRoute?
    var workspaces: [RecentWorkspace] = []
    var selectedWorkspace: String?
    var canSwitchWorkspace = false
    var connectionAvailable = false
    var pendingWorkspace: MacWorkspaceChoice?
    var refreshWorkspaces: (() -> Void)?
    // App-owned models survive popover dismissal and are never reused across scopes.
    let promptDrafts = NativePromptDrafts.shared
    var prepareQuickChat: (() -> Void)?

    var quickChat: NativePromptComposer? {
        guard let snapshot, snapshot.expiresAt > .now, let scope = snapshot.workspaceScope else { return nil }
        return promptDrafts.composer(for: scope)
    }
}

private enum MacWorkspaceChoice: Equatable {
    case local, recent(String), manage
}

private struct MacWorkspaceSwitcher: View {
    let menu: MacStatusMenu
    let select: (MacWorkspaceChoice) -> Void

    var body: some View {
        Button { select(.local) } label: {
            Label("This Mac", systemImage: menu.selectedWorkspace.flatMap({ URL(string: $0) })?.scheme == "http"
                  ? "checkmark" : "desktopcomputer")
        }
        ForEach(menu.workspaces.filter { URL(string: $0.address)?.scheme != "http" }) { item in
            Button { select(.recent(item.address)) } label: {
                Label(item.name, systemImage: item.address == menu.selectedWorkspace ? "checkmark" :
                      (item.source == .iCloud ? "icloud" : "network"))
            }
            .help(item.address)
        }
        Divider()
        Button("Workspaces…") { select(.manage) }
            .onAppear { menu.refreshWorkspaces?() }
    }
}

private struct MacStatusMenuView: View {
    let menu: MacStatusMenu
    var quickChatWindow = false
    @Environment(\.openWindow) private var openWindow
    @Environment(\.dismissWindow) private var dismissWindow
    @State private var visibilityID = UUID().uuidString
    @State private var showingQuickChat = false
    private func open(_ route: NativeRoute) {
        menu.pending = route
        openWindow(id: "workspace")
        NSApp.activate(ignoringOtherApps: true)
    }

    var body: some View {
        ScrollView {
        VStack(alignment: .leading, spacing: 12) {
            if let snapshot = menu.snapshot, snapshot.expiresAt > .now, let scope = snapshot.workspaceScope {
                Text("\(snapshot.awake) awake · \(snapshot.waiting) waiting")
                if snapshot.roster.isEmpty { Text("No Heinzels yet") }
                let attention = snapshot.menuNeedsAttention
                if !attention.isEmpty {
                    Text("NEEDS ATTENTION").font(.caption)
                    statusRows(attention, scope: scope)
                }
                Text("ALL").font(.caption)
                statusRows(snapshot.roster, scope: scope)
            } else {
                Text(menu.status == .offline ? "Workspace offline" : "Workspace status out of date")
            }
            Divider()
            Menu("Switch Workspace") {
                MacWorkspaceSwitcher(menu: menu) { choice in
                    guard menu.canSwitchWorkspace, menu.pendingWorkspace == nil else { return }
                    menu.pendingWorkspace = choice
                    openWindow(id: "workspace")
                    NSApp.activate(ignoringOtherApps: true)
                }
            }
            .disabled(!menu.canSwitchWorkspace || menu.pendingWorkspace != nil)
            .help("Finish or clear your workspace link before switching workspaces.")
            Button("Quick Chat…") {
                menu.prepareQuickChat?()
                showingQuickChat.toggle()
            }
            if showingQuickChat || quickChatWindow {
                if let composer = menu.quickChat {
                    NativePromptSheet(composer: composer, openWorkspace: { open(.workspace) }, openChat: { request in
                        open(.chat(workspace: request.workspaceScope, id: request.recipientID))
                    }, inline: true, closeInline: {
                        showingQuickChat = false
                        if quickChatWindow { dismissWindow(id: "quick-chat") }
                    })
                    .onChange(of: composer.search) { _, _ in menu.prepareQuickChat?() }
                    .onChange(of: composer.page) { _, _ in menu.prepareQuickChat?() }
                } else {
                    Text("Open a workspace to choose a Heinzel. Your drafts are kept for each workspace.")
                        .font(.callout)
                }
            }
            Button("Open Heinzel") { open(.workspace) }
            Divider()
            Text("Quick Chat shortcut · Default ⌥Space").font(.caption)
            if let shortcut = menu.preferences.shortcut {
                Text("Saved shortcut: \(shortcut.label)").font(.caption)
            }
            ShortcutRecorder { menu.preferences.apply($0) }.frame(height: 28)
            HStack {
                Button("Use ⌥Space") { menu.preferences.apply(.standard) }
                Button("Turn Shortcut Off") { menu.preferences.apply(nil) }
            }
            Text(menu.preferences.shortcutMessage).font(.caption)
            Text("Launch at login: \(menu.preferences.loginStatus)").font(.caption)
            HStack {
                Button("Enable at Login") { menu.preferences.setLogin(true) }
                Button("Disable at Login") { menu.preferences.setLogin(false) }
            }
            if let error = menu.preferences.loginError { Text(error).font(.caption) }
        }
        .padding(16)
        }
        .frame(width: 380)
        .frame(maxHeight: 640)
        .onAppear { MacMenuHostSource.scheduler.setVisible(true, surface: visibilityID); menu.restart(); menu.prepareQuickChat?(); menu.preferences.refreshLogin() }
        .onDisappear { MacMenuHostSource.scheduler.setVisible(false, surface: visibilityID) }
    }

    private func statusRows(_ rows: [WidgetHeinzel], scope: String) -> some View {
        ForEach(rows) { heinzel in
            Button { open(.chat(workspace: scope, id: heinzel.id)) } label: {
                VStack(alignment: .leading) {
                    Text("\(heinzel.name) · \(heinzel.status)")
                    if let wake = heinzel.nextWake, wake.isFinite {
                        Text("Next wake \(Date(timeIntervalSince1970: wake), style: .relative)").font(.caption)
                    }
                }
            }
        }
    }
}

private struct WorkspaceMenuAvailableKey: FocusedValueKey {
    typealias Value = Bool
}

private struct ConnectionMenuAvailableKey: FocusedValueKey {
    typealias Value = Bool
}

private extension FocusedValues {
    var workspaceMenuAvailable: Bool? {
        get { self[WorkspaceMenuAvailableKey.self] }
        set { self[WorkspaceMenuAvailableKey.self] = newValue }
    }
    var connectionMenuAvailable: Bool? {
        get { self[ConnectionMenuAvailableKey.self] }
        set { self[ConnectionMenuAvailableKey.self] = newValue }
    }
}

private struct WorkspaceCommands: Commands {
    @FocusedValue(\.workspaceMenuAvailable) private var workspaceAvailable
    @FocusedValue(\.connectionMenuAvailable) private var connectionAvailable
    private let website = "https://heinzel.team"
    private let repository = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"

    var body: some Commands {
        CommandGroup(replacing: .appInfo) {
            Button("About Heinzelmännchen") {
                WorkspaceMenuRequest.send(.about) {
                    NSApp.orderFrontStandardAboutPanel(nil)
                }
            }
        }
        CommandGroup(replacing: .appSettings) {
            command("Settings…", .settings).keyboardShortcut(",")
            command("Check for Updates…", .updates)
            Button("Workspaces…") {
                NotificationCenter.default.post(name: .showHeinzelConnection, object: nil)
            }
            .keyboardShortcut(",", modifiers: [.command, .shift])
            .disabled(connectionAvailable != true)
        }
        CommandGroup(replacing: .newItem) {
            command("New Chat", .newChat).keyboardShortcut("n")
        }
        CommandGroup(before: .toolbar) {
            command("Search…", .search).keyboardShortcut("k")
            command("Toggle Sidebar", .sidebar).keyboardShortcut("b")
            Divider()
            command("Toggle Terminal", .terminal).keyboardShortcut("j")
            command("Toggle Browser", .browser).keyboardShortcut("b", modifiers: [.command, .shift])
            command("Toggle Side Chat", .chat).keyboardShortcut(";")
            command("Show Notifications", .notifications).keyboardShortcut("n", modifiers: [.command, .shift])
            Divider()
            command("Actual Size", .resetZoom).keyboardShortcut("0")
            command("Zoom In", .zoomIn).keyboardShortcut("=")
            command("Zoom Out", .zoomOut).keyboardShortcut("-")
            command("Reload", .reload).keyboardShortcut("r")
        }
        CommandMenu("Go") {
            command("Today", .today).keyboardShortcut("1")
            command("Heinzelmännchen", .heinzel).keyboardShortcut("2")
            command("Team", .team).keyboardShortcut("3")
            command("Plugins", .plugins).keyboardShortcut("4")
        }
        CommandGroup(replacing: .help) {
            external("Heinzelmännchen Help", website + "/")
            external("Getting Started", website + "/guides/install/")
            external("Product Tour", website + "/guides/tour/")
            command("In-App Help", .help)
            external("Plugin Catalog", website + "/CATALOG/")
            command("View Release Notes", .changelog)
            Menu("Troubleshooting") {
                command("Run Doctor", .doctor)
                command("Copy Diagnostics", .copy)
                command("Restart Daemon…", .daemon)
            }
            Divider()
            external("Get Support", repository + "/issues")
            external("Report an Issue…", repository + "/issues/new")
            external("Heinzelmännchen on GitHub", repository)
            Divider()
            external("Privacy Policy", website + "/privacy/")
            external("Imprint", website + "/imprint/")
        }
    }

    private func command(_ title: String, _ action: WorkspaceMenuAction) -> some View {
        Button(title) { WorkspaceMenuRequest.send(action) }
            .disabled(workspaceAvailable != true)
    }

    private func external(_ title: String, _ address: String) -> some View {
        Button(title) {
            if let url = URL(string: address) { NSWorkspace.shared.open(url) }
        }
    }
}
#endif

struct ConnectionView: View {
    @State private var soundDockAvailableSize = CGSize(width: 600, height: 800)
    @State private var soundscape = SoundscapePlayback.shared
    #if os(macOS)
    @ObservedObject var service: LocalService
    @State private var localBackend: Endpoint.Lifecycle?
    @State private var returnAfterLocalRecovery = false
    fileprivate let statusMenu: MacStatusMenu
    #endif

    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openURL) private var openURL
    @State private var started = false
    @State private var showingColdStart = true
    @State private var startupStage = "Preparing Heinzelmännchen…"
    @State private var startupIsSlow = false
    @AppStorage("serverOrigin") private var serverOrigin = ""
    @AppStorage("workspaceHistory") private var workspaceHistory = Data()
    @AppStorage("forgottenWorkspaces") private var forgottenWorkspaces = Data()
    @AppStorage("workspaceBackendVersions") private var workspaceBackendVersions = Data()
    @AppStorage("appBuildObservation") private var appBuildObservation = Data()
    @State private var cloudDirectory = WorkspaceCloudDirectory()
    private var cloudDirectoryUnavailable: Bool { cloudDirectory.access == .unavailable }
    @State private var attemptedCloudSignIns: [String: Date] = [:]
    #if os(macOS)
    @AppStorage(CloudSyncPreference.key) private var syncWorkspaceSignIn = true
    @AppStorage("cloudPublishNoticeShown") private var cloudPublishNoticeShown = false
    @AppStorage("publishedWorkspaceOrigin") private var publishedWorkspaceOrigin = ""
    @State private var syncStatus: String?
    private let offersCloudSignIn = Bundle.main.object(forInfoDictionaryKey: "HeinzelCloudSignIn") as? String == "YES"
    #endif
    @State private var link = ""
    @State private var endpoint: Endpoint?
    private struct PendingSwitch {
        let page: WorkspacePage
        let previousEndpoint: Endpoint?
        let previousRevision: Int
        let remember: Bool
        let source: RecentWorkspace.Source?
        let cloudSignInRejected: Bool
        let menuRevision: Int
        var startsLocal = false
    }

    @State private var pendingSwitch: PendingSwitch?
    @State private var switchSequence = 0
    @State private var connectingOrigin: URL?
    @State private var connectionAttempt: UUID?
    private enum WorkspaceEntry: Equatable {
        case add, signIn(URL)
    }
    @State private var workspaceEntry: WorkspaceEntry?
    private var signInDestination: URL? {
        if case .signIn(let origin) = workspaceEntry { return origin }
        return nil
    }
    @State private var switchCompletion: CheckedContinuation<Bool, Never>?
    @State private var confirmedOrigin: URL?
    @State private var needsSignIn: Set<URL> = []
    @State private var directoryResults: [URL: WorkspaceHostProbe] = [:]
    @State private var directoryHasNetwork: Bool?
    @State private var directoryRevision = 0
    @State private var connecting = false
    @State private var deviceCheckInteracting = false
    @State private var internetClientAddress: String?
    @State private var observedDeviceChecks: [String: String] = [:]
    private var informationDeviceChecks: [(String, String)] {
        ["Internet", "Notifications", "System audio", "iCloud", "Microphone"].map {
            ($0, observedDeviceChecks[$0] ?? "Checking")
        }
    }
    @State private var loadingWorkspace = false
    @State private var workspaceBackground = Color("StartupBackground")
    @State private var error: String?
    @State private var widgetClearFailed = false
    @State private var showConnection = false
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var connectionSheetContentHeight: CGFloat = 350
    @State private var connectionSheetFooterHeight: CGFloat = 88
    private var connectionSheetHeight: CGFloat {
        min(620, max(300, (connectionSheetContentHeight + connectionSheetFooterHeight).rounded(.up)))
    }
    @State private var removalCandidate: RecentWorkspace?
    @State private var choosingWorkspace = false
    @State private var showConnectionHelp = false
    @State private var helpAfterConnection = false
    #if os(iOS)
    @State private var shortcutRouter = AppShortcutRouter.shared
    #endif
    @State private var shortcutDismissal = ShortcutSheetDismissal()
    @State private var pendingShortcut: ShortcutRequest?
    @State private var showHeinzelPicker = false
    @State private var pickerRoster: [WidgetHeinzel] = []
    @State private var pickerSearch = ""
    @State private var pickerPage = 0
    @State private var pickerHasSnapshot = false
    @State private var pickerTimedOut = false
    @State private var widgetRefreshRevision = 0
    @State private var versionRefreshRevision = 0
    @State private var chatRequest: HeinzelChatRequest?
    @State private var showPromptComposer = false
    @State private var dismissingPromptComposer = false
    @State private var disconnectedPromptComposer = NativePromptComposer()
    private var promptComposer: NativePromptComposer {
        guard let scope = widgetWorkspaceScope else { return disconnectedPromptComposer }
        return NativePromptDrafts.shared.composer(for: scope)
    }
    @State private var sharedWorkspace: Endpoint?
    @State private var loadFailure: WorkspaceProblem?
    @State private var loadRevision = 0
    @State private var widgetViewRotation = WidgetViewRotation()
    @State private var pageSessions = WorkspacePageSessions()
    @State private var returnAfterRemoteRecovery = false
    @State private var remoteRecovery: WorkspaceRemoteRecovery?
    @State private var monitorGeneration = 0

    private struct RemoteSessionWatch: Equatable {
        let origin: URL
        let page: Int
        let generation: Int
        let network: Bool?
        let authenticatedPage: Bool
    }

    private var remoteWatch: RemoteSessionWatch? {
        guard let origin = monitoredOrigin, !connecting, !deviceCheckInteracting, workspaceEntry == nil,
              !showConnectionHelp, sharedWorkspace == nil,
              remoteRecovery?.paused != true, remoteRecovery?.issue.canReconnectAutomatically != false else { return nil }
        return RemoteSessionWatch(origin: origin, page: loadRevision, generation: monitorGeneration,
                                  network: routeAvailable(to: origin),
                                  authenticatedPage: confirmedOrigin == origin || remoteRecovery != nil)
    }

    @State private var connectionIssue: WorkspaceProblem?
    #if os(iOS)
    @State private var connectionProbeRevision = 0
    @State private var connectionProbe: ConnectionProbe?
    @State private var connectionScopeRevision = 0
    @State private var signInProbeScope: Int?

    private struct ConnectionProbe {
        let revision: Int
        let origin: URL
        let manual: Bool
        let task: Task<WorkspaceProblem?, Never>
    }
    #endif
    @FocusState private var editingLink: Bool
    #if os(iOS)
    @State private var refreshingConnection = false
    #endif

    #if os(macOS)
    @State private var connectionMenuIntent = MacConnectionIntent()

    private var menuState: MacMenuState {
        MacMenuState(hasEndpoint: endpoint != nil, choosingWorkspace: choosingWorkspace,
                     starting: showingColdStart || connecting || deviceCheckInteracting,
                     loading: loadingWorkspace, hasProblem: visibleProblem != nil,
                     connectionPresented: showConnection,
                     otherSheetPresented: deviceCheckInteracting || workspaceEntry != nil || showConnectionHelp || helpAfterConnection || showHeinzelPicker ||
                        showPromptComposer || removalCandidate != nil || sharedWorkspace != nil)
    }

    private func refreshStatusMenu() {
        refreshWorkspaceMenu()
        statusMenu.configure(scope: widgetWorkspaceScope, available: menuState.workspaceAvailable)
        guard menuState.workspaceAvailable, let snapshot = ActivityWidgetData.read(),
              snapshot.visibleInStatusMenu(scope: widgetWorkspaceScope, at: .now) else {
            statusMenu.snapshot = nil
            return
        }
        statusMenu.snapshot = snapshot
        prepareQuickChat()
    }

    private func prepareQuickChat() {
        guard let snapshot = statusMenu.snapshot, snapshot.expiresAt > .now,
              let scope = snapshot.workspaceScope, scope == widgetWorkspaceScope else { return }
        let composer = statusMenu.promptDrafts.composer(for: scope)
        composer.prepareCatalog(scope: scope, snapshot: snapshot)
    }

    private func refreshWorkspaceMenu() {
        statusMenu.workspaces = recentWorkspaces
        statusMenu.selectedWorkspace = confirmedOrigin?.absoluteString
        statusMenu.canSwitchWorkspace = canSwitchWorkspaceFromMenu
    }

    private var canSwitchWorkspaceFromMenu: Bool {
        !connecting && !deviceCheckInteracting && !showingColdStart && !loadingWorkspace &&
            !MacWorkspaceSwitchState.protectsEditor(link: link, focused: editingLink) &&
            !soundscape.showingDock && !showPromptComposer && !showHeinzelPicker && removalCandidate == nil &&
            !showConnectionHelp && !helpAfterConnection && sharedWorkspace == nil
    }

    private func selectMenuWorkspace(_ choice: MacWorkspaceChoice) async {
        refreshWorkspaceMenu()
        guard statusMenu.canSwitchWorkspace else { return }
        // Discard an old chat destination before changing its workspace scope.
        statusMenu.pending = nil
        switch choice {
        case .local:
            if endpoint?.origin.scheme == "http", loadFailure == nil, service.recovery == nil { return }
            showConnection = false
            if await startLocal() == false {
                var presentation = MacWorkspaceSwitchState(
                    choosingWorkspace: choosingWorkspace, connectionPresented: showConnection,
                    hasEndpoint: endpoint != nil)
                presentation.localStartFailed()
                choosingWorkspace = presentation.choosingWorkspace
                showConnection = presentation.connectionPresented
            }
        case .recent(let address):
            guard let item = recentWorkspaces.first(where: { $0.address == address }) else { return }
            showConnection = false
            await connectRecent(item.address, source: item.source)
        case .manage:
            openConnectionFromMenu()
        }
        refreshStatusMenu()
    }

    private func openConnectionFromMenu() {
        switch menuState.connectionRoute {
        case .setup:
            connectionMenuIntent.requestSetup()
            choosingWorkspace = true
        case .connection: showConnection = true
        case .alreadyVisible, .unavailable: break
        }
    }
    #endif

    private var draftOrigin: URL? {
        draftWorkspace?.origin
    }

    private var draftWorkspace: Endpoint? {
        guard workspaceEntry != nil || choosingWorkspace || endpoint == nil else { return nil }
        return parseWorkspaceLink(link)
    }

    private var forgottenWorkspaceDates: [String: Date] {
        (try? JSONDecoder().decode([String: Date].self, from: forgottenWorkspaces)) ?? [:]
    }

    private var availableCloudWorkspaces: [RecentWorkspace] {
        let forgotten = forgottenWorkspaceDates
        return cloudDirectory.workspaces.filter { $0.lastUsed > (forgotten[$0.address] ?? .distantPast) }
    }

    private var shouldRefreshSyncedWorkspaces: Bool {
        #if os(macOS)
        // The status menu is available even while the workspace window is in the background.
        scenePhase == .active
        #else
        scenePhase == .active && (endpoint == nil || choosingWorkspace || showConnection || loadFailure == .signIn)
        #endif
    }

    private struct DirectoryProbe: Equatable {
        let origins: [URL]
        let revision: Int
    }

    private var directoryVisible: Bool {
        scenePhase == .active && (endpoint == nil || choosingWorkspace || showConnection || loadFailure == .signIn)
    }

    private func routeAvailable(to origin: URL) -> Bool? {
        WorkspaceHostProbe.routeAvailable(networkAvailable: directoryHasNetwork, probe: directoryResults[origin])
    }

    private var directoryProbe: DirectoryProbe? {
        guard directoryVisible, directoryHasNetwork != nil else { return nil }
        let origins = recentWorkspaces.compactMap { Endpoint($0.address)?.origin }
        let paused = remoteRecovery?.paused == true ? endpoint?.origin : nil
        return DirectoryProbe(origins: origins.filter { $0 != paused }, revision: directoryRevision)
    }

    private func checkDirectory(_ probe: DirectoryProbe) async {
        let session = Endpoint.probeSession()
        defer { session.invalidateAndCancel() }
        var origins = probe.origins.makeIterator()
        await withTaskGroup(of: (URL, WorkspaceHostProbe).self) { group in
            func enqueue(_ origin: URL) {
                group.addTask { (origin, await Endpoint.probeHost(at: origin, using: session)) }
            }
            for _ in 0..<3 { if let origin = origins.next() { enqueue(origin) } }
            for await (origin, result) in group {
                guard !Task.isCancelled, probe == directoryProbe else { group.cancelAll(); return }
                directoryResults[origin] = result
                if let origin = origins.next() { enqueue(origin) }
            }
        }
    }

    private var recentWorkspaces: [RecentWorkspace] {
        let local = (try? JSONDecoder().decode([RecentWorkspace].self, from: workspaceHistory)) ?? []
        let forgotten = forgottenWorkspaceDates
        var entries: [String: RecentWorkspace] = [:]
        for item in local + availableCloudWorkspaces {
            guard let target = Endpoint(item.address), target.token == nil, target.origin.absoluteString == item.address else { continue }
            let origin = item.address
            if let known = entries[origin] {
                entries[origin] = RecentWorkspace(address: origin, hostName: item.hostName ?? known.hostName,
                                                  lastUsed: known.lastUsed, source: item.source ?? known.source)
            } else {
                entries[origin] = item
            }
        }
        if let saved = Endpoint(serverOrigin), entries[saved.origin.absoluteString] == nil,
           forgotten[saved.origin.absoluteString] == nil {
            let address = saved.origin.absoluteString
            entries[address] = RecentWorkspace(address: address, lastUsed: .distantPast)
        }
        return entries.values.sorted {
            $0.lastUsed == $1.lastUsed ? $0.name.localizedStandardCompare($1.name) == .orderedAscending : $0.lastUsed > $1.lastUsed
        }
    }

    private func rememberWorkspace(_ target: Endpoint, hostName: String? = nil, source: RecentWorkspace.Source? = nil) {
        guard target.origin.scheme == "https" else { return }
        let address = target.origin.absoluteString
        var local = (try? JSONDecoder().decode([RecentWorkspace].self, from: workspaceHistory)) ?? []
        let previous = local.first(where: { $0.address == address })
        let item = RecentWorkspace(address: address, hostName: hostName ?? previous?.hostName, lastUsed: .now,
                                   source: source ?? previous?.source ?? .pasted)
        local.removeAll { $0.address == address }
        local.insert(item, at: 0)
        if let encoded = try? JSONEncoder().encode(Array(local.prefix(12))) { workspaceHistory = encoded }
    }

    private func forgetWorkspace(_ item: RecentWorkspace) {
        guard item.isUserAdded, !connecting, confirmedOrigin?.absoluteString != item.address else { return }
        if endpoint?.origin.absoluteString == item.address { disconnect() }
        var versions = (try? JSONDecoder().decode([String: WorkspaceVersionObservation].self, from: workspaceBackendVersions)) ?? [:]
        versions.removeValue(forKey: item.address)
        if let encoded = try? JSONEncoder().encode(versions) { workspaceBackendVersions = encoded }
        var local = (try? JSONDecoder().decode([RecentWorkspace].self, from: workspaceHistory)) ?? []
        local.removeAll { $0.address == item.address }
        if let encoded = try? JSONEncoder().encode(local) { workspaceHistory = encoded }
        var forgotten = (try? JSONDecoder().decode([String: Date].self, from: forgottenWorkspaces)) ?? [:]
        let latestSync = cloudDirectory.workspaces.first { $0.address == item.address }?.lastUsed ?? .distantPast
        forgotten[item.address] = max(.now, latestSync)
        if let encoded = try? JSONEncoder().encode(forgotten) { forgottenWorkspaces = encoded }
        if serverOrigin == item.address {
            clearWidgetActivity()
            serverOrigin = ""
        }
    }

    private var diagnosticOrigin: URL? {
        if choosingWorkspace { return draftOrigin }
        if endpoint == nil, let sharedWorkspace { return sharedWorkspace.origin }
        return draftOrigin ?? endpoint?.origin ?? Endpoint(serverOrigin)?.origin
    }

    private var trimmedLink: String { link.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var visibleProblem: WorkspaceProblem? {
        if let loadFailure {
            return loadFailure.canReconnectAutomatically || loadFailure == .signIn ? connectionIssue ?? loadFailure : loadFailure
        }
        return loadingWorkspace ? connectionIssue : nil
    }

    private var monitoredOrigin: URL? {
        guard scenePhase == .active, endpoint?.origin.scheme == "https" else { return nil }
        return endpoint?.origin
    }

    private var canOpenShortcut: Bool {
        scenePhase == .active && !showingColdStart && !deviceCheckInteracting &&
            removalCandidate == nil && !dismissingPromptComposer && shortcutDismissal.waiting == nil &&
            (sharedWorkspace == nil || endpoint == nil)
    }

    private func queueShortcut(_ route: NativeRoute) {
        endConnectionAttempt()
        pendingShortcut = ShortcutRequest(route: route, origin: endpoint?.origin ?? Endpoint(serverOrigin)?.origin)
        advanceShortcutDismissal()
    }

    private func advanceShortcutDismissal() {
        guard pendingShortcut != nil, !deviceCheckInteracting else { return }
        switch shortcutDismissal.next(entry: workspaceEntry != nil, connection: showConnection) {
        case .entry:
            workspaceEntry = nil
            editingLink = false
            link = ""
            error = nil
        case .connection:
            helpAfterConnection = false
            showConnection = false
        case nil: break
        }
    }

    private func shortcutSheetDidDismiss(_ sheet: ShortcutSheetDismissal.Sheet) {
        if shortcutDismissal.finished(sheet) { advanceShortcutDismissal() }
    }

    #if os(iOS)
    private func restoreShortcutWorkspace(for request: ShortcutRequest) async -> Bool {
        guard !connecting, sharedWorkspace == nil else { return false }
        let saved = Endpoint(serverOrigin)?.origin
        switch request.destination(selected: endpoint?.origin, saved: saved) {
        case .superseded: return false
        case .current: return true
        case .restore(let origin):
            if case .chat(let scope, _) = request.route, ActivityWidgetData.currentScope(for: origin) != scope { return true }
            await connectRecent(origin.absoluteString, checkHost: false, preservingShortcut: request.id)
            return endpoint?.origin == origin && sharedWorkspace == nil
        }
    }
    #endif

    private func openShortcut(_ shortcut: NativeRoute) {
        endConnectionAttempt()
        editingLink = false
        if showPromptComposer && shortcut != .writePrompt {
            queueShortcut(shortcut)
            dismissingPromptComposer = true
            showPromptComposer = false
            return
        }
        switch shortcut {
        case .workspace:
            showPromptComposer = false
            showHeinzelPicker = false
            helpAfterConnection = false
            showConnectionHelp = false
            showConnection = false
            if endpoint != nil { choosingWorkspace = false }
        case .connectionHelp:
            if showConnection {
                helpAfterConnection = true
                showConnection = false
            } else {
                showConnectionHelp = true
            }
        case .chooseHeinzel:
            showConnection = false
            showConnectionHelp = false
            showHeinzelPicker = true
        case .writePrompt:
            showConnection = false
            showConnectionHelp = false
            showHeinzelPicker = false
            if endpoint != nil { choosingWorkspace = false }
            showPromptComposer = true
        case .chat(let scope, let id):
            if currentWidgetSnapshot?.workspaceScope == scope, ActivityWidgetData.find(scope: scope, id: id) != nil {
                showConnection = false
                showConnectionHelp = false
                showHeinzelPicker = false
                if endpoint != nil { choosingWorkspace = false }
                chatRequest = HeinzelChatRequest(id: id, workspaceScope: scope)
            } else {
                showHeinzelPicker = true
            }
        }
    }

    private var widgetWorkspaceScope: String? {
        guard let endpoint else { return nil }
        #if os(macOS)
        guard endpoint.origin.scheme == "http" else { return nil }
        #else
        guard endpoint.origin.absoluteString == serverOrigin else { return nil }
        #endif
        return ActivityWidgetData.currentScope(for: endpoint.origin)
    }

    private var currentWidgetSnapshot: ActivityWidgetData? {
        guard let scope = widgetWorkspaceScope, let snapshot = ActivityWidgetData.read(),
              snapshot.workspaceScope == scope else { return nil }
        return snapshot
    }

    private var mayRefreshWidgetActivity: Bool {
        #if os(macOS)
        // Keep the desktop widgets useful while another app is frontmost.
        widgetWorkspaceScope != nil
        #else
        scenePhase == .active && widgetWorkspaceScope != nil
        #endif
    }

    private var heinzelPicker: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                connectionSheetHeader("Choose a Heinzel") {
                    connectionIconButton("xmark", label: "Close") { showHeinzelPicker = false }
                }
                if widgetWorkspaceScope == nil || loadFailure != nil {
                    #if os(macOS)
                    Text("Open the local workspace to load its Heinzels.")
                        .font(.callout).foregroundStyle(ConnectionPalette.muted)
                    Button("Open Local Workspace") {
                        showHeinzelPicker = false
                        if endpoint?.origin.scheme != "http" {
                            Task { await startLocal() }
                        } else if loadFailure != nil {
                            retryWorkspace()
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    #else
                    Text("Open your current workspace to load its Heinzels.")
                        .font(.callout).foregroundStyle(ConnectionPalette.muted)
                    Button("Open Workspace") {
                        showHeinzelPicker = false
                        if !serverOrigin.isEmpty, endpoint?.origin.absoluteString != serverOrigin {
                            Task { await connectRecent(serverOrigin) }
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    #endif
                } else if pickerRoster.isEmpty && pickerSearch.isEmpty && pickerPage == 0 {
                    if pickerHasSnapshot {
                        Text("No Heinzels yet").font(.callout).foregroundStyle(ConnectionPalette.muted)
                    } else if pickerTimedOut {
                        Text("Heinzels are unavailable right now.")
                            .font(.callout).foregroundStyle(ConnectionPalette.muted)
                        Button("Try Again") { widgetRefreshRevision += 1 }.buttonStyle(.borderedProminent)
                    } else {
                        ProgressView("Loading Heinzels…").controlSize(.small)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 0) {
                        TextField("Search Heinzels", text: $pickerSearch).onChange(of: pickerSearch) { pickerPage = 0 }
                        ForEach(pickerRoster) { item in
                            Button {
                                if let snapshot = currentWidgetSnapshot, let scope = snapshot.workspaceScope,
                                   ActivityWidgetData.find(scope: scope, id: item.id) != nil {
                                    chatRequest = HeinzelChatRequest(id: item.id, workspaceScope: scope)
                                    showHeinzelPicker = false
                                }
                            } label: {
                                HStack(spacing: 10) {
                                    if let image = item.portraitImage {
                                        image.resizable().scaledToFit()
                                            .frame(width: 35, height: 35).accessibilityHidden(true)
                                    }
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(item.name).fontWeight(.medium)
                                        Text(item.status).font(.caption).foregroundStyle(ConnectionPalette.muted)
                                        Text(item.id).font(.caption2).foregroundStyle(ConnectionPalette.muted)
                                    }
                                    Spacer(minLength: 4)
                                    Image(systemName: "chevron.forward").accessibilityHidden(true)
                                }
                                .frame(minHeight: 48).contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                        HStack {
                            Button("Previous") { pickerPage -= 1 }.disabled(pickerPage == 0)
                            Button("Next") { pickerPage += 1 }.disabled(pickerRoster.count < 50)
                        }
                    }
                    .connectionPanel()
                }
            }
            .frame(maxWidth: 420, alignment: .leading)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            ConnectionFooter().background(Color("StartupBackground"))
        }
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
        .presentationBackground(Color("StartupBackground"))
        #if os(macOS)
        .frame(width: 460, height: 440)
        #endif
        .task(id: widgetRefreshRevision) {
            pickerTimedOut = false
            let start = ContinuousClock.now
            while !Task.isCancelled {
                let snapshot = currentWidgetSnapshot
                if let scope = snapshot?.workspaceScope, let generation = snapshot?.generation {
                    pickerRoster = ActivityWidgetData.search(scope: scope, generation: generation, query: pickerSearch, offset: pickerPage * 50).map { entry in
                        snapshot?.roster.first(where: { $0.id == entry.id }) ?? entry.widgetHeinzel
                    }
                } else { pickerRoster = [] }
                pickerHasSnapshot = snapshot != nil
                pickerTimedOut = ContinuousClock.now >= start + .seconds(8)
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
    }

    private var menuContent: some View {
        content
        .accessibilityHidden(choosingWorkspace)
        .allowsHitTesting(!choosingWorkspace)
        .overlay {
            if choosingWorkspace { setup }
        }
        #if os(macOS)
        .onChange(of: menuState.connectionRoute != .unavailable, initial: true) { _, available in
            statusMenu.connectionAvailable = available
        }
        .onReceive(NotificationCenter.default.publisher(for: .showHeinzelConnection)) { _ in
            openConnectionFromMenu()
        }
        .onChange(of: menuState.workspaceAvailable ? endpoint : nil, initial: true) { _, _ in
            refreshStatusMenu()
        }
        .onChange(of: confirmedOrigin) { _, _ in refreshWorkspaceMenu() }
        .onChange(of: workspaceHistory) { _, _ in refreshWorkspaceMenu() }
        .onChange(of: forgottenWorkspaces) { _, _ in refreshWorkspaceMenu() }
        .onChange(of: cloudDirectoryUnavailable) { _, _ in refreshWorkspaceMenu() }
        .onChange(of: cloudDirectory.workspaces.map { "\($0.address)|\($0.name)|\($0.source?.rawValue ?? "")" }) { _, _ in
            refreshWorkspaceMenu()
        }
        .onChange(of: canSwitchWorkspaceFromMenu) { _, _ in refreshWorkspaceMenu() }
        .onAppear {
            service.onLost = { presentLocalLoss() }
            service.verifyRecoveredPage = { try await verifyLocalPage($0) }
            service.onRecovered = { restoreLocalSession($0) }
            service.setForeground(NSApp.isActive)
            statusMenu.prepareQuickChat = { prepareQuickChat() }
            statusMenu.refreshWorkspaces = {
                refreshSyncedWorkspaces()
                refreshWorkspaceMenu()
            }
        }
        .task(id: statusMenu.pendingWorkspace) { @MainActor in
            guard let choice = statusMenu.pendingWorkspace else { return }
            // Keep the claim until completion so repeated clicks cannot start another connection.
            await selectMenuWorkspace(choice)
            statusMenu.pendingWorkspace = nil
        }
        .onReceive(NotificationCenter.default.publisher(for: ActivityWidgetData.changed)) { _ in
            refreshStatusMenu()
        }
        .task(id: statusMenu.snapshot?.expiresAt) {
            guard let expiry = statusMenu.snapshot?.expiresAt else { return }
            if expiry > .now {
                do { try await Task.sleep(for: .seconds(expiry.timeIntervalSinceNow)) } catch { return }
            }
            if statusMenu.snapshot?.expiresAt == expiry { statusMenu.readCache() }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            service.setForeground(true)
            directoryRevision &+= 1
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.willResignActiveNotification)) { _ in
            service.setForeground(false)
        }
        .onReceive(NSWorkspace.shared.notificationCenter.publisher(for: NSWorkspace.willSleepNotification)) { _ in
            service.setSleeping(true)
        }
        .onReceive(NSWorkspace.shared.notificationCenter.publisher(for: NSWorkspace.didWakeNotification)) { _ in
            service.setSleeping(false)
        }
        .onDisappear {
            service.setForeground(false)
            service.onLost = nil
            service.verifyRecoveredPage = nil
            service.onRecovered = nil
            statusMenu.refreshWorkspaces = nil
        }
        .task(id: canOpenShortcut ? statusMenu.pending : nil) { @MainActor in
            guard !Task.isCancelled, canOpenShortcut, let route = statusMenu.pending else { return }
            statusMenu.pending = nil
            queueShortcut(route)
        }
        #endif
    }

    private var presentedContent: some View {
        menuContent
        .onGeometryChange(for: CGSize.self) { $0.size } action: { soundDockAvailableSize = $0 }
        #if !os(macOS)
        .sheet(isPresented: $soundscape.showingDock) {
            SoundDock(playback: soundscape, availableSize: soundDockAvailableSize)
                .presentationSizing(.fitted)
        }
        #endif
        .sheet(isPresented: Binding(get: { workspaceEntry != nil && !showConnection }, set: { showing in
            if !showing && !showConnection && workspaceEntry != nil { dismissWorkspaceEntry() }
        }), onDismiss: { shortcutSheetDidDismiss(.entry) }) { linkEntrySheet }
        .sheet(isPresented: Binding(get: { showConnection }, set: { showing in
            if showing { showConnection = true } else { dismissConnectionPicker() }
        }), onDismiss: {
            shortcutSheetDidDismiss(.connection)
            if helpAfterConnection {
                helpAfterConnection = false
                showConnectionHelp = true
            }
        }) {
            if let endpoint { connectionSheet(for: endpoint) }
        }
        .onChange(of: showConnection) { _, open in
            if open {
                versionRefreshRevision &+= 1
            } else if pendingSwitch != nil && !choosingWorkspace {
                endConnectionAttempt(supersedeShortcut: true)
            }
        }
        .sheet(isPresented: $showConnectionHelp, onDismiss: { directoryRevision += 1 }) {
            ConnectionHelp(
                origin: diagnosticOrigin,
                activeWorkspace: !choosingWorkspace && draftOrigin == nil && loadFailure == nil && !loadingWorkspace ? endpoint : nil,
                needsSignIn: !choosingWorkspace && loadFailure == .signIn,
                workspaceIsCurrent: { target in endpoint == target && loadFailure == nil && !loadingWorkspace }
            )
                .presentationBackground(ConnectionPalette.appSurface)
        }
        .sheet(isPresented: $showHeinzelPicker) { heinzelPicker }
        .sheet(isPresented: $showPromptComposer, onDismiss: { dismissingPromptComposer = false }) {
            NativePromptSheet(composer: promptComposer, openWorkspace: {
                showPromptComposer = false
            }, openChat: { request in
                showPromptComposer = false
                if request.workspaceScope == widgetWorkspaceScope {
                    chatRequest = HeinzelChatRequest(id: request.recipientID, workspaceScope: request.workspaceScope)
                }
            })
            .presentationBackground(Color("StartupBackground"))
            .task {
                widgetRefreshRevision += 1
                while !Task.isCancelled {
                    promptComposer.prepareCatalog(scope: widgetWorkspaceScope, snapshot: currentWidgetSnapshot)
                    do { try await Task.sleep(for: .milliseconds(500)) } catch { return }
                }
            }
        }
    }

    private var lifecycleContent: some View {
        presentedContent
        .task {
            guard !started else { return }
            started = true
            if let build = heinzelAppBuild {
                let previous = try? JSONDecoder().decode(AppBuildObservation.self, from: appBuildObservation)
                let observation = AppBuildObservation.recording(build, previous: previous, now: .now)
                if observation != previous, let encoded = try? JSONEncoder().encode(observation) {
                    appBuildObservation = encoded
                }
            }
            #if os(macOS)
            // AppKit moves this retained host from its parking window into the visible window.
            // That transfer cancels view tasks while the one-shot local attach is still running.
            Task { @MainActor in
                await openInitialWorkspace()
                showingColdStart = false
            }
            #else
            await openInitialWorkspace()
            showingColdStart = false
            #endif
        }
        .task(id: shouldRefreshSyncedWorkspaces) {
            var fastReads = 0
            while !Task.isCancelled, shouldRefreshSyncedWorkspaces {
                refreshSyncedWorkspaces()
                resumeCloudSignInIfReady()
                let delay = fastReads < 5 ? 2 : 10
                fastReads += 1
                do { try await Task.sleep(for: .seconds(delay)) } catch { return }
            }
        }
        #if os(macOS)
        .task(id: syncWorkspaceSignIn ? service.publishingSession : nil) {
            await publishLocalSignIn()
        }
        .onChange(of: syncWorkspaceSignIn) { _, enabled in
            if !enabled { removePublishedSignIn() }
        }
        #endif
        .onChange(of: deviceCheckInteracting) { _, busy in
            if !busy { advanceShortcutDismissal() }
        }
        .onChange(of: scenePhase) { _, phase in
            #if os(iOS)
            if phase != .active, signInProbeScope == connectionScopeRevision {
                loadFailure = .signIn
                loadingWorkspace = false
            }
            invalidateConnectionScope()
            #endif
        }

        .task(id: pendingSwitch?.page.id) {
            guard let revision = pendingSwitch?.page.id else { return }
            do { try await Task.sleep(for: .seconds(12)) } catch { return }
            finishPendingSwitch(revision, issue: .slow)
        }
        .onDisappear {
            #if os(macOS)
            if showingColdStart { return }
            #endif
            endConnectionAttempt(supersedeShortcut: true)
        }
        .onReceive(NotificationCenter.default.publisher(for: ActivityWidgetData.clearFailed)) { _ in widgetClearFailed = true }
        .onReceive(NotificationCenter.default.publisher(for: WidgetCredentialStore.activationChanged).receive(on: RunLoop.main)) { notification in
            let activation = ActivityWidgetData.currentActivation()
            guard widgetViewRotation.shouldReload(notificationID: notification.object as? String,
                                                  currentID: activation?.id, currentEndpoint: activation?.endpoint,
                                                  visibleEndpoint: endpoint?.origin.absoluteString) else { return }
            retryWorkspace()
        }
        .onReceive(NotificationCenter.default.publisher(for: WidgetCredentialStore.preparationRecovered).receive(on: RunLoop.main)) { notification in
            guard let activation = ActivityWidgetData.currentActivation(),
                  notification.object as? String == activation.id,
                  activation.endpoint == endpoint?.origin.absoluteString else { return }
            retryWorkspace()
        }
        .onReceive(NotificationCenter.default.publisher(for: WidgetCredentialStore.receiptRetired).receive(on: RunLoop.main)) { notification in
            let retired = notification.object as? ActivityCatalog.Activation
            guard WidgetViewRotation.shouldReloadRetirement(retired, current: ActivityWidgetData.currentActivation(),
                endpoint: retired?.endpoint, visibleEndpoint: endpoint?.origin.absoluteString,
                credentialFree: retired?.credentialGeneration == nil) else { return }
            retryWorkspace()
        }
        .alert("Widget information could not be cleared", isPresented: $widgetClearFailed) {
            Button("Retry") { Task { await Task.yield(); clearWidgetActivity() } }
            Button("Close", role: .cancel) { }
        } message: {
            Text("Widgets may still display information from the previous workspace. Retry clearing it from this device.")
        }
    }

    var body: some View {
        lifecycleContent
        .task(id: scenePhase == .active) {
            directoryHasNetwork = nil
            directoryResults.removeAll()
            guard scenePhase == .active else { return }
            let monitor = NWPathMonitor()
            defer { monitor.cancel() }
            for await path in monitor {
                guard !Task.isCancelled else { return }
                directoryResults.removeAll()
                switch path.status {
                case .satisfied: directoryHasNetwork = true
                case .unsatisfied: directoryHasNetwork = false
                case .requiresConnection: directoryHasNetwork = nil
                @unknown default: directoryHasNetwork = nil
                }
                directoryRevision &+= 1
            }
        }
        #if os(macOS)
        .task(id: directoryVisible && service.recovery == nil ? service.localSession : nil) {
            localBackend = nil
            guard directoryVisible, service.recovery == nil, service.localSession != nil else { return }
            while !Task.isCancelled {
                let backend = await service.runningBackend()
                guard !Task.isCancelled else { return }
                localBackend = backend
                do { try await Task.sleep(for: .seconds(10)) } catch { return }
            }
        }
        #endif
        .task(id: directoryProbe) {
            guard let probe = directoryProbe else { return }
            while !Task.isCancelled {
                await checkDirectory(probe)
                // With no default path, make one bounded check per destination on
                // discovery or path change. Do not poll unreachable destinations.
                guard directoryHasNetwork == true else { return }
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
            }
        }
        .task(id: remoteWatch) {
            #if os(iOS)
            connectionProbeRevision += 1
            connectionProbe?.task.cancel()
            connectionProbe = nil
            #endif
            guard let watch = remoteWatch else { return }
            if watch.authenticatedPage {
                await watchRemoteSession(watch)
            } else {
                await watchConnection(to: watch.origin)
            }
        }
        .task(id: canOpenShortcut ? pendingShortcut?.id : nil) { @MainActor in
            guard !Task.isCancelled, canOpenShortcut, let request = pendingShortcut else { return }
            #if os(iOS)
            let restored = await restoreShortcutWorkspace(for: request)
            guard !Task.isCancelled, pendingShortcut?.id == request.id else { return }
            pendingShortcut = nil
            guard restored else { return }
            #else
            pendingShortcut = nil
            #endif
            openShortcut(request.route)
        }
        #if os(iOS)
        .task(id: shortcutRouter.pending) { @MainActor in
            guard !Task.isCancelled, let shortcut = shortcutRouter.pending else { return }
            shortcutRouter.pending = nil
            queueShortcut(shortcut)
        }
        #endif
        .onOpenURL { url in
            if url == SoundscapeLink.artist {
                openURL(SoundscapeLink.artist)
                return
            }
            if SoundscapeLink.handles(url) {
                if url == SoundscapeLink.picker { soundscape.openPicker() }
                else { soundscape.dockWidth = nil; soundscape.openVideo() }
                return
            }
            if let shortcut = NativeRoute(url: url) {
                queueShortcut(shortcut)
                return
            }
            guard let target = Endpoint.sharedWorkspace(url) else {
                if endpoint == nil { error = "This Heinzel link is incomplete. Ask the host to share it again." }
                return
            }
            endConnectionAttempt(supersedeShortcut: true)
            if endpoint?.owns(target.origin) == true {
                if loadFailure != nil { retryWorkspace() }
            } else {
                sharedWorkspace = target
            }
        }
        .alert("Open workspace?", isPresented: Binding(
            get: { sharedWorkspace != nil && endpoint != nil && !showConnection && !showConnectionHelp && !connecting },
            set: { if !$0 { sharedWorkspace = nil } }
        ), presenting: sharedWorkspace) { target in
            Button("Open") {
                sharedWorkspace = nil
                Task { await connect(target.origin.absoluteString) }
            }
            Button("Cancel", role: .cancel) {
                // SwiftUI dismisses the alert without changing the selected workspace.
            }
        } message: { target in
            Text(target.origin.absoluteString)
        }
    }

    private func openInitialWorkspace() async {
        #if DEBUG
        if let link = ProcessInfo.processInfo.environment["HZ_SPIKE_SERVER"] {
            startupStage = "Checking workspace connection…"
            await connect(link, remember: false, checkHost: false, initialStartup: true)
            return
        }
        #endif
        #if os(macOS)
        startupStage = "Starting the local workspace…"
        await startLocal(initialStartup: true)
        #else
        startupStage = "Looking for known workspaces…"
        refreshSyncedWorkspaces()
        #endif
    }

    private var requestedChat: HeinzelChatRequest? {
        !loadingWorkspace && loadFailure == nil ? chatRequest : nil
    }

    private func workspacePage(_ page: WorkspacePage, obscured: Bool) -> some View {
        let endpoint = page.endpoint
        let revision = page.id
        return WorkspaceView(endpoint: endpoint, isObscured: obscured,
                      registerPage: { pageSessions.register($0, page: revision) },
                      reportsWidgetActivity: mayRefreshWidgetActivity && !obscured,
                      isSelected: { self.endpoint == endpoint && loadRevision == revision },
                      requiresVerification: {
                          pendingSwitch?.page.id == revision ||
                              (self.endpoint == endpoint && loadRevision == revision && confirmedOrigin != endpoint.origin)
                      },
                      isWidgetWorkspaceCurrent: {
            self.endpoint == endpoint && loadRevision == revision && widgetWorkspaceScope != nil
        }, isMenuWorkspaceCurrent: {
            #if os(macOS)
            self.endpoint == endpoint && loadRevision == revision && menuState.workspaceAvailable
            #else
            self.endpoint == endpoint && loadRevision == revision && scenePhase == .active &&
                confirmedOrigin == endpoint.origin && !choosingWorkspace && !showConnection &&
                workspaceEntry == nil && pendingSwitch == nil && !loadingWorkspace && loadFailure == nil
            #endif
        }, openWorkspaceChooser: {
            guard self.endpoint == endpoint, confirmedOrigin == endpoint.origin, loadRevision == revision,
                  !choosingWorkspace, !showConnection, workspaceEntry == nil, !connecting else { return false }
            #if os(macOS)
            openConnectionFromMenu()
            #else
            showConnection = true
            #endif
            return showConnection
        }, selectedWorkspaceName: {
            self.endpoint == endpoint && confirmedOrigin == endpoint.origin && loadRevision == revision
                ? workspaceName(endpoint.origin) : nil
        }, isEnrollmentViewCurrent: {
            #if os(iOS)
            self.endpoint == endpoint && loadRevision == revision && !choosingWorkspace && !showingColdStart && scenePhase == .active
            #else
            self.endpoint == endpoint && loadRevision == revision && !choosingWorkspace && !showingColdStart
            #endif
        }, chatRequest: requestedChat, promptComposer: promptComposer, widgetRefreshRevision: widgetRefreshRevision,
        versionRefreshRevision: versionRefreshRevision, ready: {
            if pendingSwitch?.page.id == revision { finishPendingSwitch(revision, issue: nil); return }
            guard self.endpoint == endpoint, loadRevision == revision, remoteRecovery == nil else { return }
            #if os(macOS)
            if service.recovery != nil && endpoint == service.localSession { return }
            #endif
            confirmedOrigin = endpoint.origin
            loadingWorkspace = false
            loadFailure = nil
        }, reportHostName: { name in
            guard self.endpoint == endpoint, serverOrigin == endpoint.origin.absoluteString,
                  loadRevision == revision else { return }
            rememberWorkspace(endpoint, hostName: name)
        }, reportBackendVersion: { observation in
            guard self.endpoint == endpoint else { return }
            var versions = (try? JSONDecoder().decode([String: WorkspaceVersionObservation].self, from: workspaceBackendVersions)) ?? [:]
            guard WorkspaceVersionObservation.record(observation, origin: endpoint.origin.absoluteString,
                selectedOrigin: self.endpoint?.origin.absoluteString ?? "", requestRevision: revision, currentRevision: loadRevision,
                into: &versions) else { return }
            let newest = versions.sorted { $0.value.observedAt > $1.value.observedAt }.prefix(12)
            if let encoded = try? JSONEncoder().encode(Dictionary(uniqueKeysWithValues: newest.map { ($0.key, $0.value) })) {
                workspaceBackendVersions = encoded
            }
        }, reportError: { message in
            if pendingSwitch?.page.id == revision { finishPendingSwitch(revision, issue: message); return }
            guard self.endpoint == endpoint, loadRevision == revision else { return }
            #if os(macOS)
            if endpoint == service.localSession, confirmedOrigin == endpoint.origin || service.recovery != nil,
               message != .interrupted {
                service.reportPageIssue(message, for: endpoint)
                return
            }
            #endif
            if endpoint.origin.scheme == "https", confirmedOrigin == endpoint.origin || remoteRecovery != nil,
               message != .interrupted {
                if remoteRecovery?.paused != true { monitorGeneration &+= 1 }
                return
            }
            if message.revokesAccess { confirmedOrigin = nil }
            if message == .signIn && endpoint.origin.scheme == "https" {
                needsSignIn.insert(endpoint.origin)
                // The navigation error has no captured secure activation.
                // Do not let it revoke a newer credential at this origin.
                let signInWidgetToken = ActivityWidgetData.legacyRefreshToken()
                #if os(iOS)
                guard scenePhase == .active else {
                    loadFailure = .signIn
                    loadingWorkspace = false
                    return
                }
                let scope = connectionScopeRevision
                signInProbeScope = scope
                #endif
                Task {
                    #if os(iOS)
                    defer { if signInProbeScope == scope { signInProbeScope = nil } }
                    while self.endpoint == endpoint, loadRevision == revision,
                          connectionScopeRevision == scope, scenePhase == .active, !Task.isCancelled {
                        guard let result = await sharedConnectionProbe(endpoint.origin, manual: false) else { continue }
                        guard self.endpoint == endpoint, loadRevision == revision,
                              connectionScopeRevision == scope, scenePhase == .active else { return }
                        guard connectionProbe?.revision == result.revision else { continue }
                        connectionIssue = result.issue
                        loadFailure = result.issue ?? .signIn
                        if result.issue == nil, widgetWorkspaceScope != nil, let signInWidgetToken {
                            _ = WidgetCredentialStore.clearShared(expectedToken: signInWidgetToken)
                            ActivityWidgetData.reload()
                        }
                        loadingWorkspace = false
                        finishConnectionProbe(result.revision)
                        return
                    }
                    #else
                    let issue = await Endpoint.connectionIssue(at: endpoint.origin)
                    guard self.endpoint == endpoint, loadRevision == revision else { return }
                    connectionIssue = issue
                    loadFailure = issue ?? .signIn
                    if issue == nil, widgetWorkspaceScope != nil, let signInWidgetToken {
                        _ = WidgetCredentialStore.clearShared(expectedToken: signInWidgetToken)
                        ActivityWidgetData.reload()
                    }
                    loadingWorkspace = false
                    #endif
                }
            } else {
                loadingWorkspace = false
                loadFailure = message
            }
        }, appearance: { color in
            guard self.endpoint == endpoint, loadRevision == revision else { return }
            workspaceBackground = color
        })
            .id(endpoint.url)
            .id(revision)
    }

    @ViewBuilder
    private var content: some View {
        if endpoint != nil, loadFailure == .signIn, pendingSwitch == nil {
            if showingColdStart { startup } else { setup }
        } else if let endpoint = endpoint ?? pendingSwitch?.page.endpoint {
            let revision = loadRevision
            let obscured = self.endpoint == nil || choosingWorkspace || showingColdStart || loadingWorkspace || visibleProblem != nil
            VStack(spacing: 0) {
                WorkspaceSwitchPages(current: self.endpoint.map { WorkspacePage(endpoint: $0, id: revision) },
                                     pending: pendingSwitch?.page, obscured: obscured) { page, hidden in
                    workspacePage(page, obscured: hidden)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .opacity(obscured ? 0 : 1)
                #if os(iOS)
                .background {
                    if !obscured { workspaceBackground.ignoresSafeArea(.container) }
                }
                #endif
                .allowsHitTesting(!obscured)
                .task(id: loadingWorkspace) {
                    guard loadingWorkspace else { return }
                    do { try await Task.sleep(for: .seconds(12)) } catch { return }
                    guard self.endpoint == endpoint, loadRevision == revision, loadingWorkspace else { return }
                    loadingWorkspace = false
                    loadFailure = .slow
                }
                .overlay(alignment: .top) {
                    if let connectionIssue, !obscured {
                        Button {
                            showConnection = true
                        } label: {
                            HStack(spacing: 8) {
                                Label(connectionIssue.title, systemImage: connectionIssue.icon)
                                Image(systemName: "chevron.forward").accessibilityHidden(true)
                            }
                            .font(.footnote.weight(.medium))
                            .padding(.horizontal, 12)
                            .frame(minHeight: 44)
                            .background(.regularMaterial, in: Capsule())
                        }
                        .buttonStyle(.plain)
                        .accessibilityHint("Opens Workspace Connection")
                        .padding(.top, 8)
                    }
                }
                .accessibilityHidden(obscured)
                .overlay {
                    if showingColdStart {
                        startup
                    } else if self.endpoint == nil || loadFailure == .signIn {
                        if !choosingWorkspace { setup }
                    } else if let problem = visibleProblem {
                        recovery(problem, for: endpoint)
                    } else if loadingWorkspace {
                        startup
                    }
                }
                #if os(macOS)
                .ignoresSafeArea(edges: .top)
                #endif
            }
        } else if showingColdStart {
            startup
        } else if let target = sharedWorkspace {
            launchCard(compactBrand: true) {
                Text("Open workspace?")
                    .font(.system(.title2, design: .serif, weight: .semibold))
                    .accessibilityAddTraits(.isHeader)
                workspaceAddress(target.origin)
                Button("Open") {
                    sharedWorkspace = nil
                    Task { await connect(target.origin.absoluteString) }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                Button("Cancel", role: .cancel) { sharedWorkspace = nil }
                    .buttonStyle(.borderless)
            }
        } else {
            setup
        }
    }

    private func connectionSheet(for endpoint: Endpoint) -> some View {
        WorkspaceInformationSurface(version: heinzelAppBuild?.version, background: ConnectionPalette.appSurface) { information in
            connectedDirectory(for: endpoint, information: information)
        } details: { workspaceInformation }
        #if os(macOS)
        .frame(width: 460, height: connectionSheetHeight)
        #endif
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
        .background(ConnectionPalette.appSurface)
        .presentationBackground(ConnectionPalette.appSurface)
        .sheet(isPresented: Binding(get: { workspaceEntry != nil }, set: { showing in
            if !showing && workspaceEntry != nil { dismissWorkspaceEntry() }
        }), onDismiss: { shortcutSheetDidDismiss(.entry) }) { linkEntrySheet }
        .interactiveDismissDisabled(deviceCheckInteracting)
        .presentationDetents([.height(connectionSheetHeight), .large])

    }

    private func connectedDirectory(for endpoint: Endpoint, information: WorkspaceInformationButton) -> some View {
        let problem = visibleProblem ?? connectionIssue
        return ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                #if os(macOS)
                connectionSheetHeader("Heinzel App") {
                    if let shareURL = endpoint.shareURL {
                        ShareLink(item: shareURL) { connectionIcon("square.and.arrow.up", label: "Share Workspace Link") }
                            .buttonStyle(.plain)
                            .help("Share Workspace Link")
                    }
                    connectionIconButton("questionmark.circle", label: "Connection Help") {
                        helpAfterConnection = true
                        showConnection = false
                    }
                    connectionIconButton("xmark", label: "Close") { showConnection = false }
                }
                #endif
                workspaceDirectory
                if let problem, problem != .signIn {
                    Button("Retry " + workspaceName(endpoint.origin), systemImage: "arrow.clockwise") {
                        showConnection = false
                        retryWorkspace()
                    }
                }
                #if os(macOS)
                if endpoint.origin.scheme == "http", offersCloudSignIn {
                    VStack(alignment: .leading, spacing: 4) {
                        Toggle("Sync Sign-in with iCloud", isOn: $syncWorkspaceSignIn)
                        if syncWorkspaceSignIn, let syncStatus {
                            Text(syncStatus).font(.footnote).foregroundStyle(ConnectionPalette.muted)
                        }
                    }
                    .connectionPanel()
                }
                #endif
                if dynamicTypeSize.isAccessibilitySize { directoryFooter(information) }
            }
            .frame(maxWidth: 420, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, 16)
            .frame(maxWidth: .infinity)
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height in
                connectionSheetContentHeight = height
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if !dynamicTypeSize.isAccessibilitySize {
                directoryFooter(information).onGeometryChange(for: CGFloat.self) { $0.size.height } action: { connectionSheetFooterHeight = $0 }
            }
        }
        #if os(iOS)
        .refreshable { directoryRevision += 1; await refreshConnectionStatus(for: endpoint) }
        #endif
    }

    private func recovery(_ problem: WorkspaceProblem, for endpoint: Endpoint) -> some View {
        launchCard(compactBrand: true) {
            Label(problem.title, systemImage: problem.icon)
                .font(.system(.title2, design: .serif, weight: .semibold))
                .foregroundStyle(ConnectionPalette.ink)
                .accessibilityAddTraits(.isHeader)
            if problem == .signIn {
                Label("Workspace reachable", systemImage: "checkmark.circle.fill")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(ConnectionPalette.accent)
            } else {
                Text(problem == .unavailable && endpoint.origin.host?.hasSuffix(".ts.net") == true
                    ? "Check Tailscale and make sure the host computer is awake. Heinzel will try again."
                    : problem.detail)
                    .font(.callout)
                    .foregroundStyle(ConnectionPalette.muted)
            }
            if endpoint.origin.scheme == "https" {
                workspaceAddress(endpoint.origin, opensConnection: !choosingWorkspace)
            }
            if problem == .signIn {
                Button("Sign In", systemImage: "person.crop.circle") { showWorkspaceEntry(.signIn(endpoint.origin)) }
                    .buttonStyle(.borderedProminent)
                Button(workspaceSwitchTitle, systemImage: workspaceSwitchSymbol, action: chooseAnotherWorkspace)
                    .buttonStyle(.borderless)
            } else if problem.shouldEnterLink {
                Button("Enter Workspace Link", action: chooseAnotherWorkspace)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
            } else {
                Button("Try Again") { retryWorkspace() }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                Button(workspaceSwitchTitle, systemImage: workspaceSwitchSymbol, action: chooseAnotherWorkspace)
                    .buttonStyle(.bordered)
                    .controlSize(.large)
            }
        }
        .multilineTextAlignment(.center)
    }

    private func workspaceAddress(_ origin: URL, opensConnection: Bool = false) -> some View {
        HStack(spacing: 6) {
            if opensConnection {
                connectionIconButton("point.3.connected.trianglepath.dotted", label: "Workspace") {
                    showConnection = true
                }
            } else {
                Image(systemName: "link").accessibilityHidden(true)
            }
            Group {
                if opensConnection { workspaceIdentity(origin).font(.callout.weight(.medium)) }
                else { Text(origin.absoluteString).font(.footnote.monospaced()) }
            }
            .textSelection(.enabled)
            .padding(.vertical, 8)
        }
        .foregroundStyle(ConnectionPalette.muted)
        .multilineTextAlignment(.leading)
        .padding(.leading, opensConnection ? 0 : 12)
        .padding(.trailing, 12)
        .background(.white.opacity(0.6), in: RoundedRectangle(cornerRadius: 8))
    }

    private func changeLink(_ value: String) {
        endConnectionAttempt(supersedeShortcut: true)
        link = value
        error = nil
    }

    private func linkField(_ title: String, submit: @escaping () -> Void) -> some View {
        HStack(spacing: 8) {
            SecureField(title, text: Binding(get: { link }, set: changeLink))
                .textFieldStyle(.plain)
                .autocorrectionDisabled()
                #if os(iOS)
                .textInputAutocapitalization(.never)
                .keyboardType(.URL)
                .submitLabel(.go)
                #endif
                .focused($editingLink)
                .accessibilityIdentifier("serverLink")
                .onSubmit(submit)
            if !link.isEmpty {
                Button {
                    changeLink("")
                    editingLink = true
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(ConnectionPalette.muted)
                .accessibilityLabel("Clear sign-in URL")
                Button(action: submit) {
                    Group {
                        if connecting {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.right")
                        }
                    }
                    .frame(width: 44, height: 44).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(connecting ? "Verifying workspace" : (workspaceEntry == .add ? "Add workspace" : "Sign in"))
                .disabled(connecting || trimmedLink.isEmpty)
            } else {
                PasteButton(payloadType: String.self) { values in
                    if let value = values.first { changeLink(value) }
                }
                .labelStyle(.titleOnly)
                .buttonStyle(.borderless)
                .controlSize(.regular)
                .fixedSize()
                .frame(minHeight: 44)
            }
        }
        .padding(.leading, 12)
        .padding(.trailing, 6)
        .frame(minHeight: 44)
        .background(.white.opacity(0.72), in: RoundedRectangle(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(editingLink ? ConnectionPalette.accent : ConnectionPalette.rule)
                .allowsHitTesting(false)
        }
    }

    private var startup: some View {
        launchCard(loading: true) {
            HStack(spacing: 9) {
                ProgressView()
                    .controlSize(.small)
                    .environment(\.colorScheme, .light)
                    .accessibilityHidden(true)
                Text(startupText).font(.callout)
            }
            .foregroundStyle(Color(red: 0.337, green: 0.404, blue: 0.369))
            .accessibilityElement(children: .combine)

        }
        .task {
            startupIsSlow = false
            do { try await Task.sleep(for: .seconds(4)) } catch { return }
            startupIsSlow = true
        }
    }

    private var startupText: String {
        if startupIsSlow { return "Still trying to open the workspace…" }
        if showingColdStart { return startupStage }
        return "Opening workspace…"
    }

    private func launchCard(
        loading: Bool = false, compactBrand: Bool = false, @ViewBuilder actions: () -> some View
    ) -> some View {
        ScrollView {
            VStack(spacing: 14) {
                brandHeader(compact: compactBrand || editingLink)
                actions()
                if !editingLink { ConnectionFooter() }
            }
            .frame(maxWidth: 420)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        #if os(iOS)
        .refreshable {
            guard !loading, let endpoint else { return }
            await refreshConnectionStatus(for: endpoint)
        }
        #endif
        .defaultScrollAnchor(editingLink ? .top : .center, for: .alignment)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
        .background(ConnectionPalette.appSurface.ignoresSafeArea())
        .overlay(alignment: .topTrailing) {
            if !loading || startupIsSlow {
                helpButton.padding(.trailing, 12)
            }
        }
    }

    private func brandHeader(compact: Bool = false) -> some View {
        let layout = dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
        return layout {
            Image("HeinzelCrew")
                .resizable().scaledToFit()
                .frame(width: compact ? 84 : 96)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text("Heinzelmännchen")
                    .font(.system(.title2, design: .serif, weight: .semibold))
                    .accessibilityAddTraits(.isHeader)
                Text(heinzelTagline)
                    .font(.footnote).multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .foregroundStyle(ConnectionPalette.muted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var helpButton: some View {
        connectionIconButton("questionmark.circle", label: "Connection Help") {
            editingLink = false
            showConnectionHelp = true
        }
    }

    private var setup: some View {
        WorkspaceInformationSurface(version: heinzelAppBuild?.version, background: ConnectionPalette.appSurface) { information in
            startDirectory(information)
        } details: { workspaceInformation }
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
        .background(ConnectionPalette.appSurface.ignoresSafeArea())
    }

    private func startDirectory(_ information: WorkspaceInformationButton) -> some View {
        WorkspaceStartLayout {
            brandHeader(compact: true)
        } heading: {
            workspaceDirectoryHeading
        } rows: {
            VStack(alignment: .leading, spacing: 8) {
                workspaceDirectoryRows
                if choosingWorkspace, remoteRecovery == nil, let current = confirmedOrigin, current == endpoint?.origin {
                    Button("Back to " + workspaceName(current), systemImage: "chevron.backward") {
                        endConnectionAttempt(supersedeShortcut: true)
                        choosingWorkspace = false
                        error = nil
                    }
                    .buttonStyle(.borderless)
                }
            }
            .foregroundStyle(ConnectionPalette.ink)
        } footer: {
            directoryFooter(information)
        }
        .refreshable {
            refreshSyncedWorkspaces()
            directoryRevision += 1
        }
    }

    @ViewBuilder
    private var workspaceDirectory: some View {
        workspaceDirectoryHeading
        workspaceDirectoryRows
    }

    private var workspaceDirectoryHeading: some View {
        HStack {
            Text("Workspaces").font(.title2.bold()).accessibilityAddTraits(.isHeader)
            Spacer()
            connectionIconButton("plus", label: "Add Workspace") { showWorkspaceEntry(.add) }
            #if os(iOS)
            if showConnection {
                connectionIconButton("xmark", label: "Close workspace sheet", action: dismissConnectionPicker)
            }
            #endif
        }
    }

    @ViewBuilder
    private var workspaceDirectoryRows: some View {
        let versions = recordedVersions
        let rows = orderedWorkspaces
        let addresses = RecentWorkspace.disambiguatingAddresses(for: rows, probes: directoryResults)
        VStack(spacing: 0) {
            #if os(macOS)
            let localVersion = if let origin = endpoint?.origin, origin.scheme == "http" {
                versions[origin.absoluteString]
            } else {
                WorkspaceVersionObservation.lastLocal(in: versions)
            }
            let localOrigin = endpoint?.origin.scheme == "http" ? endpoint?.origin : nil
            let localSession = WorkspaceDirectoryRow.Session(origin: localOrigin, retained: endpoint?.origin, confirmed: confirmedOrigin)
            WorkspaceDirectoryRow(name: "This Mac", detail: service.recovery?.detail ??
                (localBackend == nil ? "Open local workspace" : "Running · Local backend"),
                version: localBackend?.version ?? localVersion?.version,
                symbol: "desktopcomputer", session: localSession, localBackend: true, userAdded: false, busy: connecting,
                recoveryControl: localRecoveryControl, open: {
                    if localSession == .connected && service.recovery == nil {
                        endConnectionAttempt(supersedeShortcut: true)
                        choosingWorkspace = false
                        showConnection = false
                    } else {
                        endConnectionAttempt(supersedeShortcut: true)
                        Task { await startLocal() }
                    }
                }, perform: { _ in disconnect() })
            #endif
            ForEach(rows) { item in
                if let target = Endpoint(item.address) {
                    let url = target.origin
                    let recovery = url == endpoint?.origin ? remoteRecovery : nil
                    let session = WorkspaceDirectoryRow.Session(origin: url, retained: endpoint?.origin, confirmed: confirmedOrigin)
                    WorkspaceDirectoryRow(name: item.name, detail: recovery?.label(network: routeAvailable(to: url)) ?? directoryDetail(item, origin: url), version: versions[item.address]?.version,
                        symbol: item.source == .iCloud ? "icloud" : "arrow.up.right", session: session,
                        address: addresses[item.address],
                        available: recovery != nil || WorkspaceHostProbe.canAttemptConnection(networkAvailable: directoryHasNetwork, probe: directoryResults[url]),
                        userAdded: item.isUserAdded, busy: connecting,
                        recoveryControl: recovery.flatMap { state in
                            state.issue.canReconnectAutomatically ? (state.paused, { toggleRecovery() }) : nil
                        }, open: {
                            if let recovery {
                                if recovery.issue == .signIn { showWorkspaceEntry(.signIn(url)) }
                                else if recovery.paused { toggleRecovery() }
                                else if recovery.issue.canReconnectAutomatically { monitorGeneration &+= 1 }
                            } else if session == .connected {
                                endConnectionAttempt(supersedeShortcut: true)
                                choosingWorkspace = false
                                showConnection = false
                            } else if routeAvailable(to: url) == true && directoryResults[url]?.issue == nil && needsSignIn.contains(url) && !hasFreshCloudSignIn(item) {
                                showWorkspaceEntry(.signIn(url))
                            } else {
                                Task { await connectRecent(item.address, source: item.source) }
                            }
                        }, perform: { action in
                            switch action {
                            case .disconnect: disconnect()
                            case .remove: removalCandidate = item
                            }
                        })
                        #if os(iOS)
                        .contextMenu {
                            if session == .connected {
                                if let link = target.shareURL {
                                    ShareLink(item: link) { Label("Share Workspace Link", systemImage: "square.and.arrow.up") }
                                }
                                Button("Connection Help", systemImage: "questionmark.circle") {
                                    helpAfterConnection = true
                                    showConnection = false
                                }
                            }
                        }
                        #endif
                }
            }
            #if os(iOS)
            if orderedWorkspaces.isEmpty && !cloudDirectoryUnavailable {
                Text(cloudDirectory.access == .checking ? "Looking for workspaces…" : "No known workspaces")
                    .foregroundStyle(ConnectionPalette.muted)
                    .frame(maxWidth: .infinity, alignment: .leading).padding()
            }
            #endif
        }
        .background(.white, in: RoundedRectangle(cornerRadius: 14))
        .alert("Remove workspace?", isPresented: Binding(get: { removalCandidate != nil }, set: { showing in
            if !showing { removalCandidate = nil }
        }), presenting: removalCandidate) { item in
            Button("Cancel", role: .cancel) {}
            Button("Remove", role: .destructive) { forgetWorkspace(item) }
        } message: { item in
            Text("Remove " + item.name + " from this device?")
        }
        if cloudDirectoryUnavailable {
            Label("iCloud sign-ins are temporarily unavailable.", systemImage: "icloud.slash")
                .font(.footnote).foregroundStyle(ConnectionPalette.muted)
        }
        if let error, workspaceEntry == nil {
            Label(error, systemImage: "exclamationmark.circle").font(.callout)
                .foregroundStyle(ConnectionPalette.warning)
        }
        if connecting {
            HStack {
                ProgressView().controlSize(.small)
                Text("Verifying workspace…").font(.callout)
                Spacer()
                connectionIconButton("xmark", label: "Cancel connection") {
                    endConnectionAttempt(supersedeShortcut: true)
                }
            }
        }
    }

    private var recordedVersions: [String: WorkspaceVersionObservation] {
        (try? JSONDecoder().decode([String: WorkspaceVersionObservation].self, from: workspaceBackendVersions)) ?? [:]
    }

    private func directoryFooter(_ information: WorkspaceInformationButton) -> some View {
        VStack(spacing: 0) {
            deviceChecks
            information.disabled(connecting).foregroundStyle(ConnectionPalette.muted)
        }
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity)
        .background(ConnectionPalette.appSurface)
    }

    private var deviceChecks: some View {
        ConnectionDeviceSummary(revision: directoryRevision, networkAvailable: directoryHasNetwork,
            cloud: ConnectionHelp.cloudStatus(cloudDirectory.access), interacting: $deviceCheckInteracting,
            report: { observedDeviceChecks = $0 }, reportAddress: { internetClientAddress = $0 },
            refresh: { refreshSyncedWorkspaces(); directoryRevision &+= 1 })
    }

    private var orderedWorkspaces: [RecentWorkspace] {
        recentWorkspaces.sorted { lhs, rhs in
            let a = URL(string: lhs.address).map { directoryResults[$0]?.reachability == .online } == true
            let b = URL(string: rhs.address).map { directoryResults[$0]?.reachability == .online } == true
            return a && !b
        }
    }

    private func hasFreshCloudSignIn(_ item: RecentWorkspace) -> Bool {
        guard let origin = URL(string: item.address), !cloudDirectoryUnavailable, let cloud = availableCloudWorkspaces.first(where: { $0.address == item.address }) else { return false }
        return !needsSignIn.contains(origin) || attemptedCloudSignIns[item.address] != cloud.lastUsed
    }

    private func directoryDetail(_ item: RecentWorkspace, origin: URL) -> String {
        let probe = directoryResults[origin]
        let reachability = WorkspaceHostProbe.remoteReachability(networkAvailable: directoryHasNetwork, probe: probe)
        let status = reachability.rawValue
        let source: String? = switch item.source {
        case .iCloud: "iCloud"
        case .pasted: "Added manually"
        case nil: nil
        }
        let detail: String?
        if routeAvailable(to: origin) == true, reachability != .offline, let issue = probe?.issue {
            detail = issue.title
        } else if needsSignIn.contains(origin) && !hasFreshCloudSignIn(item) {
            detail = "Sign in"
        } else if !cloudDirectoryUnavailable && hasFreshCloudSignIn(item) {
            detail = "iCloud"
        } else if item.isUserAdded && confirmedOrigin == origin {
            detail = "Signed in"
        } else {
            detail = source
        }
        return [status, detail].compactMap { $0 }.joined(separator: " · ")
    }

    private func dismissConnectionPicker() {
        if connectionAttempt != nil { endConnectionAttempt(supersedeShortcut: true) }
        showConnection = false
    }

    private func showWorkspaceEntry(_ entry: WorkspaceEntry) {
        #if os(macOS)
        service.pauseRecovery()
        #endif
        endConnectionAttempt(supersedeShortcut: true)
        link = ""
        error = nil
        workspaceEntry = entry
        returnAfterRemoteRecovery = false
        monitorGeneration &+= 1
    }

    private func dismissWorkspaceEntry() {
        if signInDestination != nil, remoteRecovery == nil, confirmedOrigin != nil, confirmedOrigin == endpoint?.origin {
            choosingWorkspace = false
        }
        endConnectionAttempt(supersedeShortcut: true)
        workspaceEntry = nil
        editingLink = false
        link = ""
        error = nil
    }

    private var workspaceInformation: some View {
        let origin = confirmedOrigin.flatMap { $0 == endpoint?.origin && remoteRecovery == nil ? $0 : nil }
        let target = origin.flatMap { origin -> WorkspaceInformationRequest? in
            guard scenePhase == .active, !loadingWorkspace, visibleProblem == nil, connectionIssue == nil else { return nil }
            if origin.scheme != "http", routeAvailable(to: origin) != true || directoryResults[origin]?.reachability == .offline { return nil }
            let route = directoryResults[origin]
            return WorkspaceInformationRequest(origin: origin, page: loadRevision, networkRevision: directoryRevision,
                                               remoteAddress: route?.address, clientAddress: route?.clientAddress)
        }
        let source = origin.flatMap { origin -> String? in
            if origin.scheme == "http" { return "This Mac" }
            switch recentWorkspaces.first(where: { $0.address == origin.absoluteString })?.source {
            case .pasted: return "Added manually"
            case .iCloud: return "iCloud"
            case nil: return nil
            }
        }
        return WorkspaceVersionDetails(app: (try? JSONDecoder().decode(AppBuildObservation.self, from: appBuildObservation))
            .flatMap { $0.build == heinzelAppBuild ? $0 : nil },
            checks: informationDeviceChecks, internetClientAddress: internetClientAddress, origin: origin, name: origin.map(workspaceName), source: source,
            target: target, document: { target.flatMap { pageSessions.view(for: $0.page) } })
    }

    private var linkEntrySheet: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                connectionSheetHeader(signInDestination.map { "Sign in to " + workspaceName($0) } ?? "Add Workspace") {
                    connectionIconButton("xmark", label: "Cancel") { dismissWorkspaceEntry() }
                        #if os(macOS)
                        .keyboardShortcut(.cancelAction)
                        #endif
                }
                linkField("Sign-in URL", submit: connectDraft)
                if let error {
                    Label(error, systemImage: "exclamationmark.circle").font(.footnote)
                        .foregroundStyle(ConnectionPalette.warning)
                }
            }
            .frame(maxWidth: 440, alignment: .leading).padding(20)
            .frame(maxWidth: .infinity)
        }
        .background(ConnectionPalette.appSurface)
        .foregroundStyle(ConnectionPalette.ink).environment(\.colorScheme, .light)
        #if os(iOS)
        .onAppear { editingLink = true }
        .onChange(of: error) { _, message in
            if let message, !editingLink {
                editingLink = true
                AccessibilityNotification.Announcement(message).post()
            }
        }
        .presentationDetents([.height(dynamicTypeSize.isAccessibilitySize ? 300 : (error == nil ? 160 : 215)), .large])
        .presentationDragIndicator(.visible)
        #else
        .frame(minWidth: 380, idealWidth: 420, minHeight: 160, idealHeight: 200)
        #endif
    }

    private func connectDraft() {
        guard !connecting, !trimmedLink.isEmpty else { return }
        if let message = Endpoint.signInInputIssue(link, expectedOrigin: signInDestination) {
            error = message
            AccessibilityNotification.Announcement(message).post()
            return
        }
        guard let target = draftWorkspace, target.token != nil else { return }
        endConnectionAttempt(supersedeShortcut: true)
        let value = link
        let storedSource = recentWorkspaces.first { $0.address == target.origin.absoluteString }?.source
        editingLink = false
        Task { await connect(value, source: storedSource ?? .pasted) }
    }

    private func refreshSyncedWorkspaces() {
        cloudDirectory.update(WorkspaceKeychain.workspaces())
    }

    private func resumeCloudSignInIfReady() {
        guard workspaceEntry == nil, !connecting, !editingLink, trimmedLink.isEmpty, !choosingWorkspace,
              !showConnectionHelp, !showConnection, sharedWorkspace == nil, loadFailure == .signIn,
              let address = endpoint?.origin.absoluteString,
              let cloud = availableCloudWorkspaces.first(where: { $0.address == address }),
              attemptedCloudSignIns[address] != cloud.lastUsed else { return }
        // The chosen workspace stays the same; only its credential arrived after the first load.
        Task {
            guard workspaceEntry == nil, !connecting, !editingLink, trimmedLink.isEmpty, !choosingWorkspace,
                  loadFailure == .signIn, endpoint?.origin.absoluteString == address else { return }
            await connectCloudWorkspace(cloud, reportMissing: false)
        }
    }

    private func connectCloudWorkspace(_ workspace: RecentWorkspace, reportMissing: Bool = true) async {
        if reportMissing { endConnectionAttempt(supersedeShortcut: true) }
        guard let credential = WorkspaceKeychain.signIn(address: workspace.address,
                                                       newerThan: forgottenWorkspaceDates[workspace.address]) else {
            if reportMissing { error = "The iCloud sign-in is temporarily unavailable. Try again or paste a sign-in link." }
            return
        }
        await connectUsingCloud(credential)
    }

    private func connectRecent(_ address: String, checkHost: Bool = true, source: RecentWorkspace.Source? = nil,
                               preservingShortcut: UUID? = nil) async {
        if connecting, connectingOrigin?.absoluteString == address { return }
        endConnectionAttempt(supersedeShortcut: preservingShortcut != pendingShortcut?.id)
        workspaceEntry = nil
        if let credential = WorkspaceKeychain.signIn(address: address, newerThan: forgottenWorkspaceDates[address]) {
            await connectUsingCloud(credential, checkHost: checkHost, fallBackToWorkspace: true)
        } else {
            let previous = recentWorkspaces.first { $0.address == address }?.source
            await connect(address, checkHost: checkHost, source: source ?? previous)
        }
    }

    private func connectUsingCloud(_ credential: (link: String, workspace: RecentWorkspace),
                                   checkHost: Bool = true, fallBackToWorkspace: Bool = false) async {
        guard !connecting else { return }
        let item = credential.workspace
        attemptedCloudSignIns[item.address] = item.lastUsed
        cloudDirectory.observedSignIn(item)
        await connect(credential.link, checkHost: checkHost, fallBackToWorkspace: fallBackToWorkspace, source: .iCloud)
    }

    private func connect(_ input: String, remember: Bool = true, checkHost: Bool = true,
                         fallBackToWorkspace: Bool = false, source: RecentWorkspace.Source? = nil,
                         initialStartup: Bool = false) async {
        guard !connecting else { return }
        #if os(macOS)
        service.pauseRecovery()
        let menuRevision = initialStartup ? 0 : connectionMenuIntent.revision
        #else
        let menuRevision = 0
        #endif
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var target = parseWorkspaceLink(value) else {
            error = "Enter the full workspace link from the host."
            return
        }
        // A shared address can refer to the active workspace, including a session
        // opened with a sign-in fragment. Keep its WebKit view and in-memory state.
        if (target.token == nil || target == endpoint), confirmedOrigin == target.origin, loadFailure != .signIn {
            workspaceEntry = nil
            showConnection = false
            choosingWorkspace = false
            if loadFailure != nil { retryWorkspace() }
            return
        }
        let previousEndpoint = endpoint
        let previousRevision = loadRevision
        let attempt = UUID()
        if remoteRecovery != nil { returnAfterRemoteRecovery = false }
        connectingOrigin = target.origin
        monitorGeneration &+= 1
        connectionAttempt = attempt
        connecting = true
        error = nil
        defer {
            if connectionAttempt == attempt && pendingSwitch == nil { endConnectionAttempt() }
        }
        if checkHost {
            let probe = await Endpoint.probeHost(at: target.origin)
            guard connectionAttempt == attempt, !Task.isCancelled else { return }
            directoryResults[target.origin] = probe
            if let title = probe.issue?.title {
                error = title
                if workspaceEntry == nil && endpoint != nil && loadFailure != .signIn && !choosingWorkspace { showConnection = true }
                return
            }
        }
        do {
            guard connectionAttempt == attempt, !Task.isCancelled else { return }
            var cloudSignInRejected = false
            do { try await target.verify() }
            catch ConnectionError.authentication where fallBackToWorkspace {
                guard let addressOnly = Endpoint(target.origin.absoluteString) else { throw ConnectionError.authentication }
                target = addressOnly
                cloudSignInRejected = source == .iCloud
            }
            guard connectionAttempt == attempt, !Task.isCancelled, endpoint == previousEndpoint,
                  loadRevision == previousRevision else { return }
            let staged = !initialStartup && (endpoint != target || confirmedOrigin != target.origin)
            switchSequence = max(loadRevision, switchSequence) + 1
            let selection = PendingSwitch(page: WorkspacePage(endpoint: target, id: switchSequence),
                                          previousEndpoint: previousEndpoint, previousRevision: previousRevision,
                                          remember: remember, source: source, cloudSignInRejected: cloudSignInRejected,
                                          menuRevision: menuRevision)
            if staged {
                _ = await stageWorkspace(selection)
                return
            }
            installWorkspace(selection, staged: false)
        } catch ConnectionError.authentication {
            guard connectionAttempt == attempt, !Task.isCancelled else { return }
            needsSignIn.insert(target.origin)
            if workspaceEntry == nil { workspaceEntry = .signIn(target.origin) }
            error = "This sign-in link expired or was rejected. Ask the host for a new one."
        } catch {
            guard connectionAttempt == attempt, !Task.isCancelled else { return }
            self.error = (error as? ConnectionError)?.errorDescription ?? "The server could not be reached."
            if workspaceEntry == nil && endpoint != nil && loadFailure != .signIn && !choosingWorkspace { showConnection = true }
        }
    }

    private func installWorkspace(_ selection: PendingSwitch, staged: Bool) {
        let target = selection.page.endpoint
        monitorGeneration &+= 1
        remoteRecovery = nil
        #if os(macOS)
        if selection.startsLocal {
            if let snapshot = ActivityWidgetData.read(), snapshot.workspaceScope != ActivityWidgetData.scope(for: target.origin) {
                clearWidgetActivity()
                chatRequest = nil
            }
        }
        returnAfterLocalRecovery = false
        service.selectWorkspace(target)
        #endif
        if selection.remember {
            #if os(macOS)
            clearWidgetActivity()
            chatRequest = nil
            #else
            if serverOrigin != target.origin.absoluteString {
                clearWidgetActivity()
                chatRequest = nil
            }
            #endif
            serverOrigin = target.origin.absoluteString
            let provenance = selection.source ?? recentWorkspaces.first { $0.address == target.origin.absoluteString }?.source ?? .pasted
            let cloudName = provenance == .iCloud
                ? availableCloudWorkspaces.first { $0.address == target.origin.absoluteString }?.hostName
                : nil
            rememberWorkspace(target, hostName: cloudName, source: provenance)
            if provenance == .pasted, target.token != nil {
                var forgotten = (try? JSONDecoder().decode([String: Date].self, from: forgottenWorkspaces)) ?? [:]
                forgotten.removeValue(forKey: target.origin.absoluteString)
                if let encoded = try? JSONEncoder().encode(forgotten) { forgottenWorkspaces = encoded }
            }
        }
        link = ""
        if staged || endpoint?.origin != target.origin { connectionIssue = nil }
        if staged || endpoint != target {
            loadFailure = nil
            loadingWorkspace = !staged
        }
        #if os(iOS)
        if endpoint != target { invalidateConnectionScope() }
        #endif
        workspaceEntry = nil
        needsSignIn.remove(target.origin)
        confirmedOrigin = staged ? target.origin : (endpoint == target ? confirmedOrigin : nil)
        showConnection = false
        if staged { loadRevision = selection.page.id }
        endpoint = target
        #if os(macOS)
        choosingWorkspace = connectionMenuIntent.keepSetup(afterStartedAt: selection.menuRevision)
        #else
        choosingWorkspace = false
        #endif
        if selection.cloudSignInRejected && !staged { error = "The workspace didn’t accept the saved iCloud sign-in." }
    }

    private func stageWorkspace(_ selection: PendingSwitch) async -> Bool {
        guard !Task.isCancelled else { return false }
        let id = selection.page.id
        return await withTaskCancellationHandler {
            await withCheckedContinuation { completion in
                switchCompletion = completion
                pendingSwitch = selection
            }
        } onCancel: {
            Task { @MainActor in
                if pendingSwitch?.page.id == id { endConnectionAttempt() }
            }
        }
    }

    private func endConnectionAttempt(supersedeShortcut: Bool = false, succeeded: Bool = false) {
        if supersedeShortcut { pendingShortcut = nil }
        guard connectionAttempt != nil else { return }
        pendingSwitch = nil
        switchCompletion?.resume(returning: succeeded)
        switchCompletion = nil
        connectionAttempt = nil
        connectingOrigin = nil
        connecting = false
    }

    private func finishPendingSwitch(_ revision: Int, issue: WorkspaceProblem?) {
        guard let candidate = pendingSwitch, candidate.page.id == revision else { return }
        guard candidate.previousEndpoint == endpoint, candidate.previousRevision == loadRevision else {
            endConnectionAttempt()
            return
        }
        if let issue {
            endConnectionAttempt()
            if !showConnection && !choosingWorkspace { chooseAnotherWorkspace() }
            let name = workspaceName(candidate.page.endpoint.origin)
            if issue == .signIn && candidate.startsLocal {
                error = "Local sign-in expired. Select This Mac to reconnect."
            } else if issue == .signIn {
                needsSignIn.insert(candidate.page.endpoint.origin)
                if workspaceEntry != nil {
                    error = "This sign-in link expired or was rejected. Ask the host for a new one."
                } else {
                    link = ""
                    workspaceEntry = .signIn(candidate.page.endpoint.origin)
                    error = "\(name) needs sign-in. Paste a fresh sign-in link from that host."
                }
            } else {
                error = "\(name): \(issue.detail)"
            }
            return
        }
        installWorkspace(candidate, staged: true)
        endConnectionAttempt(succeeded: true)
    }

    private func parseWorkspaceLink(_ input: String) -> Endpoint? {
        Endpoint(input) ?? URL(string: input.trimmingCharacters(in: .whitespacesAndNewlines)).flatMap(Endpoint.sharedWorkspace)
    }

    private func chooseAnotherWorkspace() {
        endConnectionAttempt(supersedeShortcut: true)
        workspaceEntry = nil
        showConnection = false
        choosingWorkspace = true
        link = ""
        error = nil
    }

    private func workspaceIdentity(_ origin: URL) -> some View {
        HStack(spacing: 6) {
            Text(workspaceName(origin))
            if availableCloudWorkspaces.contains(where: { $0.address == origin.absoluteString }) {
                Image(systemName: "icloud").accessibilityLabel("Sign-in synced with iCloud")
            }
        }
    }

    private func workspaceName(_ origin: URL) -> String {
        if origin.scheme == "http" { return "This Mac" }
        let address = origin.absoluteString
        return recentWorkspaces.first { $0.address == address }?.name ?? RecentWorkspace.displayName(for: address)
    }

    private func disconnect() {
        monitorGeneration &+= 1
        remoteRecovery = nil
        confirmedOrigin = nil
        endConnectionAttempt(supersedeShortcut: true)
        workspaceEntry = nil
        #if os(macOS)
        service.selectWorkspace(nil)
        #endif
        showConnection = false
        choosingWorkspace = false
        #if os(iOS)
        invalidateConnectionScope()
        #endif
        endpoint = nil
        serverOrigin = ""
        clearWidgetActivity()
        chatRequest = nil
        link = ""
        loadFailure = nil
        connectionIssue = nil
        error = nil
    }

    private func clearWidgetActivity() {
        _ = WidgetCredentialStore.clearShared()
        ActivityWidgetData.reload()
    }

    private func retryWorkspace() {
        endConnectionAttempt(supersedeShortcut: true)
        loadFailure = nil
        loadingWorkspace = true
        loadRevision += 1
    }

    #if os(iOS)
    private func invalidateConnectionScope() {
        connectionScopeRevision += 1
        connectionProbe?.task.cancel()
        connectionProbe = nil
        signInProbeScope = nil
    }

    private func refreshConnectionStatus(for target: Endpoint) async {
        guard endpoint == target, !refreshingConnection else { return }
        versionRefreshRevision &+= 1
        refreshingConnection = true
        defer {
            refreshingConnection = false
            if connectionProbe?.manual == true { connectionProbe = nil }
        }
        guard let result = await sharedConnectionProbe(target.origin, manual: true),
              endpoint == target, monitoredOrigin == target.origin,
              connectionProbe?.revision == result.revision else { return }
        connectionIssue = result.issue
        if result.issue == nil, loadFailure?.canReconnectAutomatically == true { retryWorkspace() }
        AccessibilityNotification.Announcement(result.issue?.title ?? "Workspace reachable").post()
        finishConnectionProbe(result.revision, manual: true)
    }

    private func sharedConnectionProbe(_ origin: URL, manual: Bool) async -> (revision: Int, issue: WorkspaceProblem?)? {
        let flight: ConnectionProbe
        if let existing = connectionProbe, existing.origin == origin, !manual || existing.manual {
            flight = existing
        } else {
            connectionProbe?.task.cancel()
            connectionProbeRevision += 1
            let task = Task { await Endpoint.connectionIssue(at: origin) }
            flight = ConnectionProbe(revision: connectionProbeRevision, origin: origin, manual: manual, task: task)
            connectionProbe = flight
        }
        let issue = await flight.task.value
        guard !Task.isCancelled, connectionProbe?.revision == flight.revision else { return nil }
        return (flight.revision, issue)
    }

    private func finishConnectionProbe(_ revision: Int, manual: Bool = false) {
        guard connectionProbe?.revision == revision, manual || connectionProbe?.manual == false else { return }
        connectionProbe = nil
    }
    #endif

    private func watchRemoteSession(_ watch: RemoteSessionWatch) async {
        guard let view = pageSessions.view(for: watch.page), let target = endpoint else { return }
        await WorkspaceRemoteMonitor.run(recovery: remoteRecovery, network: watch.network, isCurrent: {
            remoteWatch == watch && view === pageSessions.view(for: watch.page)
        }, probe: {
            await WorkspaceWebSession.issue(in: view, for: target, requireRenderedShell: remoteRecovery != nil)
        }, report: { event in
            switch event {
            case .healthy:
                connectionIssue = nil
                guard remoteRecovery != nil else { return }
                remoteRecovery = nil
                confirmedOrigin = watch.origin
                WorkspaceWebSession.reportRestored(in: view, for: target)
                if returnAfterRemoteRecovery { choosingWorkspace = false }
                returnAfterRemoteRecovery = false
                loadFailure = nil
                loadingWorkspace = false
            case .lost(let lost):
                if directoryHasNetwork != true && [.offline, .slow, .unavailable].contains(lost.issue) {
                    directoryResults.removeValue(forKey: watch.origin)
                }
                if remoteRecovery == nil {
                    returnAfterRemoteRecovery = !choosingWorkspace && !showConnection && workspaceEntry == nil
                    showConnection = false
                    choosingWorkspace = true
                }
                remoteRecovery = lost
                connectionIssue = lost.issue
                if lost.issue.revokesAccess {
                    confirmedOrigin = nil
                    if lost.issue == .signIn { needsSignIn.insert(watch.origin) }
                }
            }
        })
    }

    private func toggleRecovery() {
        guard remoteRecovery?.issue.canReconnectAutomatically == true else { return }
        remoteRecovery?.togglePause()
        monitorGeneration &+= 1
    }

    private func watchConnection(to origin: URL) async {
        let session = Endpoint.probeSession()
        defer { session.invalidateAndCancel() }
        var retryDelay = 2
        while !Task.isCancelled {
            #if os(iOS)
            guard let result = await sharedConnectionProbe(origin, manual: false) else {
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
                continue
            }
            let issue = result.issue
            #else
            let issue = await Endpoint.connectionIssue(at: origin, using: session)
            #endif
            guard !Task.isCancelled, monitoredOrigin == origin else { return }
            #if os(iOS)
            if refreshingConnection || connectionProbe?.revision != result.revision {
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
                continue
            }
            finishConnectionProbe(result.revision)
            #endif
            let recovered = connectionIssue != nil && issue == nil
            connectionIssue = issue
            if recovered && (loadFailure?.canReconnectAutomatically == true || loadingWorkspace) { retryWorkspace() }
            let delay = issue == nil ? 12 : retryDelay
            retryDelay = issue == nil ? 2 : min(retryDelay * 2, 12)
            do { try await Task.sleep(for: .seconds(delay)) } catch { return }
        }
    }

    #if os(macOS)
    private func removePublishedSignIn() {
        guard !publishedWorkspaceOrigin.isEmpty else { return }
        let address = publishedWorkspaceOrigin
        let issued = WorkspaceKeychain.published(address: address)?.token
        if WorkspaceKeychain.remove(address: address) {
            // Switching sync off also revokes the iCloud device token on the host.
            if let issued { Task { await Self.revokeIssued(token: issued, address: address) } }
            publishedWorkspaceOrigin = ""
            syncStatus = nil
        } else {
            syncStatus = "Couldn’t remove the synced sign-in."
        }
    }

    private static func revokeIssued(token: String, address: String) async {
        guard token.hasPrefix("hzd_"), let remote = Endpoint(address + "/#token=" + token) else { return }
        await remote.revokeDeviceToken()
    }

    /// The token for the iCloud item: a named device token, minted once and renewed after 14 days or a refusal.
    private func cloudToken(for address: String, endpoint: Endpoint) async throws -> (token: String, minted: Bool) {
        let existing = WorkspaceKeychain.published(address: address)
        var refused = false
        if let existing, existing.token.hasPrefix("hzd_"), let remote = Endpoint(address + "/#token=" + existing.token) {
            do { _ = try await remote.readLifecycle() } catch ConnectionError.authentication { refused = true } catch {}
        }
        if CloudSignInPlan.decide(existingToken: existing?.token, publishedAt: existing?.publishedAt,
                                  hostRefusedIt: refused) == .keep, let existing {
            return (existing.token, false)
        }
        guard !Task.isCancelled, syncWorkspaceSignIn, service.publishingSession == endpoint else { throw CancellationError() }
        let fresh = try await endpoint.mintDeviceToken(name: CloudSignInPlan.deviceName)
        guard !Task.isCancelled, syncWorkspaceSignIn, service.publishingSession == endpoint else { throw CancellationError() }
        if let old = existing?.token { await Self.revokeIssued(token: old, address: address) }
        return (fresh, true)
    }

    private func publishLocalSignIn() async {
        guard offersCloudSignIn, syncWorkspaceSignIn, let endpoint = service.publishingSession, endpoint.origin.scheme == "http", endpoint.token != nil else { return }
        while !Task.isCancelled, syncWorkspaceSignIn, service.publishingSession == endpoint {
            do {
                let target = try await endpoint.sharedSignInOrigin()
                guard !Task.isCancelled, syncWorkspaceSignIn, service.publishingSession == endpoint else { return }
                if let target {
                    let address = target.origin.absoluteString
                    if publishedWorkspaceOrigin != address { removePublishedSignIn() }
                    let item = RecentWorkspace(address: address, hostName: Host.current().localizedName, lastUsed: .now, source: .iCloud)
                    let issued = try await cloudToken(for: address, endpoint: endpoint)
                    guard !Task.isCancelled, syncWorkspaceSignIn, service.publishingSession == endpoint else { return }
                    if WorkspaceKeychain.publish(workspace: item, token: issued.token) {
                        publishedWorkspaceOrigin = address
                        syncStatus = "Available to your devices through iCloud Keychain."
                        if !cloudPublishNoticeShown {
                            cloudPublishNoticeShown = true
                            await WorkspaceNotifications.shared.announceCloudPublish()
                        }
                    } else {
                        syncStatus = "iCloud Keychain couldn’t save the sign-in."
                    }
                } else {
                    removePublishedSignIn()
                    syncStatus = "Enable private Tailscale sharing first."
                }
            } catch {
                guard !Task.isCancelled else { return }
                syncStatus = "Checking private sharing…"
            }
            do { try await Task.sleep(for: .seconds(30)) } catch { return }
        }
    }

    private var localRecoveryControl: (paused: Bool, toggle: () -> Void)? {
        guard let recovery = service.recovery, recovery.retries || recovery == .paused else { return nil }
        return (recovery == .paused, {
            if service.recovery == .paused { service.resumeRecovery() } else { service.pauseRecovery() }
        })
    }

    private func presentLocalLoss() {
        guard let endpoint, endpoint == service.localSession else { return }
        // Keep the installed workspace page alive. Only the chooser owns interaction during loss.
        returnAfterLocalRecovery = !choosingWorkspace && !showConnection && workspaceEntry == nil && !connecting
        confirmedOrigin = nil
        choosingWorkspace = true
        if workspaceEntry == nil && !deviceCheckInteracting { showConnection = false }
        if connecting || workspaceEntry != nil || deviceCheckInteracting { service.pauseRecovery() }
    }

    private func verifyLocalPage(_ target: Endpoint) async throws {
        let revision = loadRevision
        guard endpoint == target, let view = pageSessions.view(for: revision) else { throw ConnectionError.unverifiedWorkspace }
        let issue = await WorkspaceWebSession.issue(in: view, for: target)
        guard !Task.isCancelled, endpoint == target, revision == loadRevision,
              view === pageSessions.view(for: revision) else { throw CancellationError() }
        switch issue {
        case nil: return
        case .signIn: throw ConnectionError.authentication
        case .denied: throw ConnectionError.accessDenied
        default: throw ConnectionError.unverifiedWorkspace
        }
    }

    private func restoreLocalSession(_ target: Endpoint) {
        guard endpoint == target else { return }
        if let view = pageSessions.view(for: loadRevision) {
            WorkspaceWebSession.reportRestored(in: view, for: target)
        }
        confirmedOrigin = target.origin
        connectionIssue = nil
        loadFailure = nil
        loadingWorkspace = false
        if returnAfterLocalRecovery && !connecting && workspaceEntry == nil {
            choosingWorkspace = false
        }
        returnAfterLocalRecovery = false
    }

    /// Cold launch and explicit selection share the supported local attach path.
    @discardableResult
    private func startLocal(initialStartup: Bool = false) async -> Bool? {
        guard !connecting else { return nil }
        guard service.shouldAttachOnSelection() else { return nil }
        let menuRevision = initialStartup ? 0 : connectionMenuIntent.revision
        let previousEndpoint = endpoint
        let previousRevision = loadRevision
        let attempt = UUID()
        monitorGeneration &+= 1
        connectionAttempt = attempt
        connecting = true
        error = nil
        defer {
            if connectionAttempt == attempt && pendingSwitch == nil { endConnectionAttempt() }
        }
        do {
            let target = try await service.start(monitoring: false)
            guard connectionAttempt == attempt, !Task.isCancelled, endpoint == previousEndpoint,
                  loadRevision == previousRevision else { return nil }
            switchSequence = max(loadRevision, switchSequence) + 1
            let selection = PendingSwitch(page: WorkspacePage(endpoint: target, id: switchSequence),
                                          previousEndpoint: previousEndpoint, previousRevision: previousRevision,
                                          remember: false, source: nil, cloudSignInRejected: false,
                                          menuRevision: menuRevision, startsLocal: true)
            return await stageWorkspace(selection)
        } catch {
            guard connectionAttempt == attempt, !Task.isCancelled else { return nil }
            self.error = (error as? ConnectionError)?.errorDescription ?? ConnectionError.startup.errorDescription
            return false
        }
    }
    #endif
}


private struct NativePromptSheet: View {
    @Bindable var composer: NativePromptComposer
    let openWorkspace: () -> Void
    let openChat: (NativePromptRequest) -> Void
    var inline = false
    var closeInline: (() -> Void)?
    @Environment(\.dismiss) private var dismiss
    @FocusState private var editing: Bool
    @State private var confirmingDiscard = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                connectionSheetHeader(inline ? "Quick Chat" : "Write a Prompt") {
                    connectionIconButton("xmark", label: "Close") { close() }
                }
                if composer.scope == nil {
                    ContentUnavailableView("Open a workspace first", systemImage: "link",
                                           description: Text(composer.message ?? "Connect to your workspace to choose a Heinzel."))
                    Button("Open Workspace", action: openWorkspace).buttonStyle(.borderedProminent)
                } else {
                    recipient
                    if !composer.catalogAvailable {
                        Text("Heinzels are temporarily unavailable. Your draft is still here.")
                            .font(.footnote).foregroundStyle(ConnectionPalette.warning)
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Prompt").font(.callout.weight(.semibold))
                        ZStack(alignment: .topLeading) {
                            if composer.text.isEmpty {
                                Text("What would you like this Heinzel to do?")
                                    .foregroundStyle(ConnectionPalette.muted)
                                    .padding(.horizontal, 5).padding(.vertical, 8)
                                    .allowsHitTesting(false)
                            }
                            TextEditor(text: $composer.text)
                                .scrollContentBackground(.hidden)
                                .frame(minHeight: inline ? 100 : 160, maxHeight: inline ? 160 : 260)
                                .focused($editing)
                                .disabled(composer.pending != nil)
                                .accessibilityLabel("Prompt")
                                .accessibilityIdentifier("nativePromptText")
                        }
                        .padding(8)
                        .background(.white.opacity(0.72), in: RoundedRectangle(cornerRadius: 12))
                        .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(ConnectionPalette.rule) }
                        if composer.text.unicodeScalars.count > 32_000 {
                            Text("Keep the prompt under 32,000 characters.")
                                .font(.footnote).foregroundStyle(ConnectionPalette.warning)
                        }
                    }
                    if let message = composer.message {
                        Label(message, systemImage: composer.accepted ? "checkmark.circle.fill" : "info.circle")
                            .font(.callout)
                            .fixedSize(horizontal: false, vertical: true)
                            .foregroundStyle(composer.accepted ? ConnectionPalette.accent : ConnectionPalette.warning)
                            .accessibilityIdentifier("nativePromptResult")
                    }
                    if composer.accepted {
                        if let request = composer.pending {
                            Button("Open Chat", systemImage: "bubble.left.and.bubble.right") { openChat(request) }
                                .buttonStyle(.borderedProminent).controlSize(.large)
                        }
                        Button("Write Another Prompt") { composer.newPrompt(); editing = true }
                            .buttonStyle(.bordered)
                    } else {
                        Button {
                            editing = false
                            composer.startSend()
                        } label: {
                            HStack(spacing: 8) {
                                if composer.sending { ProgressView().controlSize(.small) }
                                Text(sendPromptTitle)
                                if !composer.sending { Image(systemName: "arrow.up") }
                            }
                            .frame(maxWidth: .infinity, minHeight: 30)
                        }
                        .buttonStyle(.borderedProminent).controlSize(.large)
                        .disabled(!composer.canSend)
                        .accessibilityIdentifier("nativePromptSend")
                        .keyboardShortcut(.return, modifiers: .command)
                        if let request = composer.pending {
                            HStack {
                                Button("Check Chat") { openChat(request) }.buttonStyle(.bordered)
                                Spacer()
                                if !composer.sending {
                                    Button("Discard…", role: .destructive) { confirmingDiscard = true }
                                        .buttonStyle(.borderless)
                                }
                            }
                        }
                        Text(composer.pending == nil ? "Sends to this Heinzel’s chat. If busy, it is queued as a follow-up." :
                             "Recipient and text stay fixed until delivery is confirmed.")
                            .font(.footnote).foregroundStyle(ConnectionPalette.muted)
                    }
                }
            }
            .frame(maxWidth: 460, alignment: .leading)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .frame(idealWidth: inline ? 348 : 520, minHeight: inline ? 300 : 460, idealHeight: inline ? 430 : 630)
        .onAppear { editing = true }
        #if os(macOS)
        .onExitCommand { close() }
        #endif
        .environment(\.colorScheme, .light)
        .foregroundStyle(ConnectionPalette.ink)
        .tint(ConnectionPalette.accent)
        .confirmationDialog("Discard this saved prompt?", isPresented: $confirmingDiscard, titleVisibility: .visible) {
            Button("Discard Saved Prompt", role: .destructive) { composer.discardPending() }
            Button("Keep Prompt", role: .cancel) {
                // Dismissing the confirmation keeps the saved retry copy.
            }
        } message: {
            Text("The host may have received it. This removes the saved retry copy from this device; it does not cancel work. Check the chat first if you’re unsure.")
        }
        .privacySensitive()
    }

    private func close() {
        if let closeInline { closeInline() } else { dismiss() }
    }

    private var sendPromptTitle: String {
        if composer.sending { return "Sending…" }
        return composer.pending == nil ? "Send Prompt" : "Retry Same Prompt"
    }

    @ViewBuilder
    private var recipient: some View {
        if let request = composer.pending {
            LabeledContent("To", value: request.recipientName)
        } else if composer.roster.isEmpty && composer.search.isEmpty && composer.page == 0 {
            VStack(alignment: .leading, spacing: 8) {
                Text("No Heinzels available yet").font(.headline)
                Text("Open the workspace to refresh your list or create a Heinzel.")
                    .font(.callout).foregroundStyle(ConnectionPalette.muted)
                Button("Open Workspace", action: openWorkspace).buttonStyle(.bordered)
            }
        } else {
            TextField("Search Heinzels", text: $composer.search)
                .onChange(of: composer.search) { composer.page = 0 }
            Picker("To", selection: $composer.recipientID) {
                Text("Choose a Heinzel").tag("")
                ForEach(composer.roster) { item in Text("\(item.name) · \(item.id)").tag(item.id) }
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("nativePromptRecipient")
            HStack {
                Button("Previous") { composer.page -= 1 }.disabled(composer.page == 0)
                Button("Next") { composer.page += 1 }.disabled(composer.pageCount < 50)
            }
            if let item = composer.roster.first(where: { $0.id == composer.recipientID }), let image = item.portraitImage {
                image.resizable().scaledToFit().frame(width: 48, height: 48).accessibilityHidden(true)
            }
        }
    }
}

private struct ConnectionDeviceSummary: View {
    let revision: Int
    let networkAvailable: Bool?
    let cloud: ConnectionHelp.Result
    @Binding var interacting: Bool
    let report: ([String: String]) -> Void
    let reportAddress: (String?) -> Void
    let refresh: () -> Void
    @Environment(\.scenePhase) private var phase
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.openURL) private var openURL
    @State private var internet: ConnectionHelp.Result?
    @State private var notifications: ConnectionHelp.Result?
    @State private var playback: ConnectionHelp.Result?
    @State private var permissionTask: Task<Void, Never>?

    private enum DeviceCheck: String, CaseIterable {
        case internet = "Internet", notifications = "Notifications", audio = "System audio", cloud = "iCloud"
    }

    var body: some View {
        ViewThatFits(in: .horizontal) {
            if !typeSize.isAccessibilitySize {
                HStack(spacing: 8) { indicators }.fixedSize(horizontal: true, vertical: false)
            }
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), alignment: .center),
                                     count: typeSize.isAccessibilitySize ? 1 : 2), spacing: 0) {
                indicators
            }
        }
        .frame(maxWidth: 460)
        #if os(iOS)
        .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification)) { _ in
            if phase == .active { playback = Self.playbackCapability() }
        }
        #endif
        .onChange(of: internet?.clientAddress, initial: true) { _, value in reportAddress(value) }
        .onChange(of: informationValues, initial: true) { _, values in report(values) }
        .disabled(interacting)
        .onDisappear {
            if permissionTask != nil {
                permissionTask?.cancel()
                permissionTask = nil
                interacting = false
            }
        }
        .task(id: phase == .active ? revision : nil) {
            guard phase == .active else { return }
            playback = Self.playbackCapability()
            let session = Endpoint.probeSession()
            defer { session.invalidateAndCancel() }
            let permission = await ConnectionHelp.notificationStatus(for: nil)
            guard !Task.isCancelled else { return }
            notifications = permission
            guard networkAvailable == true else {
                internet = networkAvailable == false ? ConnectionHelp.Result(.failed, "Offline. This device has no network connection.") : nil
                return
            }
            let publicSite = await ConnectionHelp.internetStatus(using: session)
            if !Task.isCancelled { internet = publicSite }
        }
    }

    @ViewBuilder private var indicators: some View {
        indicator(.internet)
        indicator(.notifications)
        indicator(.audio)
        indicator(.cloud)
    }

    private var informationValues: [String: String] {
        var values = Dictionary(uniqueKeysWithValues: DeviceCheck.allCases.map { ($0.rawValue, result(for: $0)?.detail ?? "Checking") })
        values["Microphone"] = ConnectionHelp.microphone.detail
        return values
    }

    private func result(for check: DeviceCheck) -> ConnectionHelp.Result? {
        switch check {
        case .internet: internet
        case .notifications: notifications
        case .cloud: cloud
        case .audio: playback
        }
    }

    private static func playbackCapability() -> ConnectionHelp.Result {
        #if os(iOS)
        let available = !AVAudioSession.sharedInstance().currentRoute.outputs.isEmpty
        #else
        let available = SystemAudio.defaultPlaybackOutput != nil
        #endif
        return available ? .init(.passed, "An audio output is available for Soundscapes")
                         : .init(.unavailable, "No audio output is currently available")
    }

    @ViewBuilder private func indicator(_ check: DeviceCheck) -> some View {
        let result = result(for: check)
        if check == .notifications, result?.status == .optional {
            Button {
                guard !interacting else { return }
                interacting = true
                permissionTask = Task {
                    let updated = await ConnectionHelp.requestDeviceNotifications()
                    guard !Task.isCancelled else { return }
                    notifications = updated
                    finishPermission(updated)
                }
            } label: { statusLabel(check, result) }
            .buttonStyle(.plain).accessibilityHint("Requests notification permission")
        } else if check == .notifications, result?.status == .off {
            Button { openURL(ConnectionHelp.notificationSettingsURL) } label: { statusLabel(check, result) }
                .buttonStyle(.plain).accessibilityHint("Opens notification settings")
        } else if (check == .cloud || check == .internet || check == .notifications),
                  result?.status == .failed || result?.status == .unavailable {
            Button(action: refresh) { statusLabel(check, result) }
                .buttonStyle(.plain).accessibilityHint("Checks again")
        } else {
            statusLabel(check, result)
        }
    }

    private func statusLabel(_ check: DeviceCheck, _ result: ConnectionHelp.Result?) -> some View {
        Label {
            Text(check.rawValue).foregroundStyle(ConnectionPalette.muted)
        } icon: {
            switch result?.status {
            case .off, .failed, .unavailable:
                Image(systemName: "xmark.circle.fill").foregroundStyle(ConnectionHelp.Status.failed.color)
            case .optional:
                Image(systemName: "circle").foregroundStyle(ConnectionPalette.muted)
            default:
                Image(systemName: result?.status.symbol ?? "circle.dotted")
                    .foregroundStyle(result?.status.color ?? ConnectionPalette.muted)
            }
        }
        .font(.caption)
        .frame(minWidth: 44, minHeight: 44)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(check.rawValue)
        .accessibilityValue(result?.detail ?? "Checking")
        .help(result?.detail ?? "Checking")
    }

    private func finishPermission(_ result: ConnectionHelp.Result) {
        permissionTask = nil
        interacting = false
        AccessibilityNotification.Announcement(result.detail).post()
    }
}

private struct ConnectionHelp: View {
    let origin: URL?
    let activeWorkspace: Endpoint?
    let needsSignIn: Bool
    let workspaceIsCurrent: (Endpoint) -> Bool
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openURL) private var openURL
    @State private var run = 1
    @State private var audioRun = 0
    @State private var checking = true
    @State private var testingAudio = false
    @State private var askingPermission = false
    @State private var results: [Check: Result] = [:]
    #if os(iOS)
    @State private var showingDeviceFeatures = false
    #endif

    private enum Check: String, CaseIterable {
        case device = "Device Network", internet = "Internet", workspace = "Workspace", microphone = "Microphone"
        case systemAudio = "System Audio", notifications = "Notifications", cloud = "iCloud"
    }

    fileprivate enum Status {
        case pending, checking, passed, action, optional, off, failed, unavailable

        var symbol: String {
            switch self {
            case .pending: "circle.dotted"
            case .checking: "circle"
            case .passed: "checkmark.circle.fill"
            case .action: "exclamationmark.circle.fill"
            case .optional: "info.circle"
            case .off, .unavailable: "minus.circle.fill"
            case .failed: "xmark.circle.fill"
            }
        }

        var label: String {
            switch self {
            case .pending: "Waiting"
            case .checking: "Checking"
            case .passed: "Passed"
            case .action: "Needs attention"
            case .optional: "Optional"
            case .off: "Off"
            case .failed: "Failed"
            case .unavailable: "Unavailable"
            }
        }

        var color: Color {
            switch self {
            case .passed: Color(red: 0.19, green: 0.37, blue: 0.27)
            case .action: Color(red: 0.52, green: 0.32, blue: 0.12)
            case .failed: Color(red: 0.65, green: 0.21, blue: 0.20)
            default: .secondary
            }
        }
    }

    fileprivate struct Result {
        let status: Status
        let detail: String

        let clientAddress: String?

        init(_ status: Status, _ detail: String, clientAddress: String? = nil) {
            self.clientAddress = clientAddress
            self.status = status
            self.detail = detail
        }
    }

    fileprivate static var microphone: Result {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: Result(.passed, "Access allowed")
        case .denied: Result(.off, "Access is off in Settings")
        case .restricted: Result(.unavailable, "Restricted on this device")
        case .notDetermined: Result(.optional, "Allow access to use the microphone visualizer")
        @unknown default: Result(.failed, "Microphone permission could not be read")
        }
    }

    private var canTestSystemAudio: Bool {
        Self.canTestSystemAudio(for: activeWorkspace)
    }

    fileprivate static func canTestSystemAudio(for workspace: Endpoint?) -> Bool {
        #if os(macOS)
        SystemAudio.available && (workspace == nil || workspace?.origin.scheme == "http")
        #elseif canImport(ScreenCaptureKit) && !targetEnvironment(simulator)
        if #available(iOS 27, *) { SystemAudio.available } else { false }
        #else
        false
        #endif
    }

    private var systemName: String {
        #if os(macOS)
        "macOS"
        #else
        UIDevice.current.userInterfaceIdiom == .pad ? "iPadOS" : "iOS"
        #endif
    }

    private var detectedSystem: String {
        #if os(macOS)
        let version = ProcessInfo.processInfo.operatingSystemVersion
        let number = version.patchVersion == 0
            ? "\(version.majorVersion).\(version.minorVersion)"
            : "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
        #else
        let number = UIDevice.current.systemVersion
        #endif
        #if targetEnvironment(simulator)
        return "Simulator running \(systemName) \(number)"
        #else
        return "Running \(systemName) \(number)"
        #endif
    }

    private var systemAudio: Result {
        if canTestSystemAudio {
            #if os(macOS)
            return Result(.optional, "Check access to audio from other apps")
            #else
            return Result(.optional, "Choose what to share in the system picker")
            #endif
        }
        #if os(macOS)
        return Result(.optional, "Open a local Mac workspace to test sharing")
        #elseif targetEnvironment(simulator)
        return Result(.unavailable, "Requires a physical device running \(systemName) 27 or later")
        #else
        if #unavailable(iOS 27) {
            return Result(.unavailable, "Requires \(systemName) 27 or later")
        }
        return Result(.unavailable, "The system sharing picker is unavailable on this device")
        #endif
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                #if os(iOS)
                connectionSummary
                #endif
                diagnosticGroup([.device, .internet, .workspace])
                #if os(iOS)
                DisclosureGroup(isExpanded: $showingDeviceFeatures) {
                    diagnosticGroup([.microphone, .systemAudio, .notifications, .cloud])
                        .padding(.top, 8)
                } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Device Features").font(.callout.weight(.semibold))
                        Text("Optional microphone, audio and notifications")
                            .font(.footnote)
                            .foregroundStyle(ConnectionPalette.muted)
                    }
                }
                #else
                VStack(alignment: .leading, spacing: 8) {
                    Text("Device Features")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(ConnectionPalette.muted)
                        .accessibilityAddTraits(.isHeader)
                    diagnosticGroup([.microphone, .systemAudio, .notifications, .cloud])
                }
                #endif

                DisclosureGroup("Which link should I use?") {
                    Text("Start with the link your workspace host sent you. If Heinzel asks you to sign in, ask the host for a sign-in link. It gives this device access, so keep it private.")
                        .font(.callout)
                        .padding(.top, 8)
                }
                DisclosureGroup("Why can’t I connect?") {
                    Text("For a private workspace, sign in to Tailscale and check that the computer hosting Heinzelmännchen is awake. If the workspace is reachable but sign-in fails, ask the host for a fresh sign-in link.")
                        .font(.callout)
                        .padding(.top, 8)
                }
                DisclosureGroup("Which permissions do I need?") {
                    Text("Microphone and system audio are for the visualizer. Notifications are optional. If you connect to a server on your local network, allow Local Network access when iPhone or iPad asks.")
                        .font(.callout)
                        .padding(.top, 8)
                }
            }
            .frame(maxWidth: 460, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.top, 8)
            .padding(.bottom, 24)
            .frame(maxWidth: .infinity)
        }
        #if os(iOS)
        .refreshable {
            guard !checking && !testingAudio && !askingPermission else { return }
            await runDiagnostics()
        }
        #endif
        .safeAreaInset(edge: .top, spacing: 0) { diagnosticsHeader }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            ConnectionFooter().background(ConnectionPalette.appSurface)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active && !checking && !testingAudio && !askingPermission {
                Task { await refreshPermissions() }
            }
        }
        .task(id: run) {
            await runDiagnostics()
        }
        .task(id: audioRun) {
            if audioRun > 0 { await testSystemAudio() }
        }
        .frame(idealWidth: 500, idealHeight: 610)
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
    }

    #if os(iOS)
    @ViewBuilder
    private var connectionSummary: some View {
        if let workspace = results[.workspace], workspace.status != .checking {
            VStack(alignment: .leading, spacing: 8) {
                Text(connectionSummaryTitle(workspace))
                    .font(.system(.title3, design: .serif, weight: .semibold))
                    .foregroundStyle(ConnectionPalette.ink)
                    .accessibilityAddTraits(.isHeader)
                Text(connectionSummaryDetail(workspace))
                    .font(.callout)
                    .foregroundStyle(ConnectionPalette.muted)
                    .fixedSize(horizontal: false, vertical: true)
                if origin == nil {
                    Button("Enter Workspace Link") { dismiss() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                } else if workspace.status == .passed {
                    Button(activeWorkspace == nil ? "Back to Connect" : "Back to Workspace") { dismiss() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityIdentifier("connectionSummary")
        }
    }

    private func connectionSummaryTitle(_ workspace: Result) -> String {
        if origin == nil { return "Add your workspace" }
        if workspace.status == .passed { return activeWorkspace == nil ? "Ready to connect" : "Your workspace is connected" }
        if needsSignIn && workspace.status == .action { return "Sign in to continue" }
        return "Workspace unavailable"
    }

    private func connectionSummaryDetail(_ workspace: Result) -> String {
        if origin == nil { return "Paste the link your workspace host shared with you to check its connection." }
        if workspace.status == .passed { return "The workspace is reachable. Device features below are optional." }
        if needsSignIn && workspace.status == .action { return "Ask the host for a fresh sign-in link, then return to the sign-in screen." }
        return "Check that the host computer is awake. For a private workspace, make sure Tailscale is connected, then check again."
    }
    #endif

    private var diagnosticsHeader: some View {
        VStack(alignment: .leading, spacing: 4) {
            connectionSheetHeader("Connection Help") {
                connectionIconButton("arrow.clockwise", label: "Check Again") {
                    checking = true
                    results = [:]
                    run += 1
                }
                .disabled(checking || testingAudio || askingPermission)
                .opacity(checking || testingAudio || askingPermission ? 0.35 : 1)
                connectionIconButton("xmark", label: "Close") { dismiss() }
            }
            if checking {
                let finished = results.values.filter { $0.status != .checking }.count
                ProgressView(value: Double(finished), total: Double(Check.allCases.count)) {
                    Text("Checking \(finished)/\(Check.allCases.count)")
                        .font(.footnote)
                        .foregroundStyle(ConnectionPalette.muted)
                }
                .accessibilityIdentifier("diagnosticsProgress")
            }
        }
        .frame(maxWidth: 460, alignment: .leading)
        .padding(.horizontal, 24)
        .padding(.top, 16)
        .padding(.bottom, 8)
        .frame(maxWidth: .infinity)
        .background(ConnectionPalette.appSurface)
    }

    private func diagnosticGroup(_ checks: [Check]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(checks, id: \.self) { check in
                if check != checks.first { Divider().padding(.vertical, 10) }
                diagnostic(check)
            }
        }
        .connectionPanel()
    }

    private func refreshPermissions() async {
        let notifications = await Self.notificationStatus(for: activeWorkspace)
        guard !checking && !testingAudio && !askingPermission else { return }
        results[.microphone] = Self.microphone
        results[.notifications] = notifications
    }

    private func diagnostic(_ check: Check) -> some View {
        let result = results[check] ?? Result(.pending, "")
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(check.rawValue).font(.callout.weight(.semibold))
                    if !result.detail.isEmpty && result.status != .checking {
                        Text(result.detail).font(.footnote).foregroundStyle(ConnectionPalette.muted)
                    }
                    if check == .workspace, let origin {
                        Text(origin.absoluteString)
                            .font(.caption.monospaced())
                            .foregroundStyle(ConnectionPalette.muted)
                    }
                    if check == .systemAudio {
                        Text(detectedSystem).font(.footnote).foregroundStyle(ConnectionPalette.muted)
                    }
                }
                Spacer(minLength: 0)
                if result.status == .checking {
                    ProgressView().controlSize(.small).accessibilityLabel(result.detail)
                } else {
                    Image(systemName: result.status.symbol)
                        .foregroundStyle(result.status.color)
                        .accessibilityLabel(result.status.label)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("diagnostic-\(check.rawValue)")
            if !testingAudio && !askingPermission && result.status != .pending && result.status != .checking {
                permissionAction(check)
            }
        }
    }

    @ViewBuilder
    private func permissionAction(_ check: Check) -> some View {
        switch check {
        case .microphone:
            if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
                Button("Allow Microphone") { Task { await requestMicrophone() } }.buttonStyle(.bordered)
            } else if AVCaptureDevice.authorizationStatus(for: .audio) == .denied {
                permissionSettings
            }
        case .systemAudio:
            if canTestSystemAudio {
                Button("Test System Audio") { audioRun += 1 }.buttonStyle(.bordered)
            }
        case .notifications:
            if results[.notifications]?.status == .optional {
                Button("Enable Notifications") { Task { await requestNotifications() } }.buttonStyle(.bordered)
            } else if results[.notifications]?.status == .off {
                Button("Open Notification Settings") { openURL(Self.notificationSettingsURL) }.buttonStyle(.bordered)
            }
        default:
            EmptyView()
        }
    }

    @ViewBuilder
    private var permissionSettings: some View {
        #if os(iOS)
        if let settings = URL(string: UIApplication.openSettingsURLString) {
            Button("Open App Settings") { openURL(settings) }.buttonStyle(.bordered)
        }
        #else
        Text("Change permissions in System Settings.").font(.footnote).foregroundStyle(.secondary)
        #endif
    }

    private func runDiagnostics() async {
        checking = true
        results = Dictionary(uniqueKeysWithValues: Check.allCases.map { ($0, Result(.checking, "Checking…")) })
        defer { checking = false }
        let session = Endpoint.probeSession()
        defer { session.invalidateAndCancel() }
        results[.microphone] = Self.microphone
        results[.cloud] = Self.cloudStatus(WorkspaceCloudDirectory(WorkspaceKeychain.workspaces()).access)
        results[.systemAudio] = systemAudio
        async let device: Void = record(.device, await Self.deviceStatus())
        async let internet: Void = record(.internet, await Self.internetStatus(using: session))
        async let workspace: Void = record(.workspace, await workspaceStatus(using: session))
        async let alerts: Void = record(.notifications, await Self.notificationStatus(for: activeWorkspace))
        _ = await (device, internet, workspace, alerts)
    }

    private func record(_ check: Check, _ result: Result) {
        guard !Task.isCancelled else { return }
        results[check] = result
        if check == .workspace { AccessibilityNotification.Announcement(result.detail).post() }
    }

    fileprivate static func deviceStatus() async -> Result {
        let monitor = NWPathMonitor()
        defer { monitor.cancel() }
        var updates = monitor.makeAsyncIterator()
        guard let path = await updates.next() else { return Result(.unavailable, "Device network status unavailable") }
        switch path.status {
        case .satisfied: return Result(.passed, "Connected to a network")
        case .unsatisfied: return Result(.failed, "Device offline. Check your network connection.")
        case .requiresConnection: return Result(.action, "Connect to a network and check again")
        @unknown default: return Result(.unavailable, "Device network status unavailable")
        }
    }

    private func requestMicrophone() async {
        askingPermission = true
        defer { askingPermission = false }
        _ = await AVCaptureDevice.requestAccess(for: .audio)
        results[.microphone] = Self.microphone
    }

    fileprivate static func cloudStatus(_ access: WorkspaceCloudDirectory.Access) -> Result {
        switch access {
        case .checking: Result(.checking, "Checking iCloud Keychain")
        case .unavailable: Result(.unavailable, "iCloud Keychain could not be read")
        case .empty: Result(.optional, "Keychain is readable. No synced sign-ins on this device.")
        case .available: Result(.passed, "Synced workspace sign-ins are readable")
        }
    }

    fileprivate static func audioAvailability(for workspace: Endpoint?) -> Result {
        #if os(macOS)
        if let workspace, workspace.origin.scheme != "http" {
            return Result(.optional, "System audio is available in the local Mac workspace")
        }
        return SystemAudio.available ? Result(.optional, "System audio is available to test with your permission")
                                     : Result(.unavailable, "System audio capture is unavailable on this Mac")
        #elseif canImport(ScreenCaptureKit) && !targetEnvironment(simulator)
        if #available(iOS 27, *), SystemAudio.available {
            return Result(.optional, "Choose what to share in the system audio picker")
        }
        return Result(.unavailable, "System audio sharing picker unavailable")
        #else
        return Result(.unavailable, "System audio sharing unavailable on this device")
        #endif
    }

    fileprivate static func notificationStatus(for workspace: Endpoint?) async -> Result {
        notificationStatus(await WorkspaceNotifications.shared.alertAccess(for: workspace))
    }

    fileprivate static var notificationSettingsURL: URL {
        #if os(iOS)
        URL(string: UIApplication.openNotificationSettingsURLString)!
        #else
        URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension")!
        #endif
    }

    fileprivate static func requestDeviceNotifications() async -> Result {
        guard !Task.isCancelled else { return await notificationStatus(for: nil) }
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert])
        return await notificationStatus(for: nil)
    }

    fileprivate static func notificationStatus(_ access: WorkspaceAlertAccess) -> Result {
        switch access {
        case .denied: Result(.off, "Access is off in Settings")
        case .deliveryDisabled: Result(.off, "Turn on alerts in Settings")
        case .permissionNeeded: Result(.optional, "Notification permission is needed for workspace alerts")
        case .appAllowed: Result(.passed, "Notifications are allowed on this device")
        case .workspaceOff: Result(.optional, "Turn on alerts for this workspace")
        case .workspaceOn: Result(.passed, "Workspace alerts on")
        case .unknown: Result(.failed, "Notification permission could not be read")
        }
    }

    private func requestNotifications() async {
        askingPermission = true
        defer { askingPermission = false }
        if let activeWorkspace {
            _ = await WorkspaceNotifications.shared.setEnabled(
                true, for: activeWorkspace, workspaceIsCurrent: { workspaceIsCurrent(activeWorkspace) }, confirm: { true }
            )
            results[.notifications] = await Self.notificationStatus(for: activeWorkspace)
        } else {
            results[.notifications] = await Self.requestDeviceNotifications()
        }
    }

    private func testSystemAudio() async {
        guard canTestSystemAudio else { return }
        testingAudio = true
        defer { testingAudio = false }
        results[.systemAudio] = Result(.checking, "Checking system audio access…")
        let result = await Self.performAudioTest()
        if !Task.isCancelled { results[.systemAudio] = result }
    }

    fileprivate static func performAudioTest() async -> Result {
        #if os(macOS) || (canImport(ScreenCaptureKit) && !targetEnvironment(simulator))
        if #available(iOS 27, *) {
            let capture = SystemAudio()
            defer { capture.stop() }
            #if os(macOS)
            guard !Task.isCancelled, let window = NSApp.keyWindow else {
                return Result(.off, "System audio sharing is off. Try again.")
            }
            let confirmed = await withTaskCancellationHandler {
                await capture.confirm(in: window)
            } onCancel: {
                Task { @MainActor in capture.stop() }
            }
            guard confirmed else { return Result(.off, "System audio sharing is off. Try again.") }
            #endif
            guard !Task.isCancelled, await capture.start() else {
                return Result(.off, "System audio sharing didn’t start. Try again.")
            }
            for _ in 0..<20 {
                guard !Task.isCancelled, capture.read() != nil else {
                    return Result(.off, "System audio sharing stopped. Try again.")
                }
                if capture.hasReceivedSamples { break }
                try? await Task.sleep(for: .milliseconds(100))
            }
            return capture.hasReceivedSamples ? Result(.passed, "Test received audio samples. Test sharing has ended.")
                : Result(.optional, "Sharing started, but no audio samples arrived. Try playing sound and test again.")
        }
        #endif
        return Result(.unavailable, "System audio sharing is unavailable on this device")
    }

    fileprivate static func internetStatus(using session: URLSession) async -> Result {
        guard let url = URL(string: "https://heinzel.team/") else { return Result(.failed, "Check unavailable") }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 4)
        request.httpMethod = "HEAD"
        do {
            let route = RefuseRedirects()
            let (_, response) = try await session.data(for: request, delegate: route)
            if let response = response as? HTTPURLResponse, (200..<400).contains(response.statusCode) {
                return Result(.passed, "Public website reachable", clientAddress: route.connectionAddresses.withLock { $0.client })
            }
            return Result(.failed, "Public website did not answer as expected")
        } catch {
            return Result(.failed, "Can’t reach heinzel.team")
        }
    }

    private func workspaceStatus(using session: URLSession) async -> Result {
        guard let origin else { return Result(.action, "Enter a workspace link first") }
        if let issue = await Endpoint.connectionIssue(at: origin, using: session) {
            return Result(.failed, issue.title)
        }
        if needsSignIn { return Result(.action, "Reachable · Sign-in required") }
        if activeWorkspace != nil { return Result(.passed, "Connected") }
        return Result(.passed, "Reachable")
    }
}
