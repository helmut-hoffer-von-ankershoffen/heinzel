import Foundation
#if os(macOS)
import CFNetwork
#endif

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
        if origin.scheme == "https", origin.port == 443 { origin.port = nil }
        origin.host = host
        origin.path = ""
        self.origin = origin.url!
        self.url = url
    }

    /// Shared links identify a workspace. Authentication remains a separate in-app step.
    static func sharedWorkspace(_ url: URL) -> Endpoint? {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.user == nil, parts.password == nil, parts.port == nil else { return nil }
        let query: [URLQueryItem]?
        switch (parts.scheme, parts.host, parts.percentEncodedPath) {
        case ("heinzel", "connect", "") where parts.fragment == nil:
            query = parts.queryItems
        case ("https", "heinzel.team", "/open/") where parts.query == nil:
            guard let fragment = parts.percentEncodedFragment, !fragment.contains("?"),
                  let payload = URLComponents(string: "https://heinzel.team/?" + fragment) else { return nil }
            query = payload.queryItems
        default:
            return nil
        }
        guard let query, query.count == 1, query[0].name == "server",
              let value = query[0].value, let endpoint = Endpoint(value),
              endpoint.token == nil else { return nil }
        return endpoint
    }

    static var sharesHTTPSLinks: Bool {
        Bundle.main.object(forInfoDictionaryKey: "HeinzelHTTPSLinks") as? String == "YES"
    }

    var shareURL: URL? {
        guard origin.scheme == "https" else { return nil }
        var payload = URLComponents()
        payload.queryItems = [URLQueryItem(name: "server", value: origin.absoluteString)]
        var parts = URLComponents()
        if Self.sharesHTTPSLinks {
            parts.scheme = "https"
            parts.host = "heinzel.team"
            parts.path = "/open/"
            parts.percentEncodedFragment = payload.percentEncodedQuery
        } else {
            parts.scheme = "heinzel"
            parts.host = "connect"
            parts.percentEncodedQuery = payload.percentEncodedQuery
        }
        return parts.url
    }

    func owns(_ url: URL) -> Bool {
        guard url.user == nil, url.password == nil,
              url.scheme == origin.scheme,
              url.host?.lowercased() == origin.host?.lowercased() else { return false }
        let defaultPort = origin.scheme == "https" ? 443 : 80
        return (url.port ?? defaultPort) == (origin.port ?? defaultPort)
    }

    func permitsNavigation(to url: URL, inMainFrame: Bool) -> Bool {
        if owns(url) { return true }
        guard !inMainFrame else { return false }
        if url.absoluteString == "about:blank" { return true }
        // The host CSP still decides when a soundscape may use either YouTube player origin.
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "https", parts.user == nil, parts.password == nil, parts.fragment == nil,
              parts.port == nil || parts.port == 443,
              let host = parts.host?.lowercased(), host == "www.youtube-nocookie.com" || host == "www.youtube.com"
        else { return false }
        return parts.percentEncodedPath.wholeMatch(of: /\/embed\/[A-Za-z0-9_-]{11}/) != nil
    }

    static func probeSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        configuration.timeoutIntervalForResource = 5
        return URLSession(configuration: configuration, delegate: RefuseRedirects(), delegateQueue: nil)
    }

    static func connectionIssue(at origin: URL, using existingSession: URLSession? = nil) async -> WorkspaceProblem? {
        let session = existingSession ?? probeSession()
        defer { if existingSession == nil { session.finishTasksAndInvalidate() } }
        let request = URLRequest(
            url: origin.appending(path: "manifest.webmanifest"),
            cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 4
        )
        do {
            let (data, response) = try await session.data(for: request)
            guard let response = response as? HTTPURLResponse else { return .unavailable }
            switch response.statusCode {
            case 200..<300: break
            case 403: return .denied
            case 404, 410: return .wrongAddress
            default: return .unavailable
            }
            guard response.mimeType == "application/manifest+json",
                  let manifest = try? JSONDecoder().decode(WorkspaceManifest.self, from: data),
                  manifest.name == "Heinzelmännchen", manifest.short_name == "hz" else { return .wrongAddress }
            return nil
        } catch {
            return WorkspaceProblem(navigationError: error)
        }
    }

    private struct WorkspaceManifest: Decodable {
        let name: String
        let short_name: String
    }

    func verify() async throws {
        if token != nil { _ = try await readLifecycle() }
    }

    func readLifecycle() async throws -> Lifecycle {
        let (body, status) = try await readAuthenticatedAPI("api/lifecycle")
        guard status == 200,
              let lifecycle = try? JSONDecoder().decode(Lifecycle.self, from: body),
              !lifecycle.instance.isEmpty, !lifecycle.version.isEmpty else {
            throw ConnectionError.authentication
        }
        return lifecycle
    }

    #if os(macOS)
    func sharedSignInOrigin() async throws -> Endpoint? {
        guard origin.scheme == "http", let token else { return nil }
        let (body, status) = try await readAuthenticatedAPI("api/tailscale/status")
        guard status == 200, let sharing = try? JSONDecoder().decode(Sharing.self, from: body),
              sharing.state == "ready", let address = sharing.origin,
              let target = Endpoint(address), target.token == nil, target.origin.scheme == "https",
              target.origin.host()?.hasSuffix(".ts.net") == true,
              let remote = Endpoint(target.origin.absoluteString + "/#token=" + token) else { return nil }
        // Serve can point at a different hz process on this Mac. Check the private
        // HTTPS endpoint accepts this exact sign-in before publishing it.
        async let localIdentity = readLifecycle()
        async let remoteIdentity = remote.readLifecycle()
        let (local, shared) = try await (localIdentity, remoteIdentity)
        guard local.instance == shared.instance else { throw ConnectionError.authentication }
        return target
    }

    /// Ask this Mac's host for a named device token with the session token (`POST /api/_web/device`, ADR 0046).
    /// The token survives restarts of the host; the host stores only its SHA-256.
    func mintDeviceToken(name: String) async throws -> String {
        let body = try JSONSerialization.data(withJSONObject: ["name": name])
        let (data, status) = try await readAuthenticatedAPI("api/_web/device", method: "POST", body: body)
        guard status == 200, let paired = try? JSONDecoder().decode(Paired.self, from: data),
              paired.token.hasPrefix("hzd_") else { throw ConnectionError.authentication }
        return paired.token
    }

    /// A device token forgets itself (`DELETE /api/_web/device`); best effort when switching sync off or rotating.
    func revokeDeviceToken() async {
        _ = try? await readAuthenticatedAPI("api/_web/device", method: "DELETE")
    }

    private struct Paired: Decodable {
        let token: String
    }

    private struct Sharing: Decodable {
        let state: String
        let origin: String?
    }
    #endif

    private func readAuthenticatedAPI(_ route: String, method: String = "GET", body: Data? = nil) async throws -> (Data, Int) {
        guard let token else { throw ConnectionError.authentication }
        let configuration = URLSessionConfiguration.ephemeral
        #if os(macOS)
        if origin.scheme == "http" {
            // The launch token belongs only to the owned loopback listener.
            configuration.connectionProxyDictionary = [
                kCFNetworkProxiesHTTPEnable as String: 0,
                kCFNetworkProxiesHTTPSEnable as String: 0,
                kCFNetworkProxiesSOCKSEnable as String: 0,
                kCFNetworkProxiesProxyAutoConfigEnable as String: 0,
                kCFNetworkProxiesProxyAutoDiscoveryEnable as String: 0,
            ]
        }
        #endif
        let session = URLSession(configuration: configuration, delegate: RefuseRedirects(), delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        var request = URLRequest(
            url: origin.appending(path: route), cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 6
        )
        request.httpMethod = method
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (body, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw ConnectionError.authentication }
        return (body, response.statusCode)
    }

    struct Lifecycle: Decodable {
        let instance: String
        let version: String
        let restartable: Bool?
    }
}

