import AVFoundation
import SwiftUI
import UserNotifications
#if os(iOS)
import UIKit
#endif

private let workspaceSwitchTitle = "Switch Workspace"
private let workspaceSwitchSymbol = "arrow.left.arrow.right"

private let heinzelTagline = "Durable agents accelerating product teams, like the little elves from Cologne."

private var heinzelAppVersion: String {
    let bundle = Bundle.main
    let version = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
    let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown"
    return "Version \(version) · Build \(build)"
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
}

private struct ConnectionFooter: View {
    var showTagline = false

    var body: some View {
        VStack(spacing: 4) {
            if showTagline { Text(heinzelTagline).font(.footnote) }
            Text(heinzelAppVersion).font(.caption2)
        }
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
    let workspace: WorkspaceVersionObservation?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let app {
                VStack(alignment: .leading, spacing: 2) {
                    Text("App \(app.build.version) · Build \(app.build.number)").fontWeight(.medium)
                    Text("This build observed here since \(date(app.observedSince))")
                }
            } else {
                Text("App build details are unavailable.")
            }

            if let workspace {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Workspace web \(workspace.version)").fontWeight(.medium)
                    Text("Version last seen \(date(workspace.observedAt))")
                    if let installed = workspace.installedVersion {
                        Text("Installed hz \(installed)")
                        if let updated = workspace.installedUpdatedAt {
                            Text("Installed hz updated \(date(updated))")
                        } else {
                            Text("Workspace did not report an installation update date.")
                        }
                    } else {
                        Text("Workspace did not report the installed hz version or update date.")
                    }
                    if let daemon = workspace.daemonLastVersion, let observed = workspace.daemonLastObservedAt {
                        Text("Daemon \(daemon) · Last observed \(date(observed))")
                        if let stale = workspace.daemonObservationStale {
                            Text(stale ? "Daemon observation was stale when checked." : "Daemon observation was fresh when checked.")
                        }
                    } else {
                        Text("Workspace did not report a daemon observation.")
                    }
                    if workspace.version.range(of: #"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$"#, options: .regularExpression) != nil,
                       let release = URL(string: "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag/v\(workspace.version)") {
                        Link("Release details", destination: release)
                    }
                }
            } else {
                Text("No workspace version has been recorded here. Version details appear when the workspace can report them.")
            }
        }
        .font(.footnote)
        .foregroundStyle(ConnectionPalette.muted)
        .connectionPanel()
    }

    private func date(_ value: Date) -> String {
        value.formatted(date: .abbreviated, time: .shortened)
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
    #endif

    init() { WorkspaceNotifications.shared.install() }

    var body: some Scene {
        #if os(macOS)
        Window("Heinzelmännchen", id: "workspace") {
            ConnectionView(service: service)
                .frame(minWidth: 550, minHeight: 440)
        }
        .defaultSize(width: 1100, height: 760)
        .windowStyle(.hiddenTitleBar)
        .commands {
            WorkspaceCommands()
        }
        #else
        WindowGroup {
            ConnectionView()
        }
        #endif
    }
}

private extension Notification.Name {
    static let showHeinzelConnection = Notification.Name("showHeinzelConnection")
}

#if os(macOS)
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

enum WorkspaceMenuAction: String {
    case settings, updates, search, sidebar, terminal, browser, chat, notifications
    case newChat = "new-chat"
    case today = "go:today", heinzel = "go:heinzel", team = "go:team", plugins = "go:plugins"
    case help, changelog, doctor, copy, daemon
    case reload, zoomIn, zoomOut, resetZoom

    static let notification = Notification.Name("heinzelWorkspaceMenuAction")
}

private struct WorkspaceCommands: Commands {
    @FocusedValue(\.workspaceMenuAvailable) private var workspaceAvailable
    @FocusedValue(\.connectionMenuAvailable) private var connectionAvailable
    private let website = "https://heinzel.team"
    private let repository = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"

