import AVFoundation
import SwiftUI
import UserNotifications
#if os(iOS)
import WidgetKit
#endif

private let heinzelTagline = "Durable agents accelerating product teams, like the little elves from Cologne."

private var heinzelAppVersion: String {
    let bundle = Bundle.main
    let version = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
    let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown"
    return "Version \(version) · Build \(build)"
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
enum WorkspaceMenuAction: String {
    case about, settings, updates, search, sidebar, terminal, browser, chat, notifications
    case newChat = "new-chat"
    case today = "go:today", heinzel = "go:heinzel", team = "go:team", plugins = "go:plugins"
    case help, changelog, doctor, copy, daemon
    case reload, zoomIn, zoomOut, resetZoom

    static let notification = Notification.Name("heinzelWorkspaceMenuAction")
}

private struct WorkspaceCommands: Commands {
    private let website = "https://heinzel.team"
    private let repository = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"

    var body: some Commands {
        CommandGroup(replacing: .appInfo) {
            command("About Heinzelmännchen", .about)
        }
        CommandGroup(replacing: .appSettings) {
            command("Settings…", .settings).keyboardShortcut(",")
            command("Check for Updates…", .updates)
            Button("Connection…") {
                NotificationCenter.default.post(name: .showHeinzelConnection, object: nil)
            }
            .keyboardShortcut(",", modifiers: [.command, .shift])
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
    @State private var syncedWorkspaces: [RecentWorkspace] = []
    #if os(macOS)
    @AppStorage("syncWorkspaceSignIn") private var syncWorkspaceSignIn = false
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
    @State private var showConnection = false
    @State private var connectionSheetHeight: CGFloat = 390
    @State private var showDisconnectConfirmation = false
    @State private var choosingWorkspace = false
    @State private var showConnectionHelp = false
    @State private var helpAfterConnection = false
    #if os(iOS)
    @State private var shortcutRouter = AppShortcutRouter.shared
    @State private var pendingShortcutURL: NativeRoute?
    @State private var showHeinzelPicker = false
    @State private var pickerRoster: [WidgetHeinzel] = []
    @State private var pickerHasSnapshot = false
    @State private var pickerTimedOut = false
    @State private var widgetRefreshRevision = 0
    @State private var chatRequest: HeinzelChatRequest?
    #endif
    @State private var sharedWorkspace: Endpoint?
    @State private var loadFailure: WorkspaceProblem?
    @State private var loadRevision = 0
    @State private var connectionIssue: WorkspaceProblem?
    @State private var verifiedConnectionOrigin: URL?
    @FocusState private var editingLink: Bool
    @State private var draftRevision = 0
    @State private var checkedDraft: URL?
    @State private var draftIssue: WorkspaceProblem?

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

    private var recentWorkspaces: [RecentWorkspace] {
        let local = (try? JSONDecoder().decode([RecentWorkspace].self, from: workspaceHistory)) ?? []
        let forgotten = (try? JSONDecoder().decode([String: Date].self, from: forgottenWorkspaces)) ?? [:]
        let cloud = syncedWorkspaces.filter { $0.lastUsed > (forgotten[$0.address] ?? .distantPast) }
        var entries: [String: RecentWorkspace] = [:]
        for item in local + cloud {
            guard let target = Endpoint(item.address), target.token == nil, target.origin.absoluteString == item.address else { continue }
            let origin = item.address
            if let known = entries[origin] {
                entries[origin] = RecentWorkspace(address: origin, hostName: item.hostName ?? known.hostName,
                                                  lastUsed: known.lastUsed, source: known.source ?? item.source)
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
        var local = (try? JSONDecoder().decode([RecentWorkspace].self, from: workspaceHistory)) ?? []
        local.removeAll { $0.address == item.address }
        if let encoded = try? JSONEncoder().encode(local) { workspaceHistory = encoded }
        var forgotten = (try? JSONDecoder().decode([String: Date].self, from: forgottenWorkspaces)) ?? [:]
        let latestSync = syncedWorkspaces.first { $0.address == item.address }?.lastUsed ?? .distantPast
        forgotten[item.address] = max(.now, latestSync)
        if let encoded = try? JSONEncoder().encode(forgotten) { forgottenWorkspaces = encoded }
        if serverOrigin == item.address { serverOrigin = "" }
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

    #if os(iOS)
    private var canOpenShortcut: Bool {
        scenePhase == .active && !showingColdStart && !stoppingLocalWorkspace &&
            !showDisconnectConfirmation && (sharedWorkspace == nil || endpoint == nil)
    }

    private func openShortcut(_ shortcut: NativeRoute) {
        editingLink = false
        switch shortcut {
        case .workspace:
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
        case .chat(let scope, let id):
            if let snapshot = currentWidgetSnapshot, snapshot.workspaceScope == scope,
               snapshot.roster.contains(where: { $0.id == id }) {
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

    private var currentWidgetSnapshot: ActivityWidgetData? {
        guard let endpoint, endpoint.origin.absoluteString == serverOrigin,
              let snapshot = ActivityWidgetData.read(),
              snapshot.workspaceScope == ActivityWidgetData.scope(for: serverOrigin) else { return nil }
        return snapshot
    }

    private var heinzelPicker: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                connectionSheetHeader("Choose a Heinzel") {
                    connectionIconButton("xmark", label: "Close") { showHeinzelPicker = false }
                }
                if endpoint == nil || loadFailure != nil || endpoint?.origin.absoluteString != serverOrigin {
                    Text("Open your current workspace to load its Heinzels.")
                        .font(.callout).foregroundStyle(ConnectionPalette.muted)
                    Button("Open Workspace") {
                        showHeinzelPicker = false
                        if !serverOrigin.isEmpty, endpoint?.origin.absoluteString != serverOrigin {
                            Task { await connectRecent(serverOrigin) }
                        }
                    }
                    .buttonStyle(.borderedProminent)
                } else if pickerRoster.isEmpty {
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
                        ForEach(pickerRoster) { item in
                            Button {
                                if let snapshot = currentWidgetSnapshot, let scope = snapshot.workspaceScope,
                                   snapshot.roster.contains(where: { $0.id == item.id }) {
                                    chatRequest = HeinzelChatRequest(id: item.id, workspaceScope: scope)
                                    showHeinzelPicker = false
                                }
                            } label: {
                                HStack(spacing: 10) {
                                    if let data = item.portrait, let image = UIImage(data: data) {
                                        Image(uiImage: image).resizable().scaledToFit()
                                            .frame(width: 35, height: 35).accessibilityHidden(true)
                                    }
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(item.name).fontWeight(.medium)
                                        Text(item.status).font(.caption).foregroundStyle(ConnectionPalette.muted)
                                    }
                                    Spacer(minLength: 4)
                                    Image(systemName: "chevron.forward").accessibilityHidden(true)
                                }
                                .frame(minHeight: 48).contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .connectionPanel()
                }
                ConnectionFooter()
            }
            .frame(maxWidth: 420, alignment: .leading)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
        .presentationBackground(Color("StartupBackground"))
        .task(id: widgetRefreshRevision) {
            pickerTimedOut = false
            let start = ContinuousClock.now
            while !Task.isCancelled {
                let snapshot = currentWidgetSnapshot
                pickerRoster = snapshot?.roster ?? []
                pickerHasSnapshot = snapshot != nil
                pickerTimedOut = ContinuousClock.now >= start + .seconds(8)
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
    }
    #endif

    var body: some View {
        content
        .accessibilityHidden(choosingWorkspace)
        .allowsHitTesting(!choosingWorkspace)
        .overlay {
            if choosingWorkspace { setup }
        }
        .sheet(isPresented: $showConnection, onDismiss: {
            if helpAfterConnection {
                helpAfterConnection = false
                showConnectionHelp = true
            }
        }) {
            if let endpoint { connectionSheet(for: endpoint) }
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
        #if os(iOS)
        .sheet(isPresented: $showHeinzelPicker) { heinzelPicker }
        #endif
        .task {
            guard !started else { return }
            started = true
            // A short cold-start beat keeps the branded screen perceptible even when the host responds immediately.
            let earliest = ContinuousClock.now + .milliseconds(650)
            await openInitialWorkspace()
            try? await ContinuousClock().sleep(until: earliest)
            showingColdStart = false
        }
        .task(id: scenePhase == .active && (endpoint == nil || choosingWorkspace)) {
            while !Task.isCancelled, scenePhase == .active, endpoint == nil || choosingWorkspace {
                syncedWorkspaces = WorkspaceKeychain.workspaces()
                do { try await Task.sleep(for: .seconds(20)) } catch { return }
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
            if phase == .active && draftOrigin != nil { draftRevision += 1 }
        }
        .onChange(of: link) { _, _ in error = nil }
        .task(id: draftProbe) {
            guard let probe = draftProbe else { return }
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
            guard let origin = monitoredOrigin else { return }
            await watchConnection(to: origin)
        }
        #if os(iOS)
        .task(id: canOpenShortcut ? pendingShortcutURL : nil) { @MainActor in
            guard !Task.isCancelled, canOpenShortcut, let shortcut = pendingShortcutURL else { return }
            pendingShortcutURL = nil
            openShortcut(shortcut)
        }
        .task(id: canOpenShortcut ? shortcutRouter.pending : nil) { @MainActor in
            guard !Task.isCancelled, canOpenShortcut, let shortcut = shortcutRouter.pending else { return }
            shortcutRouter.pending = nil
            openShortcut(shortcut)
        }
        #endif
        .onOpenURL { url in
            #if os(iOS)
            if let shortcut = NativeRoute(url: url) {
                pendingShortcutURL = shortcut
                return
            }
            #endif
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
            Button("Cancel", role: .cancel) {}
        } message: { target in
            Text(target.origin.absoluteString)
        }
    }

    private func openInitialWorkspace() async {
        #if DEBUG
        if let link = ProcessInfo.processInfo.environment["HZ_SPIKE_SERVER"] {
            startupStage = "Checking workspace connection…"
            await connect(link, remember: false, checkHost: false)
            return
        }
        #endif
        #if os(macOS)
        startupStage = "Starting the local workspace…"
        await startLocal()
        #else
        if !serverOrigin.isEmpty && sharedWorkspace == nil {
            startupStage = "Checking workspace connection…"
            await connectRecent(serverOrigin, checkHost: false)
        } else {
            startupStage = "Looking for a saved workspace…"
        }
        #endif
    }

    private var requestedWidgetRefresh: Int {
        #if os(iOS)
        widgetRefreshRevision
        #else
        0
        #endif
    }

    private var requestedChat: HeinzelChatRequest? {
        #if os(iOS)
        !loadingWorkspace && loadFailure == nil ? chatRequest : nil
        #else
        nil
        #endif
    }

    @ViewBuilder
    private var content: some View {
        if let endpoint, loadFailure == .signIn {
            if showingColdStart { startup } else { recovery(visibleProblem ?? .signIn, for: endpoint) }
        } else if let endpoint {
            let revision = loadRevision
            let obscured = choosingWorkspace || showingColdStart || loadingWorkspace || visibleProblem != nil
            VStack(spacing: 0) {
                WorkspaceView(endpoint: endpoint, isObscured: obscured,
                              reportsWidgetActivity: scenePhase == .active && !obscured && serverOrigin == endpoint.origin.absoluteString,
                              chatRequest: requestedChat, widgetRefreshRevision: requestedWidgetRefresh, ready: {
                    guard self.endpoint == endpoint, loadRevision == revision else { return }
                    loadingWorkspace = false
                    loadFailure = nil
                }, reportHostName: { name in
                    guard self.endpoint == endpoint, serverOrigin == endpoint.origin.absoluteString,
                          loadRevision == revision else { return }
                    rememberWorkspace(endpoint, hostName: name)
                }, reportError: { message in
                    guard self.endpoint == endpoint, loadRevision == revision else { return }
                    if message == .signIn && endpoint.origin.scheme == "https" {
                        Task {
                            let issue = await Endpoint.connectionIssue(at: endpoint.origin)
                            guard self.endpoint == endpoint, loadRevision == revision else { return }
                            connectionIssue = issue
                            loadFailure = issue ?? .signIn
                            #if os(iOS)
                            if issue == nil, UserDefaults.standard.string(forKey: "serverOrigin") == endpoint.origin.absoluteString {
                                clearWidgetActivity()
                            }
                            #endif
                            loadingWorkspace = false
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
                .onReceive(NotificationCenter.default.publisher(for: .showHeinzelConnection)) { _ in
                    showConnection = true
                }
                #endif
            }
            #if os(iOS)
            .overlay {
                if !obscured { workspaceSafeAreaControl }
            }
            #endif
        } else if connecting || showingColdStart {
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

    #if os(iOS)
    private var workspaceSafeAreaControl: some View {
        GeometryReader { geometry in
            let bottom = geometry.safeAreaInsets.bottom
            if bottom >= 18 {
                workspaceCircle(size: min(28, bottom - 2), hitHeight: bottom)
                    .padding(.leading, 3)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
                    .ignoresSafeArea(.container, edges: .bottom)
            } else {
                workspaceCircle(size: 32, hitHeight: 44)
                    .padding(4)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
            }
        }
    }

    private func workspaceCircle(size: CGFloat, hitHeight: CGFloat) -> some View {
        Button {
            showConnection = true
        } label: {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.system(size: size * 0.52, weight: .semibold))
                .frame(width: size, height: size)
                .background(.regularMaterial, in: Circle())
                .frame(width: 44, height: hitHeight)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Workspace")
        .accessibilityHint("Shows the address, connection status, and workspace actions")
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
                    Button(action: chooseAnotherWorkspace) { connectionRow("Switch Workspace", "arrow.left.arrow.right") }
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
                ConnectionFooter()
            }
            .frame(maxWidth: 420, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, 16)
            .frame(maxWidth: .infinity)
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height in
                connectionSheetHeight = min(520, max(300, height.rounded(.up)))
            }
        }
        #if os(macOS)
        .frame(width: 460, height: connectionSheetHeight)
        #endif
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
        .presentationBackground(Color("StartupBackground"))
        .presentationDetents([.height(connectionSheetHeight), .large])
        .alert("Disconnect from this workspace?", isPresented: $showDisconnectConfirmation) {
            Button("Cancel", role: .cancel) {}
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
                signInActions(for: endpoint)
                Button("Switch Workspace") { chooseDifferentWorkspace(from: endpoint) }
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
                Button("Switch Workspace", action: chooseAnotherWorkspace)
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
            Text(origin.absoluteString)
                .textSelection(.enabled)
                .padding(.vertical, 8)
        }
        .font(.footnote.monospaced())
        .foregroundStyle(ConnectionPalette.muted)
        .multilineTextAlignment(.leading)
        .padding(.leading, opensConnection ? 0 : 12)
        .padding(.trailing, 12)
        .background(.white.opacity(0.6), in: RoundedRectangle(cornerRadius: 8))
    }

    private func signInActions(for endpoint: Endpoint) -> some View {
        VStack(alignment: .leading, spacing: 12) {
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
            }
            .frame(maxWidth: 420)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .defaultScrollAnchor(editingLink ? .top : .center, for: .alignment)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .environment(\.colorScheme, .light)
        .tint(ConnectionPalette.accent)
        .background { paperBackground }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if !editingLink { ConnectionFooter(showTagline: !loading) }
        }
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
                .minimumScaleFactor(0.7)
                .lineLimit(1)
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
                    Button {
                        error = nil
                        link = ""
                        choosingWorkspace = false
                    } label: {
                        Label("Back to \(workspaceName(endpoint.origin))", systemImage: "chevron.backward")
                            .multilineTextAlignment(.leading)
                    }
                    .buttonStyle(.bordered)
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
            if let message = error ?? (draftWorkspace == nil && !trimmedLink.isEmpty ? "Enter the full workspace link from the host." : nil) {
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
                    connectionIconButton("arrow.clockwise", label: "Check Again") { draftRevision += 1 }
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

    private func connectRecent(_ address: String, checkHost: Bool = true, source: RecentWorkspace.Source? = nil) async {
        let forgotten = (try? JSONDecoder().decode([String: Date].self, from: forgottenWorkspaces)) ?? [:]
        let link = WorkspaceKeychain.signInLink(address: address, newerThan: forgotten[address])
        let previous = recentWorkspaces.first { $0.address == address }?.source
        await connect(link ?? address, checkHost: checkHost, fallBackToWorkspace: link != nil,
                      source: source ?? previous ?? (link == nil ? nil : .iCloud))
    }

    private func connect(_ input: String, remember: Bool = true, checkHost: Bool = true,
                         fallBackToWorkspace: Bool = false, source: RecentWorkspace.Source? = nil) async {
        guard !connecting else { return }
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
            do { try await target.verify() }
            catch ConnectionError.authentication where fallBackToWorkspace {
                guard let addressOnly = Endpoint(target.origin.absoluteString) else { throw ConnectionError.authentication }
                target = addressOnly
            }
            if remember {
                #if os(iOS)
                if serverOrigin != target.origin.absoluteString {
                    clearWidgetActivity()
                    chatRequest = nil
                }
                #endif
                serverOrigin = target.origin.absoluteString
                let provenance = source ?? .pasted
                rememberWorkspace(target, source: provenance)
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
            endpoint = target
            choosingWorkspace = false
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

    private func workspaceName(_ origin: URL) -> String {
        origin.scheme == "http" ? "Local Workspace" : origin.host() ?? origin.absoluteString
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
        endpoint = nil
        serverOrigin = ""
        #if os(iOS)
        clearWidgetActivity()
        chatRequest = nil
        #endif
        link = ""
        loadFailure = nil
        connectionIssue = nil
        error = nil
    }

    #if os(iOS)
    private func clearWidgetActivity() {
        if ActivityWidgetData.clear() { ActivityWidgetData.reload() }
    }
    #endif

    private func retryWorkspace() {
        loadFailure = nil
        loadingWorkspace = true
        loadRevision += 1
    }

    private func watchConnection(to origin: URL) async {
        let session = Endpoint.probeSession()
        defer { session.invalidateAndCancel() }
        var retryDelay = 2
        while !Task.isCancelled {
            let issue = await Endpoint.connectionIssue(at: origin, using: session)
            guard !Task.isCancelled, monitoredOrigin == origin else { return }
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

    private func startLocal() async {
        guard !connecting else { return }
        connecting = true
        error = nil
        defer { connecting = false }
        service.onExit = {
            guard endpoint?.origin.scheme == "http" else { return }
            endpoint = nil
            error = ConnectionError.stopped.errorDescription
        }
        do {
            let target = try await service.start()
            choosingWorkspace = false
            loadFailure = nil
            connectionIssue = nil
            loadingWorkspace = true
            endpoint = target
        } catch {
            self.error = (error as? ConnectionError)?.errorDescription ?? ConnectionError.startup.errorDescription
        }
    }
    #endif
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
            return Result(.optional, "Choose an app or window in the system picker")
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
                diagnosticGroup([.internet, .workspace])
                VStack(alignment: .leading, spacing: 8) {
                    Text("Device Features")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(ConnectionPalette.muted)
                        .accessibilityAddTraits(.isHeader)
                    diagnosticGroup([.microphone, .systemAudio, .notifications])
                }

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
            capture.stop()
            guard !Task.isCancelled else { return }
            results[.systemAudio] = started
                ? Result(.passed, "Sharing started and stopped")
                : Result(.optional, "Sharing didn’t start. Try again and choose a source if asked.")
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