enum WorkspaceProblem {
    case offline, unavailable, slow, signIn, denied, wrongAddress, insecure, interrupted

    init(navigationError: Error) {
        switch (navigationError as? URLError)?.code {
        case .notConnectedToInternet: self = .offline
        case .serverCertificateHasBadDate, .serverCertificateUntrusted, .serverCertificateHasUnknownRoot,
             .serverCertificateNotYetValid, .secureConnectionFailed: self = .insecure
        case .userAuthenticationRequired: self = .signIn
        case .timedOut: self = .slow
        default: self = .unavailable
        }
    }

    init?(response status: Int) {
        switch status {
        case 401: self = .signIn
        case 403: self = .denied
        case 404, 410: self = .wrongAddress
        case 400..<600: self = .unavailable
        default: return nil
        }
    }

    var title: String {
        switch self {
        case .offline: "You’re offline"
        case .unavailable: "Workspace out of reach"
        case .slow: "Still trying to connect"
        case .signIn: "Sign-in required"
        case .denied: "This workspace denied access"
        case .wrongAddress: "No Heinzel workspace at this address"
        case .insecure: "Secure connection unavailable"
        case .interrupted: "The workspace closed unexpectedly"
        }
    }

    var detail: String {
        switch self {
        case .offline: "Connect to Wi-Fi or mobile data. Heinzel will try again when your network returns."
        case .unavailable: "Check that the workspace host is running. Heinzel will try again."
        case .slow: "Heinzel is waiting for the workspace to answer. You can try again now or choose another workspace."
        case .signIn: "Ask the host for a fresh private sign-in link, then paste it into Heinzel."
        case .denied: "Check that you have access to the host’s private network, or ask the host for help."
        case .wrongAddress: "Ask the host to share the workspace address again."
        case .insecure: "Ask the host to check the workspace’s HTTPS certificate."
        case .interrupted: "Open the workspace again to continue."
        }
    }

