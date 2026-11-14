import Foundation

struct Endpoint: Equatable {
    let url: URL
    let origin: URL
    let token: String?

    init?(_ input: String, loopback: Bool = false) {
        guard input.utf8.count <= 4096,
              let parts = URLComponents(string: input.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil,
              parts.percentEncodedPath.isEmpty || parts.percentEncodedPath == "/",
              parts.port.map({ (1...65535).contains($0) }) ?? true,
              let url = parts.url else { return nil }

        if loopback {
            guard parts.scheme == "http", host == "127.0.0.1", parts.port != nil else { return nil }
        } else {
            guard parts.scheme == "https" else { return nil }
        }

        if let fragment = parts.percentEncodedFragment {
            guard fragment.wholeMatch(of: /token=[A-Za-z0-9_-]{32,}/) != nil else { return nil }
            token = String(fragment.dropFirst(6))
        } else {
            guard !loopback else { return nil }
            token = nil
        }
        var origin = parts
        origin.fragment = nil
        origin.path = ""
        self.origin = origin.url!
        self.url = url
    }

    /// Shared links identify a workspace. Authentication remains a separate in-app step.
    static func sharedWorkspace(_ url: URL) -> Endpoint? {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "heinzel", parts.host == "connect", parts.path.isEmpty,
              parts.user == nil, parts.password == nil, parts.port == nil, parts.fragment == nil,
              let query = parts.queryItems, query.count == 1, query[0].name == "server",
              let value = query[0].value, let endpoint = Endpoint(value),
              endpoint.token == nil else { return nil }
        return endpoint
    }

    var shareURL: URL? {
        guard origin.scheme == "https" else { return nil }
        var parts = URLComponents()
        parts.scheme = "heinzel"
        parts.host = "connect"
        parts.queryItems = [URLQueryItem(name: "server", value: origin.absoluteString)]
        return parts.url
    }

    func owns(_ url: URL) -> Bool {
        guard url.user == nil, url.password == nil,
              url.scheme == origin.scheme,
              url.host?.lowercased() == origin.host?.lowercased() else { return false }
        let defaultPort = origin.scheme == "https" ? 443 : 80
        return (url.port ?? defaultPort) == (origin.port ?? defaultPort)
    }

    func verify() async throws {
        guard let token else { return }
        let session = URLSession(configuration: .ephemeral, delegate: RefuseRedirects(), delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        var request = URLRequest(url: origin.appending(path: "api/lifecycle"), timeoutInterval: 6)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (body, response) = try await session.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let lifecycle = try? JSONDecoder().decode(Lifecycle.self, from: body),
              !lifecycle.instance.isEmpty, !lifecycle.version.isEmpty else {
            throw ConnectionError.authentication
        }
    }

    private struct Lifecycle: Decodable {
        let instance: String
        let version: String
    }
}

private final class RefuseRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(
        _ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

enum ConnectionError: LocalizedError {
    case authentication, missingCLI, startup, stopped

    var errorDescription: String? {
        switch self {
        case .authentication: "Heinzel did not accept the sign-in link. Copy a fresh link from the host and try again."
        case .missingCLI: "Install Heinzel with uvx heinzel@latest, or connect to an existing server."
        case .startup: "The local Heinzel service could not start. Check that hz ui serve runs in Terminal."
        case .stopped: "The local Heinzel service stopped. Start it again to return to the workspace."
        }
    }
}
