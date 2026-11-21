import Foundation

@main
struct LocalServiceProbe {
    @MainActor static func main() async throws {
        let service = LocalService()
        guard LocalService.attachArguments == ["ui", "attach"] else { throw ProbeFailure.spawnsAServer }
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

    static func expectFixture(_ endpoint: Endpoint) async throws {
        let state = try await endpoint.readLifecycle()
        guard state.instance == "fixture", state.version == "1.0", state.restartable == true else {
            throw ProbeFailure.unexpectedLifecycle
        }
    }

    enum ProbeFailure: Error {
        case spawnsAServer, unexpectedStartup, unexpectedLifecycle, uncleanShutdown, serviceStopped
    }
}
