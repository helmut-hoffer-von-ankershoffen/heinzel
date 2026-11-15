import AVFoundation
import SwiftUI
import UserNotifications

private var heinzelAppVersion: String {
    let bundle = Bundle.main
    let version = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
    let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown"
    return "Version \(version) · Build \(build)"
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
    @State private var link = ""
    @State private var endpoint: Endpoint?
    @State private var connecting = false
    @State private var stoppingLocalWorkspace = false
    @State private var loadingWorkspace = false
    @State private var workspaceBackground = Color("StartupBackground")
    @State private var error: String?
    @State private var showConnection = false
    @State private var choosingWorkspace = false
    @State private var showConnectionHelp = false
    @State private var helpAfterConnection = false
    @State private var sharedWorkspace: Endpoint?
    @State private var loadFailure: WorkspaceProblem?
    @State private var loadRevision = 0
    @State private var connectionIssue: WorkspaceProblem?
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
        guard choosingWorkspace || endpoint == nil else { return nil }
        let input = link.trimmingCharacters(in: .whitespacesAndNewlines)
        let target = Endpoint(input) ?? URL(string: input).flatMap(Endpoint.sharedWorkspace)
        guard let origin = target?.origin, let host = origin.host(),
              host == "localhost" || host.contains(".") || host.contains(":") else { return nil }
        return origin
    }

    private var visibleProblem: WorkspaceProblem? {
        if let loadFailure {
            return loadFailure.canReconnectAutomatically ? connectionIssue ?? loadFailure : loadFailure
        }
        return loadingWorkspace ? connectionIssue : nil
    }

    private var monitoredOrigin: URL? {
        guard scenePhase == .active, endpoint?.origin.scheme == "https" else { return nil }
        return endpoint?.origin
    }

    var body: some View {
        content
        .accessibilityHidden(choosingWorkspace)
        .allowsHitTesting(!choosingWorkspace)
        .overlay {
            if choosingWorkspace { setup }
        }
        .sheet(isPresented: $showConnectionHelp) {
            ConnectionHelp(
                origin: draftOrigin ?? endpoint?.origin ?? Endpoint(serverOrigin)?.origin,
                activeWorkspace: draftOrigin == nil && loadFailure == nil && !loadingWorkspace ? endpoint : nil,
                workspaceIsCurrent: { target in endpoint == target && loadFailure == nil && !loadingWorkspace }
            )
                .presentationBackground(Color("StartupBackground"))
        }
        .task {
            guard !started else { return }
            started = true
            // A short cold-start beat keeps the branded screen perceptible even when the host responds immediately.
            let earliest = ContinuousClock.now + .milliseconds(650)
            await openInitialWorkspace()
            try? await ContinuousClock().sleep(until: earliest)
            showingColdStart = false
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active && draftOrigin != nil { draftRevision += 1 }
        }
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
            guard let origin = monitoredOrigin else { return }
            await watchConnection(to: origin)
        }
        .onOpenURL { url in
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
        .alert("Open Heinzel workspace?", isPresented: Binding(
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
            await connect(link, remember: false)
            return
        }
        #endif
        #if os(macOS)
        startupStage = "Starting the local workspace…"
        await startLocal()
        #else
        if !serverOrigin.isEmpty && sharedWorkspace == nil {
            startupStage = "Checking workspace connection…"
            await connect(serverOrigin)
        } else {
            startupStage = "Looking for a saved workspace…"
        }
        #endif
    }

    @ViewBuilder
    private var content: some View {
        if let endpoint, loadFailure == .signIn {
            if showingColdStart { startup } else { recovery(.signIn, for: endpoint) }
        } else if let endpoint {
            let revision = loadRevision
            let obscured = choosingWorkspace || showingColdStart || loadingWorkspace || visibleProblem != nil
            WorkspaceView(endpoint: endpoint, isObscured: obscured, ready: {
                guard self.endpoint == endpoint, loadRevision == revision else { return }
                loadingWorkspace = false
                loadFailure = nil
            }, reportError: { message in
                guard self.endpoint == endpoint, loadRevision == revision else { return }
                loadingWorkspace = false
                loadFailure = message
            }, appearance: { color in
                guard self.endpoint == endpoint, loadRevision == revision else { return }
                workspaceBackground = color
            })
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
                if let connectionIssue, !loadingWorkspace, loadFailure == nil {
                    Button {
                        showConnectionHelp = true
                    } label: {
                        Label(connectionIssue.title, systemImage: connectionIssue.icon)
                            .font(.footnote.weight(.medium))
                            .padding(.horizontal, 12)
                            .frame(minHeight: 44)
                            .background(.regularMaterial, in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens Connection Help")
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
            #else
            .overlay(alignment: .bottomTrailing) {
                if !loadingWorkspace && visibleProblem == nil {
                    Button {
                        showConnection = true
                    } label: {
                        Label("Connection", systemImage: "point.3.connected.trianglepath.dotted")
                            .labelStyle(.iconOnly)
                            .frame(width: 44, height: 44)
                            .foregroundStyle(.primary)
                            .background(.regularMaterial, in: Circle())
                            .contentShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .padding(.bottom, 48)
                    .padding(.trailing, 12)
                }
            }
            #endif
            .sheet(isPresented: $showConnection, onDismiss: {
                if helpAfterConnection {
                    helpAfterConnection = false
                    showConnectionHelp = true
                }
            }) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        Text("Workspace Connection")
                            .font(.system(.title2, design: .serif, weight: .semibold))
                            .accessibilityAddTraits(.isHeader)
                        Text(endpoint.origin.absoluteString)
                            .font(.callout.monospaced())
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                        if let shareURL = endpoint.shareURL {
                            ShareLink(item: shareURL) { Label("Share Workspace Link", systemImage: "square.and.arrow.up") }
                        }
                        if let error {
                            Label(error, systemImage: "info.circle")
                                .font(.callout)
                                .padding(12)
                                .background(.white.opacity(0.55), in: RoundedRectangle(cornerRadius: 12))
                        }
                        ViewThatFits(in: .horizontal) {
                            HStack { connectionActions }
                            VStack(alignment: .leading) { connectionActions }
                        }
                        Button("Connection Help", systemImage: "questionmark.circle") {
                            helpAfterConnection = true
                            showConnection = false
                        }
                        .buttonStyle(.borderless)
                    }
                    .frame(maxWidth: 420, alignment: .leading)
                    .padding(24)
                    .frame(maxWidth: .infinity, alignment: .center)
                }
                .defaultScrollAnchor(.center, for: .alignment)
                .environment(\.colorScheme, .light)
                .tint(Color(red: 0.188, green: 0.282, blue: 0.227))
                .presentationBackground(Color("StartupBackground"))
                .presentationDetents([.medium, .large])
            }
        } else if connecting || showingColdStart {
            startup
        } else if let target = sharedWorkspace {
            launchCard {
                Text("Open this workspace?").font(.headline)
                Text(target.origin.absoluteString)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .textSelection(.enabled)
                Button("Open Workspace") {
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

    @ViewBuilder
    private var connectionActions: some View {
        Button("Back to Workspace") { showConnection = false }
            .buttonStyle(.borderedProminent)
        Button("Switch Workspace", action: chooseAnotherWorkspace)
            .buttonStyle(.bordered)
        Button("Disconnect", role: .destructive, action: disconnect)
            .buttonStyle(.bordered)
    }

    private func recovery(_ problem: WorkspaceProblem, for endpoint: Endpoint) -> some View {
        launchCard {
            Label(problem.title, systemImage: problem.icon)
                .font(.system(.title3, design: .serif, weight: .semibold))
                .foregroundStyle(Color(red: 0.149, green: 0.231, blue: 0.196))
                .accessibilityAddTraits(.isHeader)
            Text(problem.detail)
                .font(.callout)
                .foregroundStyle(Color(red: 0.27, green: 0.33, blue: 0.29))
            if endpoint.origin.scheme == "https" {
                Text(endpoint.origin.host() ?? endpoint.origin.absoluteString)
                    .font(.footnote.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(.white.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            }
            if problem == .signIn {
                signInActions(for: endpoint)
            } else if problem.shouldEnterLink {
                Button("Enter Workspace Link", action: chooseAnotherWorkspace)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
            } else {
                Button("Try Again") { retryWorkspace() }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                Button("Use Another Workspace", action: chooseAnotherWorkspace)
                    .buttonStyle(.bordered)
                    .controlSize(.large)
            }
            if !choosingWorkspace { helpButton }
        }
        .multilineTextAlignment(.center)
    }

    private func signInActions(for endpoint: Endpoint) -> some View {
        VStack(spacing: 12) {
            linkField("Private Sign-in Link") { signIn(link, at: endpoint) }
            if let error {
                Label(error, systemImage: "info.circle")
                    .font(.callout)
                    .foregroundStyle(Color(red: 0.36, green: 0.22, blue: 0.17))
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button(connecting ? "Signing In…" : "Sign In") { signIn(link, at: endpoint) }
                    .buttonStyle(.borderedProminent)
                    .disabled(connecting || link.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                #if os(iOS)
                PasteButton(payloadType: String.self) { values in
                    if let value = values.first { signIn(value, at: endpoint) }
                }
                .disabled(connecting)
                #endif
            }
            .controlSize(.large)
            Button("Use Another Workspace", action: chooseAnotherWorkspace)
                .buttonStyle(.borderless)
                .disabled(connecting)
        }
        .frame(maxWidth: 320)
    }

    private func signIn(_ value: String, at endpoint: Endpoint) {
        guard !connecting else { return }
        guard let target = Endpoint(value), target.token != nil, endpoint.owns(target.origin) else {
            error = "Use the private sign-in link for this workspace."
            return
        }
        Task { await connect(value) }
    }

    private func linkField(_ title: String, submit: @escaping () -> Void) -> some View {
        SecureField(title, text: $link)
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            #if os(iOS)
            .textInputAutocapitalization(.never)
            .keyboardType(.URL)
            .submitLabel(.go)
            #endif
            .focused($editingLink)
            .accessibilityIdentifier("serverLink")
            .onSubmit(submit)
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
            if startupIsSlow && !stoppingLocalWorkspace {
                Text("Taking longer than usual. You can check the connection while you wait.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Button("Connection Help", systemImage: "questionmark.circle") { showConnectionHelp = true }
                    .buttonStyle(.bordered)
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { versionFooter }
        .task(id: stoppingLocalWorkspace) {
            startupIsSlow = false
            guard !stoppingLocalWorkspace else { return }
            do { try await Task.sleep(for: .seconds(4)) } catch { return }
            startupIsSlow = true
        }
    }

    private var startupText: String {
        if stoppingLocalWorkspace { return "Stopping workspace…" }
        if showingColdStart { return startupStage }
        return "Opening workspace…"
    }

    private var versionFooter: some View {
        Text(heinzelAppVersion)
            .font(.caption2)
            .foregroundStyle(Color(red: 0.38, green: 0.40, blue: 0.38))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
    }

    private func launchCard(loading: Bool = false, @ViewBuilder actions: () -> some View) -> some View {
        ScrollView {
            VStack(spacing: 14) {
                brandHeader(loading: loading)
                Capsule()
                    .fill(Color(red: 0.68, green: 0.33, blue: 0.22).opacity(0.5))
                    .frame(width: 40, height: 1)
                    .accessibilityHidden(true)
                    .padding(.vertical, 4)
                actions()
            }
            .frame(maxWidth: 420)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .defaultScrollAnchor(.center, for: .alignment)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .environment(\.colorScheme, .light)
        .tint(Color(red: 0.188, green: 0.282, blue: 0.227))
        .background { paperBackground }
    }

    private func brandHeader(loading: Bool = false, compact: Bool = false) -> some View {
        VStack(spacing: 6) {
            Image(loading ? "StartupIcon" : "HeinzelCrew")
                .resizable()
                .scaledToFit()
                .frame(width: compact ? 76 : (loading ? 104 : 158), height: compact ? 48 : (loading ? 104 : 118))
                .accessibilityHidden(true)
            Text("Heinzelmännchen")
                .font(.system(.largeTitle, design: .serif, weight: .semibold))
                .minimumScaleFactor(0.7)
                .lineLimit(1)
                .accessibilityAddTraits(.isHeader)
                .foregroundStyle(Color(red: 0.149, green: 0.231, blue: 0.196))
            if !loading && !compact {
                Text("Durable agents accelerating product teams, like the little elves from Cologne.")
                    .font(.callout)
                    .foregroundStyle(Color(red: 0.27, green: 0.33, blue: 0.29))
                    .multilineTextAlignment(.center)
            }
        }
    }

    private var helpButton: some View {
        Button("Connection Help", systemImage: "questionmark.circle") { showConnectionHelp = true }
            .buttonStyle(.borderless)
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
                Text("Connect to a workspace").font(.headline)
                Text("Open a shared Heinzel link, or paste a private sign-in link from your workspace host.")
                    .foregroundStyle(.secondary)
                linkField("Workspace or sign-in link") { Task { await connect(link) } }
                if let origin = draftOrigin {
                    Text(origin.absoluteString)
                        .font(.footnote.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .accessibilityLabel("Workspace address: \(origin.absoluteString)")
                    if checkedDraft == origin {
                        Label(draftIssue?.title ?? "Workspace reachable", systemImage: draftIssue?.icon ?? "checkmark.circle.fill")
                            .font(.callout.weight(.medium))
                            .foregroundStyle(draftIssue == nil ? Color(red: 0.19, green: 0.37, blue: 0.27) : .secondary)
                            .accessibilityIdentifier("workspaceReachability")
                        if draftIssue != nil {
                            Button("Check Again") { draftRevision += 1 }.buttonStyle(.bordered)
                        }
                    } else {
                        ProgressView("Checking workspace…")
                            .controlSize(.small)
                            .font(.callout)
                    }
                }
                ViewThatFits(in: .horizontal) {
                    HStack { setupActions }
                    VStack(alignment: .leading) { setupActions }
                }
                if !choosingWorkspace, let saved = Endpoint(serverOrigin) {
                    Button("Reconnect to \(workspaceName(saved.origin))") { Task { await connect(serverOrigin) } }
                        .buttonStyle(.bordered)
                }
                helpButton
                if connecting { ProgressView("Connecting…") }
                if let error {
                    Label(error, systemImage: "info.circle")
                        .font(.callout)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.white.opacity(0.55), in: RoundedRectangle(cornerRadius: 12))
                }
            }
            .disabled(connecting)
            .frame(maxWidth: 450, alignment: .leading)
            .padding(28)
            .frame(maxWidth: .infinity)
        }
        #if os(iOS)
        .scrollDismissesKeyboard(.interactively)
        #endif
        .defaultScrollAnchor(.center, for: .alignment)
        .frame(maxHeight: .infinity)
        .environment(\.colorScheme, .light)
        .tint(Color(red: 0.188, green: 0.282, blue: 0.227))
        .background { paperBackground }
        .safeAreaInset(edge: .bottom, spacing: 0) { versionFooter }
    }

    @ViewBuilder
    private var setupActions: some View {
        Button("Connect") { Task { await connect(link) } }
            .buttonStyle(.borderedProminent)
            .disabled(link.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        #if os(iOS)
        PasteButton(payloadType: String.self) { values in
            guard let value = values.first else { return }
            link = value
            error = nil
        }
        #endif
    }

    private func connect(_ input: String, remember: Bool = true) async {
        guard !connecting else { return }
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let target = Endpoint(value) ?? URL(string: value).flatMap(Endpoint.sharedWorkspace) else {
            error = "Enter a workspace link or private HTTPS address."
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
        do {
            try await target.verify()
            if remember { serverOrigin = target.origin.absoluteString }
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
        link = ""
        loadFailure = nil
        connectionIssue = nil
        error = nil
    }

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
            if recovered && (loadFailure?.canReconnectAutomatically == true || loadingWorkspace) { retryWorkspace() }
            let delay = issue == nil ? 12 : retryDelay
            retryDelay = issue == nil ? 2 : min(retryDelay * 2, 12)
            do { try await Task.sleep(for: .seconds(delay)) } catch { return }
        }
    }

    #if os(macOS)
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
    let workspaceIsCurrent: (Endpoint) -> Bool
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    #if os(iOS)
    @Environment(\.openURL) private var openURL
    #endif
    @State private var run = 0
    @State private var audioRun = 0
    @State private var checking = false
    @State private var testingAudio = false
    @State private var askingPermission = false
    @State private var results: [Check: Result] = [:]

    private enum Check: String, CaseIterable {
        case internet = "Internet", workspace = "Workspace", microphone = "Microphone"
        case systemAudio = "System Audio", notifications = "Notifications"
    }

    private enum Status {
        case pending, checking, passed, action, failed

        var symbol: String {
            switch self {
            case .pending: "circle.dotted"
            case .checking: "circle"
            case .passed: "checkmark.circle.fill"
            case .action: "exclamationmark.circle.fill"
            case .failed: "xmark.circle.fill"
            }
        }

        var label: String {
            switch self {
            case .pending: "Waiting"
            case .checking: "Checking"
            case .passed: "Passed"
            case .action: "Action available"
            case .failed: "Unavailable"
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
        case .authorized: Result(.passed, "Microphone access allowed")
        case .denied: Result(.failed, "Microphone access is off in Settings")
        case .restricted: Result(.failed, "Restricted on this device")
        case .notDetermined: Result(.action, "Allow access to use the microphone visualizer")
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

    private var systemAudio: Result {
        if canTestSystemAudio {
            #if os(macOS)
            return Result(.action, "Check access to audio from other apps")
            #else
            return Result(.action, "Choose an app or window in the system picker")
            #endif
        }
        #if os(macOS)
        return Result(.action, "Open a local Mac workspace to test sharing")
        #elseif targetEnvironment(simulator)
        return Result(.failed, "System audio needs an iPhone or iPad running iOS or iPadOS 27 or later")
        #else
        if #unavailable(iOS 27) {
            return Result(.failed, "Requires iOS or iPadOS 27 or later")
        }
        return Result(.failed, "The system sharing picker is unavailable on this device")
        #endif
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Check.allCases, id: \.self) { check in
                        if check != .internet { Divider().padding(.vertical, 12) }
                        diagnostic(check)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
                .background(.white.opacity(0.65), in: RoundedRectangle(cornerRadius: 16))

                DisclosureGroup("Which link should I use?") {
                    Text("Open the workspace link to choose a workspace. On a new device, paste the private sign-in link from your host. Keep that link private.")
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
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .safeAreaInset(edge: .top, spacing: 0) { diagnosticsHeader }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active && run > 0 && !checking && !testingAudio && !askingPermission {
                Task { await refreshPermissions() }
            }
        }
        .task(id: run) {
            if run > 0 { await runDiagnostics() }
        }
        .task(id: audioRun) {
            if audioRun > 0 { await testSystemAudio() }
        }
        .frame(idealWidth: 500, idealHeight: 610)
        .environment(\.colorScheme, .light)
        .tint(Color(red: 0.188, green: 0.282, blue: 0.227))
    }

    private var diagnosticsHeader: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Connection Help")
                        .font(.system(.title, design: .serif, weight: .semibold))
                        .accessibilityAddTraits(.isHeader)
                    Text(heinzelAppVersion).font(.footnote).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Close") { dismiss() }.buttonStyle(.bordered)
            }
            Button(checking ? "Checking…" : "Run Diagnostics", systemImage: "stethoscope") {
                checking = true
                results = [:]
                run += 1
            }
            .buttonStyle(.borderedProminent)
            .disabled(checking || testingAudio || askingPermission)
            if run > 0 {
                let finished = results.values.filter { $0.status != .checking }.count
                Text(checking ? "\(finished) of \(Check.allCases.count) checks complete" : "Diagnostics complete")
                    .font(.callout.weight(.medium))
                    .contentTransition(.numericText())
                    .accessibilityIdentifier("diagnosticsProgress")
            }
        }
        .frame(maxWidth: 460, alignment: .leading)
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .frame(maxWidth: .infinity)
        .background(Color("StartupBackground"))
    }

    private func refreshPermissions() async {
        let notifications = await notificationStatus()
        guard !checking && !testingAudio && !askingPermission else { return }
        results[.microphone] = microphone
        results[.notifications] = notifications
    }

    private func diagnostic(_ check: Check) -> some View {
        let result = results[check] ?? Result(.pending, checking ? "Waiting to check" : "Ready to check")
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(check.rawValue).font(.callout.weight(.semibold))
                    Text(result.detail).font(.footnote)
                        .foregroundStyle(Color(red: 0.27, green: 0.33, blue: 0.29))
                }
                Spacer(minLength: 0)
                if result.status == .checking {
                    ProgressView().controlSize(.small).accessibilityLabel("Checking")
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
            if results[.notifications]?.status == .action {
                Button("Enable Notifications") { Task { await requestNotifications() } }.buttonStyle(.bordered)
            } else if results[.notifications]?.status == .failed {
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

        for check in Check.allCases {
            results[check] = Result(.checking, "Checking…")
            let result: Result
            switch check {
            case .internet: result = await publicCheck
            case .workspace: result = await privateCheck
            case .microphone: result = microphone
            case .systemAudio: result = systemAudio
            case .notifications: result = await notificationStatus()
            }
            // Keep quick local checks visible long enough for the change in status to register.
            do { try await Task.sleep(for: .milliseconds(220)) } catch { return }
            results[check] = result
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
            return Result(.failed, "Notification access is off in Settings")
        case .notDetermined, .provisional:
            return Result(.action, "Allow alerts for workspace updates")
        case .authorized, .ephemeral:
            guard settings.alertSetting == .enabled || settings.notificationCenterSetting == .enabled else {
                return Result(.failed, "Turn on alerts in Settings")
            }
            if let activeWorkspace {
                let state = await WorkspaceNotifications.shared.status(for: activeWorkspace)
                if state == "off" { return Result(.action, "Turn on alerts for this workspace") }
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
                : Result(.action, "Sharing didn’t start. Try again and choose a source if asked.")
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
        return Result(.passed, "\(origin.host() ?? "Workspace") is reachable")
    }
}