    var body: some Commands {
        CommandGroup(replacing: .appInfo) {
            Button("About Heinzelmännchen") { NSApp.orderFrontStandardAboutPanel(nil) }
        }
        CommandGroup(replacing: .appSettings) {
            command("Settings…", .settings).keyboardShortcut(",")
            command("Check for Updates…", .updates)
            Button("Connection…") {
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
        Button(title) { NotificationCenter.default.post(name: WorkspaceMenuAction.notification, object: action) }
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
    #if os(macOS)
    let service: LocalService
    #endif

    @Environment(\.scenePhase) private var scenePhase
    @State private var started = false
    @State private var showingColdStart = true
    @State private var startupStage = "Preparing Heinzelmännchen…"
    @State private var startupIsSlow = false
    @AppStorage("serverOrigin") private var serverOrigin = ""
    @AppStorage("workspaceHistory") private var workspaceHistory = Data()
    @AppStorage("forgottenWorkspaces") private var forgottenWorkspaces = Data()
    @AppStorage("workspaceBackendVersions") private var workspaceBackendVersions = Data()
    @AppStorage("appBuildObservation") private var appBuildObservation = Data()
    @State private var syncedWorkspaces: [RecentWorkspace] = []
    @State private var cloudDirectoryUnavailable = false
    @State private var cloudDirectoryChecked = false
    @State private var attemptedCloudSignIns: [String: Date] = [:]
    @State private var cloudSignInInProgress = false
    #if os(macOS)
    @AppStorage(CloudSyncPreference.key) private var syncWorkspaceSignIn = true
    @AppStorage("publishedWorkspaceOrigin") private var publishedWorkspaceOrigin = ""
    @State private var syncStatus: String?
    private let offersCloudSignIn = Bundle.main.object(forInfoDictionaryKey: "HeinzelCloudSignIn") as? String == "YES"
    #endif
    @State private var link = ""
    @State private var endpoint: Endpoint?
    @State private var connecting = false
    @State private var stoppingLocalWorkspace = false
    @State private var loadingWorkspace = false
    @State private var workspaceBackground = Color("StartupBackground")
    @State private var error: String?
    @State private var widgetClearFailed = false
    @State private var showConnection = false
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    #if os(iOS)
    @State private var nativeFooterWidth: CGFloat = 88
    @State private var nativeFooterBottom: CGFloat = 0
    @State private var nativeFooterGeneration = 0
    @State private var nativeFooterSlot: CGRect?
    #endif
    @State private var connectionSheetContentHeight: CGFloat = 350
    private var connectionSheetHeight: CGFloat {
        min(520, max(300, connectionSheetContentHeight.rounded(.up)))
    }
    @State private var showDisconnectConfirmation = false
    @State private var choosingWorkspace = false
    @State private var showConnectionHelp = false
    @State private var helpAfterConnection = false
    #if os(iOS)
    @State private var shortcutRouter = AppShortcutRouter.shared
    #endif
    @State private var pendingShortcutURL: NativeRoute?
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
    @State private var promptComposer = NativePromptComposer()
    @State private var sharedWorkspace: Endpoint?
    @State private var loadFailure: WorkspaceProblem?
    @State private var loadRevision = 0
    @State private var widgetViewRotation = WidgetViewRotation()
    @State private var connectionIssue: WorkspaceProblem?
    @State private var verifiedConnectionOrigin: URL?
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
    @State private var draftRevision = 0
    @State private var checkedDraft: URL?
    @State private var draftIssue: WorkspaceProblem?
    #if os(iOS)
    @State private var manualDraftProbe: DraftProbe?
    @State private var refreshingConnection = false
    #endif

    #if os(macOS)
    @State private var connectionMenuIntent = MacConnectionIntent()

    private var menuState: MacMenuState {
        MacMenuState(hasEndpoint: endpoint != nil, choosingWorkspace: choosingWorkspace,
                     starting: showingColdStart || connecting || stoppingLocalWorkspace,
                     loading: loadingWorkspace, hasProblem: visibleProblem != nil,
                     connectionPresented: showConnection,
                     otherSheetPresented: showConnectionHelp || helpAfterConnection || showHeinzelPicker ||
                        showPromptComposer || showDisconnectConfirmation || sharedWorkspace != nil)
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

    private struct DraftProbe: Equatable {
        let origin: URL
        let revision: Int
    }

    private var draftProbe: DraftProbe? {
        draftOrigin.map { DraftProbe(origin: $0, revision: draftRevision) }
    }

    private var draftOrigin: URL? {
        draftWorkspace?.origin
    }

    private var draftWorkspace: Endpoint? {
        guard choosingWorkspace || endpoint == nil else { return nil }
        return parseWorkspaceLink(link)
    }

    private var forgottenWorkspaceDates: [String: Date] {
        (try? JSONDecoder().decode([String: Date].self, from: forgottenWorkspaces)) ?? [:]
    }

    private var availableCloudWorkspaces: [RecentWorkspace] {
        let forgotten = forgottenWorkspaceDates
        return syncedWorkspaces.filter { $0.lastUsed > (forgotten[$0.address] ?? .distantPast) }
    }

    private var shouldRefreshSyncedWorkspaces: Bool {
        scenePhase == .active && (endpoint == nil || choosingWorkspace || showConnection || loadFailure == .signIn)
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
        var versions = (try? JSONDecoder().decode([String: WorkspaceVersionObservation].self, from: workspaceBackendVersions)) ?? [:]
        versions.removeValue(forKey: item.address)
        if let encoded = try? JSONEncoder().encode(versions) { workspaceBackendVersions = encoded }
        var local = (try? JSONDecoder().decode([RecentWorkspace].self, from: workspaceHistory)) ?? []
        local.removeAll { $0.address == item.address }
        if let encoded = try? JSONEncoder().encode(local) { workspaceHistory = encoded }
        var forgotten = (try? JSONDecoder().decode([String: Date].self, from: forgottenWorkspaces)) ?? [:]
        let latestSync = syncedWorkspaces.first { $0.address == item.address }?.lastUsed ?? .distantPast
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

    private var mayRequestStartupNotifications: Bool {
        #if DEBUG
        if ProcessInfo.processInfo.environment["HZ_SPIKE_SERVER"] != nil { return false }
        #endif
        return scenePhase == .active && !showingColdStart && !connecting && !loadingWorkspace &&
            !stoppingLocalWorkspace && !showConnection && !showConnectionHelp && !helpAfterConnection &&
            sharedWorkspace == nil && !editingLink
    }

    private var canOpenShortcut: Bool {
        scenePhase == .active && !showingColdStart && !stoppingLocalWorkspace &&
            !showDisconnectConfirmation && !dismissingPromptComposer &&
            (sharedWorkspace == nil || endpoint == nil)
    }

    private func openShortcut(_ shortcut: NativeRoute) {
        editingLink = false
        if showPromptComposer && shortcut != .writePrompt {
            pendingShortcutURL = shortcut
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

    var body: some View {
        content
        .accessibilityHidden(choosingWorkspace)
        .allowsHitTesting(!choosingWorkspace)
        .overlay {
            if choosingWorkspace { setup }
        }
        #if os(macOS)
        .focusedSceneValue(\.workspaceMenuAvailable, menuState.workspaceAvailable)
        .focusedSceneValue(\.connectionMenuAvailable, menuState.connectionRoute != .unavailable)
        .onReceive(NotificationCenter.default.publisher(for: .showHeinzelConnection)) { _ in
            openConnectionFromMenu()
        }
        #endif
        .sheet(isPresented: $showConnection, onDismiss: {
            if helpAfterConnection {
                helpAfterConnection = false
                showConnectionHelp = true
            }
        }) {
            if let endpoint { connectionSheet(for: endpoint) }
        }
        .onChange(of: showConnection) { _, open in
            if open { versionRefreshRevision &+= 1 }
        }
        .sheet(isPresented: $showConnectionHelp) {
            ConnectionHelp(
                origin: diagnosticOrigin,
                activeWorkspace: !choosingWorkspace && draftOrigin == nil && loadFailure == nil && !loadingWorkspace ? endpoint : nil,
                needsSignIn: !choosingWorkspace && loadFailure == .signIn,
                workspaceIsCurrent: { target in endpoint == target && loadFailure == nil && !loadingWorkspace }
            )
                .presentationBackground(Color("StartupBackground"))
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
            // A short cold-start beat keeps the branded screen perceptible even when the host responds immediately.
            let earliest = ContinuousClock.now + .milliseconds(650)
            await openInitialWorkspace()
            try? await ContinuousClock().sleep(until: earliest)
            showingColdStart = false
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
        .task(id: syncWorkspaceSignIn ? endpoint?.origin : nil) {
            await publishLocalSignIn()
        }
        .onChange(of: syncWorkspaceSignIn) { _, enabled in
            if !enabled { removePublishedSignIn() }
        }
        #endif
        .task(id: mayRequestStartupNotifications) {
            guard mayRequestStartupNotifications else { return }
            // Give the startup view or connection sheet time to leave before presenting the system prompt.
            do { try await Task.sleep(for: .milliseconds(350)) } catch { return }
            await WorkspaceNotifications.shared.requestStartupAuthorization {
                mayRequestStartupNotifications
            }
        }
        .onChange(of: scenePhase) { _, phase in
            #if os(iOS)
            if phase != .active, signInProbeScope == connectionScopeRevision {
                loadFailure = .signIn
                loadingWorkspace = false
            }
            invalidateConnectionScope()
            #endif
            if phase == .active && draftOrigin != nil { draftRevision += 1 }
        }
        .onChange(of: link) { _, _ in error = nil }
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
        .task(id: draftProbe) {
            guard let probe = draftProbe else { return }
            #if os(iOS)
            guard manualDraftProbe != probe else { return }
            #endif
            let origin = probe.origin
            let session = Endpoint.probeSession()
            defer { session.invalidateAndCancel() }
            checkedDraft = nil
            do { try await Task.sleep(for: .milliseconds(450)) } catch { return }
            let issue = await Endpoint.connectionIssue(at: origin, using: session)
            guard !Task.isCancelled, draftProbe == probe else { return }
            draftIssue = issue
            checkedDraft = origin
        }
        .task(id: monitoredOrigin) {
            verifiedConnectionOrigin = nil
            #if os(iOS)
            connectionProbeRevision += 1
            connectionProbe?.task.cancel()
            connectionProbe = nil
            #endif
            guard let origin = monitoredOrigin else { return }
            await watchConnection(to: origin)
        }
        .task(id: canOpenShortcut ? pendingShortcutURL : nil) { @MainActor in
            guard !Task.isCancelled, canOpenShortcut, let shortcut = pendingShortcutURL else { return }
            pendingShortcutURL = nil
            openShortcut(shortcut)
        }
        #if os(iOS)
        .task(id: canOpenShortcut ? shortcutRouter.pending : nil) { @MainActor in
            guard !Task.isCancelled, canOpenShortcut, let shortcut = shortcutRouter.pending else { return }
            shortcutRouter.pending = nil
            openShortcut(shortcut)
        }
        #endif
        .onOpenURL { url in
            if let shortcut = NativeRoute(url: url) {
                pendingShortcutURL = shortcut
                return
            }
            guard let target = Endpoint.sharedWorkspace(url) else {
                if endpoint == nil { error = "This Heinzel link is incomplete. Ask the host to share it again." }
                return
            }
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
        refreshSyncedWorkspaces()
        if !serverOrigin.isEmpty && sharedWorkspace == nil {
            startupStage = "Checking workspace connection…"
            await connectRecent(serverOrigin, checkHost: false)
        } else {
            startupStage = "Looking for a saved workspace…"
        }
        #endif
    }

    private var requestedChat: HeinzelChatRequest? {
        !loadingWorkspace && loadFailure == nil ? chatRequest : nil
    }

    @ViewBuilder
    private var content: some View {
        if let endpoint, loadFailure == .signIn {
            Group {
                if showingColdStart { startup } else { recovery(visibleProblem ?? .signIn, for: endpoint) }
            }
            #if os(iOS)
            .safeAreaInset(edge: .bottom, spacing: 0) { recoveryWorkspaceFooter(for: endpoint) }
            #endif
        } else if let endpoint {
            let revision = loadRevision
            let obscured = choosingWorkspace || showingColdStart || loadingWorkspace || visibleProblem != nil
            VStack(spacing: 0) {
                WorkspaceView(endpoint: endpoint, isObscured: obscured,
                              reportsWidgetActivity: mayRefreshWidgetActivity && !obscured,
                              isWidgetWorkspaceCurrent: {
                    self.endpoint == endpoint && loadRevision == revision && widgetWorkspaceScope != nil
                }, isMenuWorkspaceCurrent: {
                    #if os(macOS)
                    self.endpoint == endpoint && loadRevision == revision && menuState.workspaceAvailable
                    #else
                    false
                    #endif
                }, isEnrollmentViewCurrent: {
                    #if os(iOS)
                    self.endpoint == endpoint && loadRevision == revision && !choosingWorkspace && !showingColdStart && scenePhase == .active
                    #else
                    self.endpoint == endpoint && loadRevision == revision && !choosingWorkspace && !showingColdStart
                    #endif
                }, chatRequest: requestedChat, promptComposer: promptComposer, widgetRefreshRevision: widgetRefreshRevision,
                versionRefreshRevision: versionRefreshRevision, footerWidth: nativeFooterWidthValue, footerBottom: nativeFooterBottomValue,
                footerGeneration: nativeFooterGenerationValue,
                reportFooterSlot: { slot in
                    #if os(iOS)
                    guard self.endpoint == endpoint, loadRevision == revision else { return }
                    nativeFooterSlot = slot
                    #endif
                }, ready: {
                    guard self.endpoint == endpoint, loadRevision == revision else { return }
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
                        selectedOrigin: serverOrigin, requestRevision: revision, currentRevision: loadRevision,
                        into: &versions) else { return }
                    let newest = versions.sorted { $0.value.observedAt > $1.value.observedAt }.prefix(12)
                    if let encoded = try? JSONEncoder().encode(Dictionary(uniqueKeysWithValues: newest.map { ($0.key, $0.value) })) {
                        workspaceBackendVersions = encoded
                    }
                }, reportError: { message in
                    guard self.endpoint == endpoint, loadRevision == revision else { return }
                    if message == .signIn && endpoint.origin.scheme == "https" {
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
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                #if os(iOS)
                .ignoresSafeArea(.container, edges: nativeFooterSlot == nil ? [] : .bottom)
                #endif
                .id(endpoint.url)
                .id(revision)
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
                    } else if let problem = visibleProblem {
                        recovery(problem, for: endpoint)
                    } else if loadingWorkspace {
                        startup
                    }
                }
                #if os(macOS)
                .ignoresSafeArea(edges: .top)
                #endif
                #if os(iOS)
                if !obscured && nativeFooterSlot == nil {
                    HStack(spacing: 0) {
                        workspaceFooterButton(for: endpoint, width: nativeFooterWidth)
                        Spacer(minLength: 0)
                    }
                    .frame(height: 48)
                    .background(workspaceBackground.ignoresSafeArea(.container, edges: .bottom))
                }
                #endif
            }
            #if os(iOS)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if obscured { recoveryWorkspaceFooter(for: endpoint) }
            }
            .overlay {
                if !obscured { workspaceFooterControl(for: endpoint, fallback: false) }
            }
            #endif
        } else if connecting || showingColdStart {
            startup
            #if os(iOS)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if let saved = Endpoint(serverOrigin) { recoveryWorkspaceFooter(for: saved) }
            }
            #endif
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

    private var nativeFooterWidthValue: CGFloat {
        #if os(iOS)
        nativeFooterWidth
        #else
        88
        #endif
    }

    private var nativeFooterBottomValue: CGFloat {
        #if os(iOS)
        nativeFooterBottom
        #else
        0
        #endif
    }

    private var nativeFooterGenerationValue: Int {
        #if os(iOS)
        nativeFooterGeneration
        #else
        0
        #endif
    }

    #if os(iOS)
    // Native recovery screens reserve their own space; the web footer uses reported geometry.
    private func recoveryWorkspaceFooter(for endpoint: Endpoint) -> some View {
        Button {
            if self.endpoint == nil { chooseAnotherWorkspace() } else { showConnection = true }
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "point.3.connected.trianglepath.dotted").accessibilityHidden(true)
                Text(workspaceName(endpoint.origin)).fixedSize(horizontal: false, vertical: true)
                Image(systemName: "chevron.down").accessibilityHidden(true)
            }
            .font(.footnote.weight(.medium))
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Workspace: \(workspaceName(endpoint.origin))")
        .accessibilityHint("Open connection details or switch workspace")
        .foregroundStyle(ConnectionPalette.ink)
        .environment(\.colorScheme, .light)
        .background(Color("StartupBackground").ignoresSafeArea(.container, edges: .bottom))
    }

    private func workspaceFooterControl(for endpoint: Endpoint, fallback: Bool) -> some View {
        GeometryReader { geometry in
            let bottom = geometry.safeAreaInsets.bottom
            let name = workspaceName(endpoint.origin)
            let textWidth = (name as NSString).size(withAttributes: [.font: UIFont.preferredFont(forTextStyle: .footnote)]).width
            let width = max(44, min(180, geometry.size.width * 0.45, max(76, textWidth + 52)))
            let slot = fallback ? nil : nativeFooterSlot
            ZStack(alignment: .bottomLeading) {
                if fallback {
                    workspaceBackground
                        .frame(height: 48)
                        .frame(maxHeight: .infinity, alignment: .bottom)
                        .ignoresSafeArea(.container, edges: .bottom)
                        .allowsHitTesting(false)
                }
                if fallback || slot != nil {
                    workspaceFooterButton(for: endpoint, width: width)
                        .position(x: slot?.midX ?? width / 2, y: slot?.midY ?? geometry.size.height - 24)
                }
            }
            .onAppear { nativeFooterWidth = width; nativeFooterBottom = bottom }
            .onChange(of: width) { _, value in nativeFooterSlot = nil; nativeFooterWidth = value }
            .onChange(of: bottom) { _, value in nativeFooterSlot = nil; nativeFooterBottom = value }
            .onChange(of: geometry.size) { _, _ in nativeFooterSlot = nil; nativeFooterGeneration &+= 1 }
            .onChange(of: dynamicTypeSize) { _, _ in nativeFooterSlot = nil; nativeFooterGeneration &+= 1 }
            .onChange(of: nativeFooterSlot == nil) { _, _ in nativeFooterGeneration &+= 1 }
        }
    }

    private func workspaceFooterButton(for endpoint: Endpoint, width: CGFloat) -> some View {
        let name = workspaceName(endpoint.origin)
        return Button {
            if self.endpoint == nil { chooseAnotherWorkspace() } else { showConnection = true }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: "point.3.connected.trianglepath.dotted").accessibilityHidden(true)
                if width >= 76 { Text(name).lineLimit(1).truncationMode(.tail) }
                Image(systemName: "chevron.down").accessibilityHidden(true)
            }
            .font(.footnote.weight(.medium))
            .frame(width: width, height: 48)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Workspace \(name)")
        .accessibilityHint("Shows saved workspaces and connection actions")
    }
    #endif

    private func connectionSheet(for endpoint: Endpoint) -> some View {
        let problem = visibleProblem ?? connectionIssue
        let confirmed = !loadingWorkspace && (endpoint.origin.scheme == "http" || verifiedConnectionOrigin == endpoint.origin)
        return ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                connectionSheetHeader("Workspace") {
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
                VStack(alignment: .leading, spacing: 5) {
                    Group {
                        if let problem {
                            Label(problem == .signIn ? "Sign-in required" : problem.title, systemImage: problem.icon)
                                .foregroundStyle(ConnectionPalette.warning)
                        } else if confirmed {
                            Label("Connected", systemImage: "checkmark.circle.fill")
                                .foregroundStyle(ConnectionPalette.accent)
                        } else {
                            ProgressView("Checking connection…")
                                .controlSize(.small)
                                .foregroundStyle(ConnectionPalette.muted)
                        }
                    }
                    .font(.callout.weight(.semibold))
                    .accessibilityIdentifier("workspaceConnectionState")
                    if endpoint.origin.scheme == "https", workspaceName(endpoint.origin) != endpoint.origin.host() {
                        workspaceIdentity(endpoint.origin)
                            .font(.callout.weight(.medium))
                    }
                    Text(endpoint.origin.absoluteString)
                        .font(.footnote.monospaced())
                        .foregroundStyle(ConnectionPalette.muted)
                        .textSelection(.enabled)
                }
                .connectionPanel()
                if let error {
                    Label(error, systemImage: "exclamationmark.circle")
                        .font(.callout)
                        .foregroundStyle(ConnectionPalette.warning)
                }
                if let problem {
                    if problem == .signIn || problem == .wrongAddress {
                        Button(problem == .signIn ? "Enter Sign-in Link" : "Enter Workspace Link") {
                            if problem == .wrongAddress { chooseAnotherWorkspace() }
                            showConnection = false
                        }
                        .buttonStyle(.borderedProminent)
                    } else if loadFailure != nil || problem.canReconnectAutomatically {
                        Button(loadFailure != nil ? "Try Again" : "Reload Workspace") {
                            showConnection = false
                            retryWorkspace()
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }
                VStack(spacing: 0) {
                    Button(action: chooseAnotherWorkspace) { connectionRow(workspaceSwitchTitle, workspaceSwitchSymbol) }
                        .foregroundStyle(ConnectionPalette.accent)
                    Divider()
                    Button {
                        showDisconnectConfirmation = true
                    } label: {
                        connectionRow("Disconnect…", "rectangle.portrait.and.arrow.right")
                    }
                    .foregroundStyle(ConnectionPalette.warning)
                }
                .buttonStyle(.plain)
                WorkspaceVersionDetails(
                    app: (try? JSONDecoder().decode(AppBuildObservation.self, from: appBuildObservation))
                        .flatMap { $0.build == heinzelAppBuild ? $0 : nil },
                    workspace: (try? JSONDecoder().decode([String: WorkspaceVersionObservation].self,
                        from: workspaceBackendVersions))?[endpoint.origin.absoluteString]
                )
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
            }
            .frame(maxWidth: 420, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, 16)
            .frame(maxWidth: .infinity)
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height in
                connectionSheetContentHeight = height
            }
        }
        #if os(iOS)
        .refreshable { await refreshConnectionStatus(for: endpoint) }
        #endif
        #if os(macOS)
        .frame(width: 460, height: connectionSheetHeight)
        #endif
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
        .presentationBackground(Color("StartupBackground"))
        .presentationDetents([.height(connectionSheetHeight), .large])
        .alert("Disconnect from this workspace?", isPresented: $showDisconnectConfirmation) {
            Button("Cancel", role: .cancel) {
                // SwiftUI dismisses the alert and leaves the connection intact.
            }
            Button("Disconnect", role: .destructive, action: disconnect)
        } message: {
            Text(endpoint.origin.scheme == "http"
                ? "This stops the local workspace and returns to the chooser."
                : "You’ll return to the workspace chooser.")
        }
    }

    private func connectionRow(_ title: String, _ icon: String) -> some View {
        Label(title, systemImage: icon)
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .contentShape(Rectangle())
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
                if cloudSignInInProgress {
                    ProgressView("Signing in with iCloud…")
                        .controlSize(.small)
                        .font(.callout)
                } else {
                    signInActions(for: endpoint)
                }
                Button(workspaceSwitchTitle, systemImage: workspaceSwitchSymbol) { chooseDifferentWorkspace(from: endpoint) }
                    .buttonStyle(.borderless)
                    .controlSize(.large)
                    .disabled(connecting)
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

    private func signInActions(for endpoint: Endpoint) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if let cloud = availableCloudWorkspaces.first(where: { $0.address == endpoint.origin.absoluteString }) {
                Button("Sign In with iCloud", systemImage: "icloud") {
                    Task { await connectCloudWorkspace(cloud) }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                Divider()
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    Label(cloudDirectoryUnavailable
                        ? "Synced sign-ins couldn’t be read on this device."
                        : cloudDirectoryChecked
                            ? "No synced sign-in for this workspace is available on this device."
                            : "Checking for a synced sign-in…", systemImage: "icloud")
                    if endpoint.origin.host?.hasSuffix(".ts.net") == true {
                        Text("Enable private Tailscale sharing on the Mac, then Sync Sign-in with iCloud in the Mac app’s Connection screen. Both devices need the same Apple Account and iCloud Keychain. If that option is missing on the Mac, use a private sign-in link.")
                    }
                }
                .font(.footnote)
                .foregroundStyle(ConnectionPalette.muted)
                .fixedSize(horizontal: false, vertical: true)
                Divider()
            }
            if endpoint.origin.host?.hasSuffix(".ts.net") == true {
                Text("On the computer running Heinzel, open **Tailscale** at the bottom of the signed-in workspace. Choose **Copy link** or **Share**, send yourself the link privately, then paste it here.")
                    .font(.callout)
                    .foregroundStyle(ConnectionPalette.muted)
            } else {
                Text("Ask the person who runs this workspace to send you a sign-in link, then paste it below.")
                    .font(.callout)
                    .foregroundStyle(ConnectionPalette.muted)
            }
            linkField("Sign-in link") { signIn(at: endpoint) }
            if let message = error ?? signInLinkIssue(for: endpoint) {
                Label(message, systemImage: "exclamationmark.circle")
                    .font(.callout)
                    .foregroundStyle(ConnectionPalette.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Button { signIn(at: endpoint) } label: {
                Text(connecting ? "Signing In…" : "Sign In")
                    .frame(maxWidth: .infinity, minHeight: 30)
            }
            .buttonStyle(.borderedProminent)
            .disabled(trimmedLink.isEmpty || signInLinkIssue(for: endpoint) != nil)
            .controlSize(.large)
        }
        .disabled(connecting)
        .multilineTextAlignment(.leading)
        .connectionPanel()
    }

    private func signInLinkIssue(for endpoint: Endpoint) -> String? {
        guard !trimmedLink.isEmpty else { return nil }
        let target = parseWorkspaceLink(link)
        guard let target else { return "Enter the full sign-in link from the host." }
        guard endpoint.owns(target.origin) else {
            return "This link is for another workspace."
        }
        guard target.token != nil else {
            return "This link has no sign-in access. Get a link from the signed-in workspace on the host computer."
        }
        return nil
    }

    private func chooseDifferentWorkspace(from endpoint: Endpoint) {
        let alternateLink = parseWorkspaceLink(link).flatMap { endpoint.owns($0.origin) ? nil : link }
        chooseAnotherWorkspace()
        if let alternateLink { link = alternateLink }
    }

    private func signIn(at endpoint: Endpoint) {
        guard !connecting, !trimmedLink.isEmpty, signInLinkIssue(for: endpoint) == nil else { return }
        let value = link
        editingLink = false
        Task { await connect(value) }
    }

    private func linkField(_ title: String, submit: @escaping () -> Void) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "link")
                .foregroundStyle(ConnectionPalette.muted)
                .accessibilityHidden(true)
            SecureField(title, text: $link)
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
                    link = ""
                    error = nil
                    editingLink = true
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(ConnectionPalette.muted)
                .accessibilityLabel("Clear link")
            }
            #if os(iOS)
            Rectangle()
                .fill(ConnectionPalette.rule)
                .frame(width: 1, height: 24)
                .accessibilityHidden(true)
            PasteButton(payloadType: String.self) { values in
                if let value = values.first { link = value }
            }
            .labelStyle(.titleOnly)
            .buttonStyle(.borderless)
            .controlSize(.regular)
            .fixedSize()
            .frame(minHeight: 44)
            #endif
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
        .task(id: stoppingLocalWorkspace) {
            startupIsSlow = false
            guard !stoppingLocalWorkspace else { return }
            do { try await Task.sleep(for: .seconds(4)) } catch { return }
            startupIsSlow = true
        }
    }

    private var startupText: String {
        if stoppingLocalWorkspace { return "Stopping workspace…" }
        if startupIsSlow { return "Still trying to open the workspace…" }
        if showingColdStart { return startupStage }
        return "Opening workspace…"
    }

    private func launchCard(
        loading: Bool = false, compactBrand: Bool = false, @ViewBuilder actions: () -> some View
    ) -> some View {
        ScrollView {
            VStack(spacing: 14) {
                brandHeader(loading: loading, compact: compactBrand || editingLink)
                actions()
                if !editingLink { ConnectionFooter(showTagline: !loading) }
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
        .background { paperBackground }
        .overlay(alignment: .topTrailing) {
            if !loading || startupIsSlow && !stoppingLocalWorkspace {
                helpButton.padding(.trailing, 12)
            }
        }
    }

    private func brandHeader(loading: Bool = false, compact: Bool = false) -> some View {
        VStack(spacing: 6) {
            Image(loading ? "StartupIcon" : "HeinzelCrew")
                .resizable()
                .scaledToFit()
                .frame(width: compact ? 76 : (loading ? 96 : 133), height: compact ? 48 : (loading ? 96 : 95))
                .accessibilityHidden(true)
            Text("Heinzelmännchen")
                .font(.system(compact ? .title2 : .largeTitle, design: .serif, weight: .semibold))
                .minimumScaleFactor(dynamicTypeSize.isAccessibilitySize ? 1 : 0.7)
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
                .foregroundStyle(ConnectionPalette.ink)
        }
    }

    private var helpButton: some View {
        connectionIconButton("questionmark.circle", label: "Connection Help") {
            editingLink = false
            showConnectionHelp = true
        }
    }

    private var paperBackground: some View {
        ZStack {
            Color("StartupBackground")
            RadialGradient(
                colors: [.white.opacity(0.7), Color(red: 0.81, green: 0.72, blue: 0.57).opacity(0.23)],
                center: .center, startRadius: 30, endRadius: 620
            )
            Canvas(rendersAsynchronously: true) { context, size in
                var grain = Path()
                var seed: UInt64 = 67
                func sample() -> Double {
                    seed = 1_664_525 &* seed &+ 1_013_904_223
                    return Double(UInt32(truncatingIfNeeded: seed)) / 4_294_967_296
                }
                let grainCount = min(12_000, Int(size.width * size.height / 34))
                for _ in 0..<grainCount {
                    let x = sample() * size.width
                    let y = sample() * size.height
                    grain.addEllipse(in: CGRect(x: x, y: y, width: 0.55 + sample() * 1.25, height: 0.65))
                }
                context.fill(grain, with: .color(Color(red: 0.43, green: 0.32, blue: 0.21).opacity(0.10)))
            }
            .accessibilityHidden(true)
        }
        .ignoresSafeArea()
    }

    private var setup: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                brandHeader(compact: editingLink).frame(maxWidth: .infinity)
                if choosingWorkspace, let endpoint {
                    let cloud = availableCloudWorkspaces.first { $0.address == endpoint.origin.absoluteString }
                    Button {
                        if let cloud, loadFailure == .signIn {
                            choosingWorkspace = false
                            Task { await connectCloudWorkspace(cloud) }
                        } else {
                            error = nil
                            link = ""
                            choosingWorkspace = false
                        }
                    } label: {
                        HStack(spacing: 8) {
                            Label(loadFailure == .signIn && cloud != nil
                                  ? "Sign in to \(workspaceName(endpoint.origin))"
                                  : "Back to \(workspaceName(endpoint.origin))",
                                  systemImage: "chevron.backward")
                            if cloud != nil {
                                Image(systemName: "icloud")
                                    .accessibilityHidden(true)
                            }
                        }
                        .multilineTextAlignment(.leading)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityValue(cloud == nil ? "" : "Sign-in synced with iCloud")
                }
                #if os(macOS)
                if endpoint?.origin.scheme != "http" {
                    Button("Start Local Workspace", systemImage: "desktopcomputer") {
                        Task { await startLocal() }
                    }
                    .buttonStyle(.bordered)
                    Divider()
                }
                #endif
                let choices = recentWorkspaces.filter { $0.address != endpoint?.origin.absoluteString }
                if !choices.isEmpty {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Recent Workspaces")
                            .font(.system(.headline, design: .serif))
                            .foregroundStyle(ConnectionPalette.ink)
                            .accessibilityAddTraits(.isHeader)
                        ForEach(choices) { item in
                            HStack(spacing: 0) {
                                Button { Task { await connectRecent(item.address, source: item.source) } } label: {
                                    HStack(spacing: 8) {
                                        Image(systemName: "link").accessibilityHidden(true)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(item.name).fontWeight(.medium)
                                            if choices.contains(where: { $0.id != item.id && $0.name == item.name }) {
                                                Text(item.address).font(.caption).foregroundStyle(ConnectionPalette.muted)
                                            }
                                        }
                                        if item.source == .iCloud {
                                            Image(systemName: "icloud")
                                                .font(.footnote)
                                                .foregroundStyle(ConnectionPalette.muted)
                                                .accessibilityHidden(true)
                                        }
                                        Spacer(minLength: 0)
                                    }
                                    .frame(minHeight: 44).contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                .accessibilityValue(item.source == .iCloud ? "Synced with iCloud" : "Added on this device")
                                connectionIconButton("trash", label: "Forget \(item.name)") { forgetWorkspace(item) }
                            }
                        }
                    }
                    .connectionPanel()
                }
                setupForm
            }
            .disabled(connecting)
            .frame(maxWidth: 450, alignment: .leading)
            .padding(28)
            .frame(maxWidth: .infinity)
        }
        #if os(iOS)
        .scrollDismissesKeyboard(.interactively)
        .refreshable { await refreshSetupStatus() }
        #endif
        .defaultScrollAnchor(editingLink ? .top : .center, for: .alignment)
        .frame(maxHeight: .infinity)
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
        .background { paperBackground }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if !editingLink { ConnectionFooter(showTagline: true) }
        }
        .overlay(alignment: .topTrailing) {
            helpButton.padding(.trailing, 12)
        }
    }

    private var setupForm: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Connect to a workspace")
                .font(.system(.title2, design: .serif, weight: .semibold))
                .accessibilityAddTraits(.isHeader)
                .foregroundStyle(ConnectionPalette.ink)
            Text("Enter the link your workspace host shared with you.")
                .font(.callout)
                .foregroundStyle(ConnectionPalette.muted)
            linkField("Workspace link", submit: connectDraft)
            if let origin = draftOrigin { reachability(for: origin) }
            if let message = error ?? (draftWorkspace == nil && !trimmedLink.isEmpty && !editingLink ? "Enter the full workspace link from the host." : nil) {
                Label(message, systemImage: "exclamationmark.circle")
                    .font(.footnote)
                    .foregroundStyle(ConnectionPalette.warning)
            }
            Button(action: connectDraft) {
                Text(connecting ? "Connecting…" : "Connect")
                    .frame(maxWidth: .infinity, minHeight: 30)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(!canConnectDraft)
            #if os(iOS)
            DisclosureGroup("Where do I find my link?") {
                Text("Ask the person hosting your workspace for a sign-in link, then paste it here. Already connected on another device? You can also choose a recent workspace above when one is available.")
                    .font(.footnote)
                    .foregroundStyle(ConnectionPalette.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 6)
            }
            .font(.footnote.weight(.medium))
            #endif
        }
        .connectionPanel()
    }

    private func reachability(for origin: URL) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(origin.absoluteString)
                .font(.footnote.monospaced())
                .foregroundStyle(ConnectionPalette.muted)
                .textSelection(.enabled)
                .accessibilityLabel("Workspace address: \(origin.absoluteString)")
            if checkedDraft == origin {
                Label(draftIssue?.title ?? "Workspace reachable", systemImage: draftIssue?.icon ?? "checkmark.circle.fill")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(draftIssue == nil ? Color(red: 0.19, green: 0.37, blue: 0.27) : ConnectionPalette.warning)
                    .accessibilityIdentifier("workspaceReachability")
                if draftIssue != nil {
                    #if os(iOS)
                    Button("Check Again", systemImage: "arrow.clockwise") { draftRevision += 1 }
                        .buttonStyle(.bordered)
                        .controlSize(.regular)
                    #else
                    connectionIconButton("arrow.clockwise", label: "Check Again") { draftRevision += 1 }
                    #endif
                }
            } else {
                ProgressView("Checking workspace…")
                    .controlSize(.small)
                    .font(.callout)
            }
        }
    }

