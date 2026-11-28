#if os(macOS)
import AppKit
import Foundation
import Combine
import Synchronization

/// Attach to this Mac's one owned web service (the launchd job) through `hz ui attach`; never serve.
///
/// `hz ui attach` installs or starts the service when needed, prints one session link and exits. The app keeps no
/// hz process of its own, so quitting, crashing or updating the app leaves the service running, and a second server
/// with its own auto-updater on old code can't exist.
@MainActor
final class LocalService: NSObject, NSApplicationDelegate, ObservableObject {
    private var installation: ManagedInstallation?
    private var attached: (executable: String, environment: [String: String])?
    // Keep only the owned loopback session in memory when the client leaves its workspace.
    @Published private(set) var localSession: Endpoint?
    private var pending: Task<Endpoint, Error>?

    func runningBackend() async -> Endpoint.Lifecycle? {
        guard let localSession else { return nil }
        return try? await localSession.readLifecycle()
    }
    private var monitor: Task<Void, Never>?
    enum Recovery: Equatable {
        case reconnecting, unavailable, paused, stopped, signIn, denied

        var retries: Bool { self == .reconnecting || self == .unavailable }

        var detail: String {
            switch self {
            case .reconnecting: "Reconnecting…"
            case .unavailable: "Temporarily unavailable · Reconnecting…"
            case .paused: "Paused"
            case .stopped: "Stopped · Start local workspace"
            case .signIn: "Sign in"
            case .denied: "Access denied"
            }
        }
    }

