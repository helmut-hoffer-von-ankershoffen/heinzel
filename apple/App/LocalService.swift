#if os(macOS)
import AppKit
import Foundation

@MainActor
final class LocalService: NSObject, NSApplicationDelegate {
    private var child: Process?
    private var installation: ManagedInstallation?
    private var readyChild: Process?
    private var quitting = false
    var onExit: (() -> Void)?

    func start() async throws -> Endpoint {
        let home = FileManager.default.homeDirectoryForCurrentUser
        guard child == nil else { throw ConnectionError.startup }
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
        child.arguments = ["ui", "serve", "--port", "0", "--no-browser"]
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = stdout
        child.standardError = FileHandle.nullDevice
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = [
            home.appending(path: ".local/bin").path, "/opt/homebrew/bin", "/usr/local/bin",
            environment["PATH"] ?? "/usr/bin:/bin",
        ].joined(separator: ":")
        child.environment = environment
        child.terminationHandler = { [weak self] process in
            Task { @MainActor in
                guard self?.child === process else { return }
                self?.child = nil
                self?.readyChild = nil
                if self?.quitting == false { self?.onExit?() }
            }
        }

        do {
            try child.run()
        } catch {
            installation = nil
            throw error
        }
        self.child = child
        let handle = stdout.fileHandleForReading
        let output = AsyncStream<Data> { continuation in
            handle.readabilityHandler = { reader in
                let data = reader.availableData
                if data.isEmpty { continuation.finish() }
                else { continuation.yield(data) }
            }
        }
        defer { handle.readabilityHandler = nil }

        do {
            let endpoint = try await withThrowingTaskGroup(of: Endpoint.self) { group in
                group.addTask {
                    try await Task.sleep(for: .seconds(90))
                    throw ConnectionError.startup
                }
                group.addTask {
                    var line = Data()
                    for await data in output {
                        line.append(data)
                        guard line.count <= 4096 else { break }
                        if line.contains(10) {
                            guard let value = String(data: line, encoding: .utf8),
                                  let endpoint = Endpoint(value, loopback: true) else { break }
                            try await endpoint.verify()
                            return endpoint
                        }
                    }
                    throw ConnectionError.startup
                }
                defer { group.cancelAll() }
                guard let endpoint = try await group.next() else { throw ConnectionError.startup }
                return endpoint
            }
            guard child.isRunning, !quitting else { throw ConnectionError.startup }
            readyChild = child
            return endpoint
        } catch {
            await stop()
            throw error
        }
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

    @discardableResult
    func stop() async -> Bool {
        guard let child, child.isRunning else { return false }
        readyChild = nil
        child.interrupt()
        for _ in 0..<80 {
            if !child.isRunning {
                return child.terminationReason == .exit && child.terminationStatus == 0
            }
            try? await Task.sleep(for: .milliseconds(100))
        }
        if child.isRunning { kill(child.processIdentifier, SIGKILL) }
        for _ in 0..<20 {
            if !child.isRunning { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateLater }
        guard let child, child.isRunning else { return .terminateNow }
        let refresh = readyChild === child ? installation?.executable : nil
        quitting = true
        Task {
            let graceful = await stop()
            guard !child.isRunning else {
                quitting = false
                sender.reply(toApplicationShouldTerminate: false)
                return
            }
            installation = nil
            if graceful, let refresh { requestRefresh(executable: refresh, environment: child.environment) }
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
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