    private var canConnectDraft: Bool {
        guard let origin = draftOrigin else { return false }
        return !connecting && checkedDraft == origin && draftIssue == nil
    }

    private func connectDraft() {
        guard canConnectDraft else { return }
        let value = link
        editingLink = false
        Task { await connect(value) }
    }

    private func refreshSyncedWorkspaces() {
        cloudDirectoryChecked = true
        if let workspaces = WorkspaceKeychain.workspaces() {
            syncedWorkspaces = workspaces
            cloudDirectoryUnavailable = false
        } else {
            cloudDirectoryUnavailable = true
        }
    }

    private func resumeCloudSignInIfReady() {
        guard !connecting, !editingLink, trimmedLink.isEmpty, !choosingWorkspace,
              !showConnectionHelp, !showConnection, sharedWorkspace == nil, loadFailure == .signIn,
              let address = endpoint?.origin.absoluteString,
              let cloud = availableCloudWorkspaces.first(where: { $0.address == address }),
              attemptedCloudSignIns[address] != cloud.lastUsed else { return }
        // The chosen workspace stays the same; only its credential arrived after the first load.
        Task {
            guard !connecting, !editingLink, trimmedLink.isEmpty, !choosingWorkspace,
                  loadFailure == .signIn, endpoint?.origin.absoluteString == address else { return }
            await connectCloudWorkspace(cloud, reportMissing: false)
        }
    }

