import SwiftUI

@main
struct HeinzelApp: App {
    #if os(macOS)
    @NSApplicationDelegateAdaptor(LocalService.self) private var service
    #endif

    var body: some Scene {
        #if os(macOS)
        Window("Heinzelmännchen", id: "workspace") {
            ConnectionView(service: service)
                .frame(minWidth: 550, minHeight: 440)
        }
        .defaultSize(width: 1100, height: 760)
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(after: .appSettings) {
                Button("Connection…") {
                    NotificationCenter.default.post(name: .showHeinzelConnection, object: nil)
                }
                .keyboardShortcut(",", modifiers: [.command, .shift])
            }
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

struct ConnectionView: View {
    #if os(macOS)
    let service: LocalService
    #endif

    @State private var started = false
    @AppStorage("serverOrigin") private var serverOrigin = ""
    @State private var link = ""
    @State private var endpoint: Endpoint?
    @State private var connecting = false
    @State private var loadingWorkspace = false
    @State private var error: String?
    @State private var showConnection = false
    @State private var sharedWorkspace: Endpoint?

    var body: some View {
        content.task {
            guard !started else { return }
            started = true
            #if DEBUG
            if let link = ProcessInfo.processInfo.environment["HZ_SPIKE_SERVER"] {
                await connect(link)
                return
            }
            #endif
            #if os(macOS)
            await startLocal()
            #else
            if !serverOrigin.isEmpty && sharedWorkspace == nil { await connect(serverOrigin) }
            #endif
        }
        .onOpenURL { url in
            guard let target = Endpoint.sharedWorkspace(url) else { return }
            sharedWorkspace = target
        }
        .alert("Open Heinzel workspace?", isPresented: Binding(
            get: { sharedWorkspace != nil },
            set: { if !$0 { sharedWorkspace = nil } }
        ), presenting: sharedWorkspace) { target in
            Button("Open") { Task { await connect(target.origin.absoluteString) } }
            Button("Cancel", role: .cancel) {}
        } message: { target in
            Text(target.origin.absoluteString)
        }
    }

    @ViewBuilder
    private var content: some View {
        if let endpoint {
            WorkspaceView(endpoint: endpoint, ready: { loadingWorkspace = false }) { message in
                error = message
                showConnection = true
            }
            .id(endpoint.url)
            .overlay { if loadingWorkspace { startup } }
            #if os(macOS)
            .ignoresSafeArea(edges: .top)
            .onReceive(NotificationCenter.default.publisher(for: .showHeinzelConnection)) { _ in
                showConnection = true
            }
            #else
            .overlay(alignment: .bottomTrailing) {
                Button("Connection", systemImage: "network") { showConnection = true }
                    .labelStyle(.iconOnly)
                    .frame(width: 44, height: 30)
                    .offset(y: -44)
                    .padding(.trailing, 14)
            }
            #endif
            .sheet(isPresented: $showConnection) {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Connection").font(.title2.bold())
                    Text(endpoint.origin.absoluteString).textSelection(.enabled)
                    if let shareURL = endpoint.shareURL {
                        ShareLink(item: shareURL) { Label("Share workspace link", systemImage: "square.and.arrow.up") }
                    }
                    if let error { Text(error).foregroundStyle(.red) }
                    HStack {
                        Button("Disconnect", role: .destructive) {
                            showConnection = false
                            self.endpoint = nil
                            serverOrigin = ""
                            #if os(macOS)
                            Task { await service.stop() }
                            #endif
                        }
                        Spacer()
                        Button("Done") { showConnection = false }
                    }
                }
                .padding(24)
                .presentationDetents([.medium])
            }
        } else if connecting || (!started && launchesAtStartup) {
            startup
        } else {
            setup
        }
    }

    private var launchesAtStartup: Bool {
        #if os(macOS)
        true
        #else
        !serverOrigin.isEmpty
        #endif
    }

    private var startup: some View {
        VStack(spacing: 20) {
            Image("StartupIcon")
                .resizable()
                .scaledToFit()
                .frame(width: 92, height: 92)
                .accessibilityHidden(true)
            Text("Heinzelmännchen").font(.largeTitle.bold())
            ProgressView("Opening workspace…")
                .foregroundStyle(.secondary)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color("StartupBackground"))
    }

    private var setup: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Image(systemName: "sparkles")
                    .font(.system(size: 32))
                    .foregroundStyle(.tint)
                    .accessibilityHidden(true)
                Text("Heinzelmännchen").font(.largeTitle.bold())
                #if os(macOS)
                Button("Start local workspace", systemImage: "desktopcomputer") {
                    Task { await startLocal() }
                }
                .buttonStyle(.borderedProminent)
                Divider()
                #endif
                Text("Connect to a server").font(.headline)
                Text("Paste the private HTTPS sign-in link from your Heinzel host. If this device has already been paired, use the server address.")
                    .foregroundStyle(.secondary)
                SecureField("Heinzel HTTPS link", text: $link)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("serverLink")
                    .onSubmit { Task { await connect(link) } }
                Button("Connect") { Task { await connect(link) } }
                    .buttonStyle(.borderedProminent)
                    .disabled(link.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if !serverOrigin.isEmpty {
                    Button("Reconnect to \(serverOrigin)") { Task { await connect(serverOrigin) } }
                }
                if connecting { ProgressView("Connecting…") }
                if let error { Text(error).foregroundStyle(.red) }
            }
            .disabled(connecting)
            .frame(maxWidth: 450, alignment: .leading)
            .padding(28)
            .frame(maxWidth: .infinity)
        }
        .frame(maxHeight: .infinity)
    }

    private func connect(_ input: String) async {
        guard !connecting else { return }
        guard let target = Endpoint(input) else {
            error = "Enter a private HTTPS server address or Heinzel sign-in link."
            return
        }
        connecting = true
        error = nil
        defer { connecting = false }
        do {
            try await target.verify()
            serverOrigin = target.origin.absoluteString
            link = ""
            if endpoint != target { loadingWorkspace = true }
            endpoint = target
            #if os(macOS)
            await service.stop()
            #endif
        } catch {
            self.error = (error as? ConnectionError)?.errorDescription ?? "The server could not be reached."
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
            loadingWorkspace = true
            endpoint = target
        } catch {
            self.error = (error as? ConnectionError)?.errorDescription ?? ConnectionError.startup.errorDescription
        }
    }
    #endif
}