    var icon: String {
        switch self {
        case .offline: "wifi.slash"
        case .unavailable, .interrupted: "exclamationmark.circle"
        case .slow: "clock"
        case .signIn: "person.crop.circle.badge.exclamationmark"
        case .denied, .insecure: "lock"
        case .wrongAddress: "link"
        }
    }

    var canReconnectAutomatically: Bool {
        self == .offline || self == .unavailable || self == .slow
    }

    var shouldEnterLink: Bool { self == .signIn || self == .wrongAddress }
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
    case authentication, missingCLI, managedInstall, startup, stopped, outdatedCLI

    var errorDescription: String? {
        switch self {
        case .authentication: "Heinzel did not accept the sign-in link. Copy a fresh link from the host and try again."
        case .missingCLI: "Install Heinzel with uvx heinzel@latest, or connect to an existing server."
        case .managedInstall: "The installed Heinzel service is unavailable or in use. Close other Heinzel apps and try again. If the problem persists, run hz app install --flavor=swift."
        case .startup: "The local Heinzel service could not start. Run hz ui attach in Terminal to see why."
        case .stopped: "The local Heinzel service stopped. Start it again to return to the workspace."
        case .outdatedCLI: "This hz is too old for the app. Run hz system update in Terminal, then open the app again."
        }
    }
}


enum ExternalLink {
    static func destination(_ input: String) -> URL? {
        guard input.utf8.count <= 8192, !hasControls(input),
              let parts = URLComponents(string: input), let url = parts.url,
              parts.user == nil, parts.password == nil else { return nil }

        if parts.scheme == "mailto" {
            guard input.utf8.count <= 2000, parts.host == nil, parts.fragment == nil,
                  validAddresses(parts.path) else { return nil }
            var seen = Set<String>()
            for item in parts.queryItems ?? [] {
                let key = item.name.lowercased()
                guard ["subject", "body", "cc", "bcc"].contains(key), seen.insert(key).inserted,
                      let value = item.value, !hasControls(value) else { return nil }
                if ["cc", "bcc"].contains(key), !validAddresses(value) { return nil }
            }
            return url
        }

        guard parts.scheme == "http" || parts.scheme == "https",
              let host = url.host(),
              BrowserAddress.host(host + ":443") != nil || BrowserAddress.publicEndpoint(host) != nil else { return nil }
        return url
    }

    private static func hasControls(_ value: String) -> Bool {
        value.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
    }

    private static func validAddresses(_ value: String) -> Bool {
        value.split(separator: ",", omittingEmptySubsequences: false).allSatisfy {
            $0.wholeMatch(of: /[A-Za-z0-9.!#$%&'*+\/=?^_`{|}~-]+@[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*\.[A-Za-z0-9]+/) != nil
        } && !value.isEmpty
    }
}

/// A workspace may hand one public URL to the system after recent trusted input.
struct ExternalLinkHandoff {
    private var activatedAt: ContinuousClock.Instant?
    private var openedAt: ContinuousClock.Instant?

    mutating func activate(at time: ContinuousClock.Instant = .now) { activatedAt = time }

    mutating func clearActivation() { activatedAt = nil }

    mutating func consumeActivation(at time: ContinuousClock.Instant = .now) -> Bool {
        let activation = activatedAt
        clearActivation()
        return activation.map { $0.duration(to: time) <= .seconds(1) } ?? false
    }

    mutating func take(
        _ value: String, from endpoint: Endpoint, needsActivation: Bool, at time: ContinuousClock.Instant = .now
    ) -> URL? {
        guard let target = ExternalLink.destination(value), !endpoint.owns(target) else { return nil }
        let activated = consumeActivation(at: time)
        if needsActivation, !activated { return nil }
        if let openedAt, openedAt.duration(to: time) < .seconds(2) { return nil }
        openedAt = time
        return target
    }
}