    private func connectCloudWorkspace(_ workspace: RecentWorkspace, reportMissing: Bool = true) async {
        guard let credential = WorkspaceKeychain.signIn(address: workspace.address,
                                                       newerThan: forgottenWorkspaceDates[workspace.address]) else {
            if reportMissing { error = "The iCloud sign-in is temporarily unavailable. Try again or paste a sign-in link." }
            return
        }
        await connectUsingCloud(credential)
    }

    private func connectRecent(_ address: String, checkHost: Bool = true, source: RecentWorkspace.Source? = nil) async {
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
        syncedWorkspaces.removeAll { $0.address == item.address }
        syncedWorkspaces.append(item)
        cloudSignInInProgress = true
        defer { cloudSignInInProgress = false }
        await connect(credential.link, checkHost: checkHost, fallBackToWorkspace: fallBackToWorkspace, source: .iCloud)
    }

    private func connect(_ input: String, remember: Bool = true, checkHost: Bool = true,
                         fallBackToWorkspace: Bool = false, source: RecentWorkspace.Source? = nil,
                         initialStartup: Bool = false) async {
        guard !connecting else { return }
        #if os(macOS)
        let menuRevision = initialStartup ? 0 : connectionMenuIntent.revision
        #endif
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var target = parseWorkspaceLink(value) else {
            error = "Enter the full workspace link from the host."
            return
        }
        // A shared address can refer to the active workspace, including a session
        // opened with a sign-in fragment. Keep its WebKit view and in-memory state.
        if target.token == nil, endpoint?.owns(target.origin) == true {
            choosingWorkspace = false
            if loadFailure != nil { retryWorkspace() }
            return
        }
        connecting = true
        error = nil
        defer { connecting = false }
        if checkHost, let issue = await Endpoint.connectionIssue(at: target.origin) {
            error = issue.title
            if endpoint != nil && loadFailure != .signIn && !choosingWorkspace { showConnection = true }
            return
        }
        do {
            var cloudSignInRejected = false
            do { try await target.verify() }
            catch ConnectionError.authentication where fallBackToWorkspace {
                guard let addressOnly = Endpoint(target.origin.absoluteString) else { throw ConnectionError.authentication }
                target = addressOnly
                cloudSignInRejected = source == .iCloud
            }
            if remember {
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
                let provenance = source ?? .pasted
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
            if endpoint?.origin != target.origin { connectionIssue = nil }
            if endpoint != target {
                loadFailure = nil
                loadingWorkspace = true
            }
            #if os(iOS)
            if endpoint != target { invalidateConnectionScope() }
            #endif
            endpoint = target
            #if os(macOS)
            choosingWorkspace = connectionMenuIntent.keepSetup(afterStartedAt: menuRevision)
            #else
            choosingWorkspace = false
            #endif
            if cloudSignInRejected { error = "The workspace didn’t accept the saved iCloud sign-in." }
            #if os(macOS)
            await service.stop()
            #endif
        } catch {
            self.error = (error as? ConnectionError)?.errorDescription ?? "The server could not be reached."
            if endpoint != nil && loadFailure != .signIn && !choosingWorkspace { showConnection = true }
        }
    }

    private func parseWorkspaceLink(_ input: String) -> Endpoint? {
        Endpoint(input) ?? URL(string: input.trimmingCharacters(in: .whitespacesAndNewlines)).flatMap(Endpoint.sharedWorkspace)
    }

    private func chooseAnotherWorkspace() {
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
        if origin.scheme == "http" { return "Local Workspace" }
        let address = origin.absoluteString
        return recentWorkspaces.first { $0.address == address }?.name ?? RecentWorkspace.displayName(for: address)
    }

    private func disconnect() {
        #if os(macOS)
        if endpoint?.origin.scheme == "http" {
            connecting = true
            stoppingLocalWorkspace = true
            Task {
                defer {
                    connecting = false
                    stoppingLocalWorkspace = false
                }
                await service.stop()
            }
        }
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

    private func refreshSetupStatus() async {
        refreshSyncedWorkspaces()
        guard let origin = draftOrigin, !connecting else { return }
        draftRevision += 1
        let probe = DraftProbe(origin: origin, revision: draftRevision)
        manualDraftProbe = probe
        checkedDraft = nil
        defer { if manualDraftProbe == probe { manualDraftProbe = nil } }
        let session = Endpoint.probeSession()
        defer { session.invalidateAndCancel() }
        let issue = await Endpoint.connectionIssue(at: origin, using: session)
        guard !Task.isCancelled, draftProbe == probe else { return }
        draftIssue = issue
        checkedDraft = origin
        AccessibilityNotification.Announcement(issue?.title ?? "Workspace reachable").post()
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
        verifiedConnectionOrigin = result.issue == nil ? target.origin : nil
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
            verifiedConnectionOrigin = issue == nil ? origin : nil
            if recovered && (loadFailure?.canReconnectAutomatically == true || loadingWorkspace) { retryWorkspace() }
            let delay = issue == nil ? 12 : retryDelay
            retryDelay = issue == nil ? 2 : min(retryDelay * 2, 12)
            do { try await Task.sleep(for: .seconds(delay)) } catch { return }
        }
    }

    #if os(macOS)
    private func removePublishedSignIn() {
        guard !publishedWorkspaceOrigin.isEmpty else { return }
        if WorkspaceKeychain.remove(address: publishedWorkspaceOrigin) {
            publishedWorkspaceOrigin = ""
            syncStatus = nil
        } else {
            syncStatus = "Couldn’t remove the synced sign-in."
        }
    }

    private func publishLocalSignIn() async {
        guard offersCloudSignIn, syncWorkspaceSignIn, let endpoint, endpoint.origin.scheme == "http", let token = endpoint.token else { return }
        while !Task.isCancelled, syncWorkspaceSignIn, self.endpoint == endpoint {
            do {
                let target = try await endpoint.sharedSignInOrigin()
                guard !Task.isCancelled, syncWorkspaceSignIn, self.endpoint == endpoint else { return }
                if let target {
                    let address = target.origin.absoluteString
                    if publishedWorkspaceOrigin != address { removePublishedSignIn() }
                    let item = RecentWorkspace(address: address, hostName: Host.current().localizedName, lastUsed: .now, source: .iCloud)
                    if WorkspaceKeychain.publish(workspace: item, token: token) {
                        publishedWorkspaceOrigin = address
                        syncStatus = "Available to your devices through iCloud Keychain."
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

    /// Attach again after the owned service was lost, with bounded backoff; only when every attempt failed does the
    /// connection screen open, where Start Local Workspace tries again.
    private func reattachLocal() async {
        // The person choosing another workspace (or none) during the backoff ends it: that choice wins.
        let back = await LocalService.retrying(cancelled: { endpoint?.origin.scheme != "http" }) {
            await startLocal()
        }
        guard !back, endpoint?.origin.scheme == "http" else { return }
        endpoint = nil
        error = ConnectionError.stopped.errorDescription
        showConnection = true
    }

    /// True when attached, false when attaching failed, nil when another connection attempt is running (busy).
    @discardableResult
    private func startLocal(initialStartup: Bool = false) async -> Bool? {
        guard !connecting else { return nil }
        let menuRevision = initialStartup ? 0 : connectionMenuIntent.revision
        connecting = true
        error = nil
        defer { connecting = false }
        service.onLost = {
            // The owned service restarted with a new session: attach again rather than show a dead workspace.
            guard endpoint?.origin.scheme == "http" else { return }
            Task { await reattachLocal() }
        }
        do {
            let target = try await service.start()
            if let snapshot = ActivityWidgetData.read(), snapshot.workspaceScope != ActivityWidgetData.scope(for: target.origin) {
                clearWidgetActivity()
                chatRequest = nil
            }
            choosingWorkspace = connectionMenuIntent.keepSetup(afterStartedAt: menuRevision)
            loadFailure = nil
            connectionIssue = nil
            loadingWorkspace = true
            endpoint = target
            return true
        } catch {
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
    @Environment(\.dismiss) private var dismiss
    @FocusState private var editing: Bool
    @State private var confirmingDiscard = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                connectionSheetHeader("Write a Prompt") {
                    connectionIconButton("xmark", label: "Close") { dismiss() }
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
                                .frame(minHeight: 160, maxHeight: 260)
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
        .frame(idealWidth: 520, minHeight: 460, idealHeight: 630)
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

private struct ConnectionHelp: View {
    let origin: URL?
    let activeWorkspace: Endpoint?
    let needsSignIn: Bool
    let workspaceIsCurrent: (Endpoint) -> Bool
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    #if os(iOS)
    @Environment(\.openURL) private var openURL
    #endif
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
        case internet = "Internet", workspace = "Workspace", microphone = "Microphone"
        case systemAudio = "System Audio", notifications = "Notifications"
    }

    private enum Status {
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

    private struct Result {
        let status: Status
        let detail: String

        init(_ status: Status, _ detail: String) {
            self.status = status
            self.detail = detail
        }
    }

    private var microphone: Result {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: Result(.passed, "Access allowed")
        case .denied: Result(.off, "Access is off in Settings")
        case .restricted: Result(.unavailable, "Restricted on this device")
        case .notDetermined: Result(.optional, "Allow access to use the microphone visualizer")
        @unknown default: Result(.failed, "Microphone permission could not be read")
        }
    }

    private var canTestSystemAudio: Bool {
        #if os(macOS)
        activeWorkspace?.origin.scheme == "http"
        #elseif canImport(ScreenCaptureKit)
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
                diagnosticGroup([.internet, .workspace])
                #if os(iOS)
                DisclosureGroup(isExpanded: $showingDeviceFeatures) {
                    diagnosticGroup([.microphone, .systemAudio, .notifications])
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
                    diagnosticGroup([.microphone, .systemAudio, .notifications])
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
            ConnectionFooter(showTagline: true).background(Color("StartupBackground"))
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
        if !checking, let workspace = results[.workspace] {
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
        .background(Color("StartupBackground"))
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
        let notifications = await notificationStatus()
        guard !checking && !testingAudio && !askingPermission else { return }
        results[.microphone] = microphone
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
            if !checking && !testingAudio && !askingPermission && result.status != .pending {
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
                permissionSettings
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
        results = [:]
        defer { checking = false }
        let session = Endpoint.probeSession()
        defer { session.invalidateAndCancel() }
        async let publicCheck = internetStatus(using: session)
        async let privateCheck = workspaceStatus(using: session)
        async let alertCheck = notificationStatus()

        for check in Check.allCases {
            results[check] = Result(.checking, "Checking…")
            let result: Result
            switch check {
            case .internet: result = await publicCheck
            case .workspace: result = await privateCheck
            case .microphone: result = microphone
            case .systemAudio: result = systemAudio
            case .notifications: result = await alertCheck
            }
            // Keep quick local checks visible long enough for the change in status to register.
            do { try await Task.sleep(for: .milliseconds(220)) } catch { return }
            results[check] = result
        }
        if let status = results[.workspace] {
            AccessibilityNotification.Announcement(status.detail).post()
        }
    }

    private func requestMicrophone() async {
        askingPermission = true
        defer { askingPermission = false }
        _ = await AVCaptureDevice.requestAccess(for: .audio)
        results[.microphone] = microphone
    }

    private func notificationStatus() async -> Result {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        switch settings.authorizationStatus {
        case .denied:
            return Result(.off, "Access is off in Settings")
        case .notDetermined, .provisional:
            return Result(.optional, "Allow alerts for workspace updates")
        case .authorized, .ephemeral:
            guard settings.alertSetting == .enabled || settings.notificationCenterSetting == .enabled else {
                return Result(.off, "Turn on alerts in Settings")
            }
            if let activeWorkspace {
                let state = await WorkspaceNotifications.shared.status(for: activeWorkspace)
                if state == "off" { return Result(.optional, "Turn on alerts for this workspace") }
            }
            return Result(.passed, activeWorkspace == nil ? "App alerts allowed. Open a workspace to enable its alerts." : "Workspace alerts on")
        @unknown default:
            return Result(.failed, "Notification permission could not be read")
        }
    }

    private func requestNotifications() async {
        askingPermission = true
        defer { askingPermission = false }
        if let activeWorkspace {
            _ = await WorkspaceNotifications.shared.setEnabled(
                true, for: activeWorkspace, workspaceIsCurrent: { workspaceIsCurrent(activeWorkspace) }, confirm: { true }
            )
        } else {
            _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert])
        }
        results[.notifications] = await notificationStatus()
    }

    private func testSystemAudio() async {
        #if os(macOS) || canImport(ScreenCaptureKit)
        guard canTestSystemAudio else { return }
        testingAudio = true
        #if os(macOS)
        results[.systemAudio] = Result(.checking, "Checking audio access…")
        #else
        results[.systemAudio] = Result(.checking, "Opening the system sharing picker…")
        #endif
        defer { testingAudio = false }
        if #available(iOS 27, *) {
            let capture = SystemAudio()
            #if os(macOS)
            guard let window = NSApp.keyWindow, await capture.confirm(in: window), !Task.isCancelled else {
                results[.systemAudio] = systemAudio
                return
            }
            #endif
            let started = await capture.start()
            #if os(macOS)
            var stopped = false
            if started {
                for _ in 0..<20 {
                    if capture.read() == nil { stopped = true; break }
                    if capture.hasReceivedSamples || Task.isCancelled { break }
                    try? await Task.sleep(for: .milliseconds(100))
                }
            }
            let received = capture.hasReceivedSamples
            capture.stop()
            guard !Task.isCancelled else { return }
            if !started || stopped {
                results[.systemAudio] = Result(.optional, "Sharing didn’t start or stopped. Try again.")
            } else if received {
                results[.systemAudio] = Result(.passed, "Audio samples received")
            } else {
                results[.systemAudio] = Result(.optional, "Sharing started, but no audio samples arrived. Try playing sound and test again.")
            }
            #else
            if started {
                for _ in 0..<20 {
                    if capture.read() == nil || capture.hasReceivedSamples || Task.isCancelled { break }
                    try? await Task.sleep(for: .milliseconds(100))
                }
            }
            let received = capture.hasReceivedSamples
            capture.stop()
            guard !Task.isCancelled else { return }
            if !started {
                results[.systemAudio] = Result(.optional, "Sharing didn’t start. Try again and choose a source if asked.")
            } else if received {
                results[.systemAudio] = Result(.passed, "Audio samples received")
            } else {
                results[.systemAudio] = Result(.optional, "Sharing started, but no audio samples arrived. Try playing sound and test again.")
            }
            #endif
        }
        #endif
    }

    private func internetStatus(using session: URLSession) async -> Result {
        guard let url = URL(string: "https://heinzel.team/") else { return Result(.failed, "Check unavailable") }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 4)
        request.httpMethod = "HEAD"
        do {
            let (_, response) = try await session.data(for: request)
            if let response = response as? HTTPURLResponse, (200..<400).contains(response.statusCode) {
                return Result(.passed, "Public website reachable")
            }
            return Result(.failed, "Public website did not answer as expected")
        } catch let error as URLError where error.code == .notConnectedToInternet {
            return Result(.failed, "This device is offline")
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