    @Published private(set) var recovery: Recovery?
    // A selected tab can remain signed in after the service rotates its launch token.
    @Published private var pageAuthenticatedSession: Endpoint?
    private enum SessionAuthority { case launch, page }
    private var selected: Endpoint?
    private var foreground = true
    private var sleeping = false
    var onLost: (() -> Void)?
    var verifyRecoveredPage: ((Endpoint) async throws -> Void)?
    var onRecovered: ((Endpoint) -> Void)?
    var probeInterval: Duration = .seconds(5)
    var lostAfter = 3
    var probe: (Endpoint) async throws -> Endpoint.Lifecycle = { try await $0.readLifecycle() }
    var wait: (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    var retryDelay: (Int) -> Duration = { attempt in
        let seconds = [5.0, 10.0, 20.0, 40.0][min(attempt, 3)]
        return .seconds(min(40, seconds * Double.random(in: 0.9...1.1)))
    }

    /// The exact arguments: the app only ever attaches. Tests check that `serve` never appears.
    static let attachArguments = ["ui", "attach"]

    /// Attach, or wait for the attach already running (a re-attach and the person's click share one).
    func start(monitoring: Bool = true) async throws -> Endpoint {
        let endpoint: Endpoint
        if let pending {
            endpoint = try await pending.value
        } else {
            let task = Task {
                if let localSession, localSession != pageAuthenticatedSession, await runningBackend() != nil {
                    return localSession
                }
                // Explicit reattachment obtains a verified native token when only the tab is signed in.
                let endpoint = try await attach()
                localSession = endpoint
                pageAuthenticatedSession = nil
                return endpoint
            }
            pending = task
            defer { pending = nil }
            endpoint = try await task.value
        }
        // A staged page starts its monitor when the chooser selects it.
        if monitoring { selectWorkspace(endpoint) }
        return endpoint
    }

    private func attach() async throws -> Endpoint {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let executable: String
        if Bundle.main.object(forInfoDictionaryKey: "HeinzelManagedInstall") as? String == "YES" {
            let binding = try installation ?? ManagedInstallation(home: home)
            executable = binding.executable
            installation = binding
        } else {
            executable = try developmentCLI(home: home)
        }

        let child = Process()
        let stdout = Pipe()
        child.executableURL = URL(fileURLWithPath: executable)
        child.arguments = Self.attachArguments
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = stdout
        child.standardError = FileHandle.nullDevice
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = [
            home.appending(path: ".local/bin").path, "/opt/homebrew/bin", "/usr/local/bin",
            environment["PATH"] ?? "/usr/bin:/bin",
        ].joined(separator: ":")
        child.environment = environment
        let exited = AsyncStream<Int32>.makeStream()
        child.terminationHandler = { process in
            exited.continuation.yield(process.terminationReason == .exit ? process.terminationStatus : -1)
            exited.continuation.finish()
        }
        try child.run()
        let output = startupLine(from: stdout.fileHandleForReading)

        do {
            let endpoint = try await withThrowingTaskGroup(of: Endpoint.self) { group in
                group.addTask {
                    // Installing the service and its bounded readiness wait take well under this.
                    try await Task.sleep(for: .seconds(60))
                    throw ConnectionError.startup
                }
                group.addTask {
                    var lines = output.makeAsyncIterator()
                    var statuses = exited.stream.makeAsyncIterator()
                    let line = await lines.next()
                    let status = await statuses.next()
                    // An older hz has no `ui attach` (a usage error, exit 2): say that an update fixes it.
                    if status == 2 && line == nil { throw ConnectionError.outdatedCLI }
                    guard status == 0, let line,
                          let value = String(data: line, encoding: .utf8),
                          let endpoint = Endpoint(value, loopback: true) else {
                        throw ConnectionError.startup
                    }
                    // The workspaces this app connects to list it with this backend version.
                    ClientIdentity.backendVersion = endpoint.token == nil ? nil : try await endpoint.readLifecycle().version
                    return endpoint
                }
                defer { group.cancelAll() }
                guard let endpoint = try await group.next() else { throw ConnectionError.startup }
                return endpoint
            }
            attached = (executable, environment)
            return endpoint
        } catch {
            // Only the one-shot attach command is ours to end; the service it reached keeps running.
            if child.isRunning { child.interrupt() }
            throw error
        }
    }

    private func verifyPage(_ endpoint: Endpoint) async throws {
        guard let verifyRecoveredPage else { throw ConnectionError.unverifiedWorkspace }
        try await verifyRecoveredPage(endpoint)
    }

    private func verifySelectedSession(_ endpoint: Endpoint) async throws -> (authority: SessionAuthority, version: String?) {
        if pageAuthenticatedSession == endpoint && recovery == nil {
            try await verifyPage(endpoint)
            return (.page, nil)
        }
        // During an outage the native request can distinguish a stopped loopback listener.
        // Its 401 only rejects the launch token. WebKit verifies its own credentials in place.
        let version: String
        do {
            version = try await probe(endpoint).version
        } catch ConnectionError.authentication {
            try Task.checkCancellation()
            try await verifyPage(endpoint)
            return (.page, nil)
        }
        try Task.checkCancellation()
        if recovery != nil { try await verifyPage(endpoint) }
        return (.launch, version)
    }

    /// One task owns both observation and recovery. Each change of selection, user intent or
    /// activity cancels that task. A late HTTP or clock completion can never publish a result.
    private func watch(checkNow: Bool = false) {
        monitor?.cancel()
        monitor = nil
        guard let endpoint = selected, foreground, !sleeping, recovery == nil || recovery?.retries == true else { return }
        monitor = Task {
            var misses = 0
            var retry = 0
            var immediate = checkNow || recovery != nil
            while !Task.isCancelled {
                if !immediate {
                    let delay = recovery == nil ? probeInterval : retryDelay(retry)
                    if recovery != nil { retry += 1 }
                    do { try await wait(delay) } catch { return }
                }
                immediate = false
                guard !Task.isCancelled else { return }
                do {
                    let (authority, version) = try await verifySelectedSession(endpoint)
                    guard !Task.isCancelled else { return }
                    if let version { ClientIdentity.backendVersion = version }
                    let adoptedPage = authority == .page && pageAuthenticatedSession != endpoint
                    if adoptedPage { pageAuthenticatedSession = endpoint }
                    if authority == .launch && pageAuthenticatedSession == endpoint { pageAuthenticatedSession = nil }
                    misses = 0
                    if recovery != nil || adoptedPage {
                        recovery = nil
                        onRecovered?(endpoint)
                    }
                } catch {
                    guard !Task.isCancelled else { return }
                    misses += 1
                    if recovery == nil {
                        guard misses >= max(1, lostAfter) else { continue }
                        recovery = .reconnecting
                        onLost?()
                        guard !Task.isCancelled else { return }
                        immediate = true
                        retry = 0
                    }
                    switch error {
                    case ConnectionError.authentication: recovery = .signIn; return
                    case ConnectionError.accessDenied: recovery = .denied; return
                    case ConnectionError.unverifiedWorkspace: recovery = .unavailable
                    default: recovery = .reconnecting
                    }
                    if retry >= 4, let failure = error as? URLError, failure.code == .cannotConnectToHost {
                        recovery = .stopped
                        return
                    }
                }
            }
        }
    }

    var publishingSession: Endpoint? { recovery == nil && pageAuthenticatedSession != localSession ? localSession : nil }

    /// The local row and both menus share this decision before starting an attach.
    func shouldAttachOnSelection() -> Bool {
        if recovery == .paused { resumeRecovery(); return false }
        return recovery == nil || recovery == .stopped || recovery == .signIn
    }

    /// The native monitor confirms transport failures. WebKit can also lose its own
    /// credentials while the launch credential used by the native monitor still works.
    func reportPageIssue(_ issue: WorkspaceProblem, for endpoint: Endpoint) {
        guard selected == endpoint, recovery == nil, issue.revokesAccess else { return }
        recovery = issue == .signIn ? .signIn : .denied
        monitor?.cancel()
        onLost?()
    }

    func pauseRecovery() {
        guard recovery?.retries == true else { return }
        recovery = .paused
        watch()
    }

    func resumeRecovery() {
        guard recovery == .paused else { return }
        recovery = .reconnecting
        watch(checkNow: true)
    }

    func setForeground(_ active: Bool) {
        guard active != foreground else { return }
        foreground = active
        watch(checkNow: active)
    }

    func setSleeping(_ asleep: Bool) {
        guard asleep != sleeping else { return }
        sleeping = asleep
        watch(checkNow: !asleep)
    }

    private func startupLine(from handle: FileHandle) -> AsyncStream<Data> {
        let pending = Mutex<Data?>(Data())
        let (line, result) = AsyncStream<Data>.makeStream()
        handle.readabilityHandler = { reader in
            let data = reader.availableData
            guard !data.isEmpty else {
                reader.readabilityHandler = nil
                result.finish()
                return
            }
            pending.withLock { buffer in
                guard let length = buffer?.count else { return }
                let newline = data.firstIndex(of: 10)
                let prefix = data.prefix(upTo: newline ?? data.endIndex)
                guard length + prefix.count <= 4096 else {
                    buffer = nil
                    result.finish()
                    return
                }
                buffer?.append(contentsOf: prefix)
                if newline != nil {
                    if let buffer { result.yield(buffer) }
                    buffer = nil
                    result.finish()
                }
            }
        }
        return line
    }

    private func developmentCLI(home: URL) throws -> String {
        #if DEBUG
        if let path = ProcessInfo.processInfo.environment["HZ_SPIKE_CLI"] {
            guard path.hasPrefix("/"), FileManager.default.isExecutableFile(atPath: path) else {
                throw ConnectionError.missingCLI
            }
            return path
        }
        #endif
        let candidates = [home.appending(path: ".local/bin/hz").path, "/opt/homebrew/bin/hz", "/usr/local/bin/hz"]
        guard let executable = candidates.first(where: FileManager.default.isExecutableFile(atPath:)) else {
            throw ConnectionError.missingCLI
        }
        return executable
    }

    /// Only the selected local workspace needs automatic client reattachment.
    /// Leaving or selecting a remote workspace keeps the backend and its loopback session alive.
    func selectWorkspace(_ selected: Endpoint?) {
        self.selected = selected == localSession ? selected : nil
        recovery = nil
        watch()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        monitor?.cancel()
        let managed = installation != nil
        installation = nil  // Releases the installer's desktop-use gate before the refresh needs it.
        if managed, let attached { requestRefresh(executable: attached.executable, environment: attached.environment) }
        return .terminateNow
    }

    private func requestRefresh(executable: String, environment: [String: String]?) {
        let refresh = Process()
        refresh.executableURL = URL(fileURLWithPath: executable)
        refresh.arguments = ["app", "refresh"]
        refresh.environment = environment
        refresh.standardInput = FileHandle.nullDevice
        refresh.standardOutput = FileHandle.nullDevice
        refresh.standardError = FileHandle.nullDevice
        try? refresh.run()
    }
}
/// Read the installer’s private binding while holding its exclusive desktop-use gate.
final class ManagedInstallation {
    let executable: String
    private let lock: Int32

