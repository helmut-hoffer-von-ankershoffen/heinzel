import Foundation

@main
struct LocalServiceProbe {
    @MainActor static func main() async throws {
        let service = LocalService()
        guard LocalService.attachArguments == ["ui", "attach"] else { throw ProbeFailure.spawnsAServer }
        if CommandLine.arguments.last == "outdated" {
            do {
                _ = try await service.start()
            } catch ConnectionError.outdatedCLI {
                return
            }
            throw ProbeFailure.unexpectedStartup
        }
        if CommandLine.arguments.last == "lost" {
            try await reattachAfterLoss(service)
            return
        }
        if CommandLine.arguments.last == "refuse" {
            do {
                _ = try await service.start()
            } catch ConnectionError.startup {
                return
            }
            throw ProbeFailure.unexpectedStartup
        }
        let endpoint: Endpoint
        do {
            endpoint = try await service.start()
            try await expectFixture(endpoint)
        } catch {
            throw ProbeFailure.unexpectedLifecycle
        }
        // Leaving the workspace never ends the owned service: it still answers afterwards.
        guard await service.stop() else { throw ProbeFailure.uncleanShutdown }
        do {
            try await expectFixture(endpoint)
        } catch {
            throw ProbeFailure.serviceStopped
        }
    }

    /// The service crashes (the probe kills the fixture's service); the app notices and attaches again.
    @MainActor static func reattachAfterLoss(_ service: LocalService) async throws {
        service.probeInterval = .milliseconds(100)
        var lost = false
        service.onLost = { lost = true }
        let first = try await service.start()
        try await expectFixture(first)
        guard let text = try? String(contentsOfFile: "service.pid", encoding: .utf8), let pid = Int32(text) else {
            throw ProbeFailure.unexpectedLifecycle
        }
        kill(pid, SIGKILL)
        for _ in 0..<50 where !lost { try await Task.sleep(for: .milliseconds(100)) }
        guard lost else { throw ProbeFailure.lossUnnoticed }
        let second = try await service.start()
        try await expectFixture(second)
        guard second.origin != first.origin else { throw ProbeFailure.unexpectedLifecycle }
        await service.stop()
    }

    @MainActor static func expectFixture(_ endpoint: Endpoint) async throws {
        let state = try await endpoint.readLifecycle()
        guard state.instance == "fixture", state.version == "1.0", state.restartable == true else {
            throw ProbeFailure.unexpectedLifecycle
        }
    }

    enum ProbeFailure: Error {
        case spawnsAServer, unexpectedStartup, unexpectedLifecycle, uncleanShutdown, serviceStopped, lossUnnoticed
    }
}
