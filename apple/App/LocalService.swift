#if os(macOS)
import AppKit
import Foundation

@MainActor
final class LocalService: NSObject, NSApplicationDelegate {
    private var child: Process?
    var onExit: (() -> Void)?

    func start() async throws -> Endpoint {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let executable = [home.appending(path: ".local/bin/hz").path, "/opt/homebrew/bin/hz", "/usr/local/bin/hz"]
            .first(where: FileManager.default.isExecutableFile(atPath:))
        guard let executable else { throw ConnectionError.missingCLI }
        guard child == nil else { throw ConnectionError.startup }

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
                self?.onExit?()
            }
        }

        try child.run()
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
            return try await withThrowingTaskGroup(of: Endpoint.self) { group in
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
        } catch {
            await stop()
            throw error
        }
    }

    func stop() async {
        guard let child, child.isRunning else { return }
        child.interrupt()
        for _ in 0..<80 {
            if !child.isRunning { return }
            try? await Task.sleep(for: .milliseconds(100))
        }
        if child.isRunning { kill(child.processIdentifier, SIGKILL) }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard child?.isRunning == true else { return .terminateNow }
        Task {
            await stop()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}
#endif