    init(home: URL) throws {
        var directories: [Int32] = []
        defer { for descriptor in directories.reversed() { Darwin.close(descriptor) } }
        let directoryFlags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
        for component in [home.path, ".config", "heinzel"] {
            let descriptor = openat(directories.last ?? AT_FDCWD, component, directoryFlags)
            guard descriptor >= 0 else { throw ConnectionError.managedInstall }
            directories.append(descriptor)
            var info = stat()
            guard fstat(descriptor, &info) == 0, info.st_uid == getuid(), info.st_mode & 0o022 == 0 else {
                throw ConnectionError.managedInstall
            }
        }

        let directory = directories[2]
        let gate = try Self.openPrivate("desktop-use.lock", in: directory)
        var keepGate = false
        defer { if !keepGate { Darwin.close(gate) } }
        guard flock(gate, LOCK_EX | LOCK_NB) == 0 else { throw ConnectionError.managedInstall }
        var journal = stat()
        guard fstatat(directory, "desktop-installation-journal.json", &journal, AT_SYMLINK_NOFOLLOW) < 0,
              errno == ENOENT else { throw ConnectionError.managedInstall }

        let receipt = try Self.openPrivate("desktop-owned-cli.json", in: directory)
        defer { Darwin.close(receipt) }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 1024)
        while data.count <= 4096 {
            let count = read(receipt, &buffer, buffer.count)
            if count == 0 { break }
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { throw ConnectionError.managedInstall }
            data.append(contentsOf: buffer.prefix(count))
        }
        guard data.count <= 4096, let binding = try? JSONDecoder().decode(Binding.self, from: data),
              binding.version == 1, binding.executable.hasPrefix("/"),
              !binding.executable.contains("\0") else { throw ConnectionError.managedInstall }
        var executableInfo = stat()
        guard stat(binding.executable, &executableInfo) == 0,
              executableInfo.st_mode & S_IFMT == S_IFREG,
              access(binding.executable, X_OK) == 0 else { throw ConnectionError.managedInstall }

        executable = binding.executable
        lock = gate
        keepGate = true
    }

    deinit { Darwin.close(lock) }

    private static func openPrivate(_ name: String, in directory: Int32) throws -> Int32 {
        let descriptor = openat(directory, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard descriptor >= 0 else { throw ConnectionError.managedInstall }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_uid == getuid(), info.st_nlink == 1,
              info.st_mode & S_IFMT == S_IFREG, info.st_mode & 0o077 == 0, info.st_size <= 4096 else {
            Darwin.close(descriptor)
            throw ConnectionError.managedInstall
        }
        return descriptor
    }

    private struct Binding: Decodable {
        let version: Int
        let executable: String
    }
}
#endif
