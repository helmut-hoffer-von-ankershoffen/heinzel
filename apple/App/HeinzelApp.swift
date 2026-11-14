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
        #else
        WindowGroup {
            NavigationStack { ConnectionView() }
        }
        #endif
    }
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
    @State private var error: String?
    @State private var showConnection = false

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
            #endif
        }
    }

    @ViewBuilder
    private var content: some View {
        if let endpoint {
            WorkspaceView(endpoint: endpoint) { message in
                error = message
                showConnection = true
            }
            .id(endpoint.origin)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button("Connection", systemImage: "network") { showConnection = true }
                }
            }
            .sheet(isPresented: $showConnection) {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Connection").font(.title2.bold())
                    Text(endpoint.origin.absoluteString).textSelection(.enabled)
                    if let error { Text(error).foregroundStyle(.red) }
                    HStack {
                        Button("Disconnect", role: .destructive) {
                            showConnection = false
                            self.endpoint = nil
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
            .navigationTitle("Heinzelmännchen")
        } else {
            setup
        }
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
            endpoint = target
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
            endpoint = try await service.start()
        } catch {
            self.error = (error as? ConnectionError)?.errorDescription ?? ConnectionError.startup.errorDescription
        }
    }
    #endif
}
