import Foundation
import Synchronization
#if os(macOS)
import CFNetwork
#endif

enum ClientIdentity {
    private final class Backend: @unchecked Sendable {
        private let lock = NSLock()
        private var version: String?
        func get() -> String? { lock.lock(); defer { lock.unlock() }; return version }
        func set(_ value: String?) { lock.lock(); defer { lock.unlock() }; version = value }
    }
    private static let backend = Backend()

    /// The version of the hz this app started or attached to, read from its lifecycle; nil until it answered.
    static var backendVersion: String? {
        get { backend.get() }
        set { backend.set(newValue) }
    }

    static func header(appVersion: String?, backendVersion: String?, machine: String?) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: ".-_+"))
        func field(_ value: String?) -> String {
            guard let value, !value.isEmpty else { return "unknown" }
            return String(String.UnicodeScalarView(value.unicodeScalars.prefix(64))).addingPercentEncoding(withAllowedCharacters: allowed) ?? "unknown"
        }
        return "flavor=native;app=\(field(appVersion));backend=\(field(backendVersion));machine=\(field(machine))"
    }
}

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

    func displayAddress(observedIP: String? = nil) -> String {
        let host = observedIP ?? origin.host(percentEncoded: false) ?? origin.absoluteString
        let formatted = host.contains(":") && !host.hasPrefix("[") ? "[" + host + "]" : host
        return origin.port.map { formatted + ":" + String($0) } ?? formatted
    }

    /// Add and Sign In require a private URL. Address-only links retain their separate open flow.
    static func signInInputIssue(_ value: String, expectedOrigin: URL?) -> String? {
        let input = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else { return nil }
        guard let target = Endpoint(input) else { return "Invalid link. Paste the full private HTTPS sign-in URL from the host." }
        if let expectedOrigin, !target.owns(expectedOrigin) { return "This sign-in URL is for another workspace." }
        return target.token == nil ? "Ask the host for a private sign-in URL that includes access." : nil
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
        await probeHost(at: origin, using: existingSession).issue
    }

    static func probeHost(at origin: URL, using existingSession: URLSession? = nil) async -> WorkspaceHostProbe {
        let session = existingSession ?? probeSession()
        defer { if existingSession == nil { session.finishTasksAndInvalidate() } }
        let request = URLRequest(
            url: origin.appending(path: "manifest.webmanifest"),
            cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 4
        )
        do {
            let route = RefuseRedirects()
            let (data, response) = try await session.data(for: request, delegate: route)
            let path = route.connectionAddresses.withLock { $0 }
            guard let response = response as? HTTPURLResponse else { return WorkspaceHostProbe(.unknown, .unavailable) }
            switch response.statusCode {
            case 200..<300: break
            case 403: return WorkspaceHostProbe(.online, .denied, address: path.backend, clientAddress: path.client)
            case 404, 410: return WorkspaceHostProbe(.unknown, .wrongAddress, address: path.backend, clientAddress: path.client)
            default: return WorkspaceHostProbe(.online, .serviceUnavailable, address: path.backend, clientAddress: path.client)
            }
            guard response.mimeType == "application/manifest+json",
                  let manifest = try? JSONDecoder().decode(WorkspaceManifest.self, from: data),
                  manifest.name == "Heinzelmännchen", manifest.short_name == "hz" else { return WorkspaceHostProbe(.unknown, .wrongAddress, address: path.backend, clientAddress: path.client) }
            return WorkspaceHostProbe(.online, nil, address: path.backend, clientAddress: path.client)
        } catch {
            return WorkspaceHostProbe(error: error)
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
        // Only a rejected credential should start sign-in recovery or renew an iCloud token.
        if status == 401 { throw ConnectionError.authentication }
        if status == 403 { throw ConnectionError.accessDenied }
        guard status == 200,
              let lifecycle = try? JSONDecoder().decode(Lifecycle.self, from: body),
              !lifecycle.instance.isEmpty, !lifecycle.version.isEmpty else {
            throw ConnectionError.unverifiedWorkspace
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
        guard let response = response as? HTTPURLResponse else { throw ConnectionError.unverifiedWorkspace }
        return (body, response.statusCode)
    }

    struct Lifecycle: Decodable {
        let instance: String
        let version: String
        let restartable: Bool?
    }
}

struct WorkspaceHostProbe {
    enum Reachability: String { case online = "Online", offline = "Offline", unknown = "Unknown" }
    let reachability: Reachability
    let issue: WorkspaceProblem?
    let address: String?
    let clientAddress: String?

    init(_ reachability: Reachability, _ issue: WorkspaceProblem?, address: String? = nil, clientAddress: String? = nil) {
        self.reachability = reachability
        self.clientAddress = clientAddress
        self.address = address
        self.issue = issue
    }

    init(error: Error) {
        address = nil
        clientAddress = nil
        let code = (error as? URLError)?.code
        switch code {
        case .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed, .timedOut, .networkConnectionLost:
            reachability = .offline
        default:
            reachability = .unknown
        }
        issue = WorkspaceProblem(navigationError: error)
    }

    /// Results are discarded on every path change. A response on this path is stronger
    /// evidence for its origin than the system's general internet route.
    static func routeAvailable(networkAvailable: Bool?, probe: Self?) -> Bool? {
        probe?.reachability == .online ? true : networkAvailable
    }

    static func canAttemptConnection(networkAvailable: Bool?, probe: Self?) -> Bool {
        routeAvailable(networkAvailable: networkAvailable, probe: probe) != false
            && remoteReachability(networkAvailable: networkAvailable, probe: probe) != .offline
    }

    static func remoteReachability(networkAvailable: Bool?, probe: Self?) -> Reachability {
        routeAvailable(networkAvailable: networkAvailable, probe: probe) == true ? probe?.reachability ?? .unknown : .unknown
    }
}

struct WorkspaceRemoteRecovery {
    var issue: WorkspaceProblem
    private(set) var paused = false
    private var interval = 0

    init(issue: WorkspaceProblem) { self.issue = issue }

    mutating func togglePause() {
        paused.toggle()
        interval = 0
    }

    mutating func nextDelay(jitter: Double) -> Double {
        defer { interval = min(interval + 1, 4) }
        return min(40, [0.0, 5, 10, 20, 40][interval] * jitter)
    }

    func label(network: Bool?) -> String {
        if paused { return "Paused" }
        switch issue {
        case .signIn: return "Sign in"
        case .denied: return "Access denied"
        case .wrongAddress: return "Check workspace address"
        case .insecure: return "Secure connection unavailable"
        default:
            if network == false { return "Waiting for network" }
            return issue == .serviceUnavailable ? "Unavailable · Reconnecting…" : "Reconnecting…"
        }
    }
}

enum WorkspaceProblem {
    case offline, unavailable, serviceUnavailable, slow, signIn, denied, wrongAddress, insecure, interrupted

    var revokesAccess: Bool { self == .signIn || self == .denied }

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
        case 500..<600: self = .serviceUnavailable
        case 400..<500: self = .unavailable
        default: return nil
        }
    }

    var title: String {
        switch self {
        case .offline: "You’re offline"
        case .unavailable: "Workspace out of reach"
        case .serviceUnavailable: "Workspace service unavailable"
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
        case .serviceUnavailable: "The host answered. Try the workspace again shortly."
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
        case .unavailable, .serviceUnavailable, .interrupted: "exclamationmark.circle"
        case .slow: "clock"
        case .signIn: "person.crop.circle.badge.exclamationmark"
        case .denied, .insecure: "lock"
        case .wrongAddress: "link"
        }
    }

    var canReconnectAutomatically: Bool {
        self == .offline || self == .unavailable || self == .serviceUnavailable || self == .slow
    }

    var shouldEnterLink: Bool { self == .signIn || self == .wrongAddress }
}

final class RefuseRedirects: NSObject, URLSessionTaskDelegate {
    let connectionAddresses = Mutex<(client: String?, backend: String?)>((nil, nil))

    func urlSession(_ session: URLSession, task: URLSessionTask, didFinishCollecting metrics: URLSessionTaskMetrics) {
        guard let hop = metrics.transactionMetrics.last else { return }
        connectionAddresses.withLock { $0 = (hop.localAddress, hop.isProxyConnection ? nil : hop.remoteAddress) }
    }

    func urlSession(
        _ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

enum ConnectionError: LocalizedError {
    case authentication, accessDenied, unverifiedWorkspace, missingCLI, managedInstall, startup, stopped, outdatedCLI

    var errorDescription: String? {
        switch self {
        case .authentication: "Heinzel did not accept the sign-in link. Copy a fresh link from the host and try again."
        case .accessDenied: WorkspaceProblem.denied.detail
        case .unverifiedWorkspace: "The workspace could not confirm its status. Try connecting again."
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
