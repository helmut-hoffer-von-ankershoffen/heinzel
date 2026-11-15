import Foundation

@main
struct LocalServiceProbe {
    @MainActor static func main() async throws {
        let service = LocalService()
        if CommandLine.arguments.last == "refuse" {
            do {
                _ = try await service.start()
            } catch ConnectionError.startup {
                return
            }
            await service.stop()
            throw ProbeFailure.unexpectedStartup
        }
        do {
            let endpoint = try await service.start()
            // The child writes a full megabyte to stdout before replying to this request.
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForResource = 3
            let session = URLSession(configuration: configuration)
            defer { session.invalidateAndCancel() }
            var request = URLRequest(url: endpoint.origin.appending(path: "noise"))
            request.setValue("Bearer " + (endpoint.token ?? ""), forHTTPHeaderField: "Authorization")
            let (_, response) = try await session.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 204 else { throw ProbeFailure.outputBlocked }
        } catch {
            await service.stop()
            // URLSession errors can include request details. Report only the failing test stage.
            throw ProbeFailure.outputBlocked
        }
        guard await service.stop() else { throw ProbeFailure.uncleanShutdown }
    }

    enum ProbeFailure: Error {
        case unexpectedStartup, outputBlocked, uncleanShutdown
    }
}
