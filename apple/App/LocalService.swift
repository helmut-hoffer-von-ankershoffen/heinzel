#if os(macOS)
import AppKit
import Foundation
import Synchronization

/// Attach to this Mac's one owned web service (the launchd job) through `hz ui attach`; never serve.
///
/// `hz ui attach` installs or starts the service when needed, prints one session link and exits. The app keeps no
/// hz process of its own, so quitting, crashing or updating the app leaves the service running, and a second server
/// with its own auto-updater on old code can't exist.
@MainActor
final class LocalService: NSObject, NSApplicationDelegate {
    private var installation: ManagedInstallation?
    private var attached: (executable: String, environment: [String: String])?
    private var pending: Task<Endpoint, Error>?
    private var monitor: Task<Void, Never>?
    /// Called when the attached service stops answering this endpoint (a crash restart has a new session token);
    /// the app then attaches again.
    var onLost: (() -> Void)?
    /// How often the app asks the attached service whether it still answers.
    var probeInterval: Duration = .seconds(5)
    /// Consecutive failed probes before the service counts as lost: one slow answer during a restart is no loss.
    var lostAfter = 3
    /// The waits between attempts to attach again after a loss (bounded backoff).
    static let reattachDelays: [Duration] = [.seconds(5), .seconds(10), .seconds(20), .seconds(40)]

    /// Try `attempt` now, then after each delay; true at the first success, false when all failed.
    static func retrying(
        delays: [Duration] = reattachDelays,
        wait: (Duration) async -> Void = { try? await Task.sleep(for: $0) },
        attempt: () async -> Bool
    ) async -> Bool {
        for index in 0...delays.count {
            if Task.isCancelled { return false }
            if await attempt() { return true }
            if index < delays.count { await wait(delays[index]) }
        }
        return false
    }

    /// The exact arguments: the app only ever attaches. Tests check that `serve` never appears.
    static let attachArguments = ["ui", "attach"]

    /// Attach, or wait for the attach already running (a re-attach and the person's click share one).
    func start() async throws -> Endpoint {
        if let pending { return try await pending.value }
        let task = Task { try await self.attach() }
        pending = task
        defer { pending = nil }
        return try await task.value
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
                    try await endpoint.verify()
                    return endpoint
                }
                defer { group.cancelAll() }
                guard let endpoint = try await group.next() else { throw ConnectionError.startup }
                return endpoint
            }
            attached = (executable, environment)
            watch(endpoint)
            return endpoint
        } catch {
            // Only the one-shot attach command is ours to end; the service it reached keeps running.
            if child.isRunning { child.interrupt() }
            throw error
        }
    }

    private func watch(_ endpoint: Endpoint) {
        monitor?.cancel()
        let interval = probeInterval
        let threshold = max(1, lostAfter)
        monitor = Task { [weak self] in
            var failures = 0
            while !Task.isCancelled {
                try? await Task.sleep(for: interval)
                if Task.isCancelled { return }
                do {
                    _ = try await endpoint.readLifecycle()
                    failures = 0
                } catch {
                    failures += 1
                    if Task.isCancelled { return }
                    if failures < threshold { continue }
                    self?.monitor = nil
                    self?.onLost?()
                    return
                }
            }
        }
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

    /// Leave the local workspace. The owned service is not the app's to stop, so nothing ends; always true.
    @discardableResult
    func stop() async -> Bool {
        monitor?.cancel()
        monitor = nil
        return true
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
