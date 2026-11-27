import CoreFoundation
import CoreGraphics
import Foundation
import Security

enum WorkspaceFooterGeometry {
    static func deliveryIsCurrent(revision: Int, currentRevision: Int,
                                  requested: (CGFloat, CGFloat, Int), current: (CGFloat, CGFloat, Int)) -> Bool {
        revision == currentRevision && requested == current
    }

    /// The page can only reserve the current viewport's bottom-left footer slot.
    static func slot(_ values: [String: NSNumber], viewport: CGSize, width expectedWidth: CGFloat, bottom: CGFloat) -> CGRect? {
        let keys = ["x", "y", "width", "height", "viewportWidth", "viewportHeight"]
        guard keys.allSatisfy({ values[$0].map { CFGetTypeID($0) != CFBooleanGetTypeID() } == true }),
              let x = values["x"]?.doubleValue, let y = values["y"]?.doubleValue,
              let width = values["width"]?.doubleValue, let height = values["height"]?.doubleValue,
              let viewportWidth = values["viewportWidth"]?.doubleValue,
              let viewportHeight = values["viewportHeight"]?.doubleValue,
              [x, y, width, height, viewportWidth, viewportHeight, expectedWidth, bottom].allSatisfy(\.isFinite),
              abs(viewportWidth - Double(viewport.width)) <= 2, abs(viewportHeight - Double(viewport.height)) <= 2,
              x >= 0, x <= 80, width >= 44, width <= 180, abs(width - Double(expectedWidth)) <= 2,
              height >= 44, height <= 52, x + width <= viewportWidth,
              y >= 0, abs(y + height + Double(bottom) - viewportHeight) <= 3 else { return nil }
        return CGRect(origin: CGPoint(x: x, y: y), size: CGSize(width: width, height: height))
    }
}

/// What the native app tells every workspace it connects to, so the workspace can list its clients. The web view cannot
/// add a header to page requests, so the text rides in the user agent as `HeinzelClient(...)`; the server reads both.
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

    static func userAgentToken(appVersion: String?, backendVersion: String?, machine: String?) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: ".-_+"))
        func field(_ value: String?) -> String {
            guard let value, !value.isEmpty else { return "unknown" }
            return String(value.prefix(64)).addingPercentEncoding(withAllowedCharacters: allowed) ?? "unknown"
        }
        return "HeinzelClient(flavor=native;app=\(field(appVersion));backend=\(field(backendVersion));machine=\(field(machine)))"
    }
}

struct AppBuildObservation: Codable, Equatable {
    struct Build: Codable, Equatable {
        let version: String
        let number: String
        let revision: String?

        init?(version: String?, number: String?, revision: String?) {
            guard let version, !version.isEmpty, let number, !number.isEmpty else { return nil }
            self.version = version
            self.number = number
            self.revision = revision.flatMap { $0.range(of: #"^[a-f0-9]{7,64}$"#, options: .regularExpression) == nil ? nil : $0 }
        }
    }

    let build: Build
    let observedSince: Date

    static func recording(_ build: Build, previous: Self?, now: Date) -> Self {
        if let previous, previous.build == build, previous.observedSince <= now { return previous }
        return Self(build: build, observedSince: now)
    }
}

struct WorkspaceVersionObservation: Codable, Equatable {
    let version: String
    let observedAt: Date
    var installedVersion: String? = nil
    var installedUpdatedAt: Date? = nil
    var daemonLastVersion: String? = nil
    var daemonLastObservedAt: Date? = nil
    var daemonObservationStale: Bool? = nil

    static func fromPayload(_ data: [String: Any], now: Date) -> Self? {
        func text(_ value: Any?) -> String? {
            guard let value = value as? String, !value.isEmpty, value.count <= 64,
                  value.rangeOfCharacter(from: .controlCharacters) == nil else { return nil }
            return value
        }
        func date(_ value: Any?) -> Date? {
            guard let value = value as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
                  value.doubleValue.isFinite, value.doubleValue > 0 else { return nil }
            let date = Date(timeIntervalSince1970: value.doubleValue / 1000)
            return date <= now ? date : nil
        }
        guard let version = text(data["version"]) else { return nil }
        var observation = Self(version: version, observedAt: now)
        observation.installedVersion = text(data["installedVersion"])
        observation.installedUpdatedAt = observation.installedVersion == nil ? nil : date(data["installedUpdatedAt"])
        observation.daemonLastVersion = text(data["daemonLastVersion"])
        observation.daemonLastObservedAt = date(data["daemonLastObservedAt"])
        observation.daemonObservationStale = data["daemonObservationStale"] as? Bool
        return observation
    }

    static func record(_ observation: Self, origin: String, selectedOrigin: String,
                       requestRevision: Int, currentRevision: Int, into values: inout [String: Self]) -> Bool {
        guard origin == selectedOrigin, requestRevision == currentRevision else { return false }
        values[origin] = observation
        return true
    }
}

/// One bounded metadata lookup at a time; failed or superseded requests never relabel cached observations.
@MainActor
final class WorkspaceVersionRefresh {
    private var lastRevision = 0
    private var generation = 0
    private var task: Task<Void, Never>?

    func cancel() {
        generation &+= 1
        task?.cancel()
        task = nil
    }

    @discardableResult
    func request(revision: Int? = nil,
                 read: @escaping @MainActor () async -> WorkspaceVersionObservation?,
                 isCurrent: @escaping @MainActor () -> Bool,
                 report: @escaping @MainActor (WorkspaceVersionObservation) -> Void) -> Task<Void, Never>? {
        if let revision {
            guard revision != lastRevision else { return nil }
            lastRevision = revision
        }
        cancel()
        let requestedGeneration = generation
        let pending = Task { [weak self] in
            guard !Task.isCancelled, isCurrent() else { return }
            let observation = await read()
            guard !Task.isCancelled, self?.generation == requestedGeneration, isCurrent(),
                  let observation else { return }
            report(observation)
        }
        task = pending
        return pending
    }
}

struct RecentWorkspace: Codable, Identifiable {
    let address: String
    var hostName: String?
    var lastUsed: Date
    var source: Source?

    enum Source: String, Codable {
        case pasted, iCloud
    }

    var id: String { address }

    var name: String { Self.displayName(for: address, hostName: hostName) }

    static func displayName(for address: String, hostName: String? = nil) -> String {
        let host = hostName ?? URL(string: address)?.host() ?? address
        if host.hasSuffix(".ts.net") || host.hasSuffix(".local") {
            let firstLabel = host.split(separator: ".").first ?? Substring(host)
            return firstLabel.split(separator: "-").map { word in
                word.lowercased() == "macbook" ? "MacBook" : String(word).localizedCapitalized
            }.joined(separator: " ")
        }
        return host
    }
}

/// The "Sync Sign-in with iCloud" switch. A missing value means on; only an explicit stored false stays off.
enum CloudSyncPreference {
    static let key = "syncWorkspaceSignIn"

    static func isEnabled(_ defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: key) as? Bool ?? true
    }

    static func set(_ enabled: Bool, in defaults: UserDefaults = .standard) {
        defaults.set(enabled, forKey: key)
    }
}

/// What the publishing Mac does with the iCloud item it already wrote.
enum CloudSignInPlan {
    enum Action: Equatable { case keep, mint }

    /// The iCloud item carries a named device token (`hzd_`), never the per-process session token; it is renewed
    /// after `rotateAfter` and whenever the host refused it.
    static let rotateAfter: TimeInterval = 14 * 24 * 3600
    static let deviceName = "iCloud sign-in"

    static func decide(existingToken: String?, publishedAt: Date?, hostRefusedIt: Bool, now: Date = .now) -> Action {
        guard let existingToken, existingToken.hasPrefix("hzd_"), let publishedAt, !hostRefusedIt else { return .mint }
        return now.timeIntervalSince(publishedAt) >= rotateAfter ? .mint : .keep
    }
}

private struct SyncedWorkspace: Codable {
    let workspace: RecentWorkspace
    let token: String
}

enum WorkspaceKeychain {
    private static let service = "team.heinzel.apple.workspace-sign-in.v1"

    private static func query(address: String? = nil) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrSynchronizable as String: true
        ]
        #if os(macOS)
        query[kSecUseDataProtectionKeychain as String] = true
        #endif
        if let address { query[kSecAttrAccount as String] = address }
        return query
    }

    private static func entry(address: String) -> SyncedWorkspace? {
        var query = query(address: address)
        query[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(SyncedWorkspace.self, from: data)
    }

    // nil means Keychain could not answer. Only an authoritative empty result clears the directory.
    static func workspaces() -> [RecentWorkspace]? {
        var query = query()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitAll
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return [] }
        guard status == errSecSuccess, let data = result as? [Data] else { return nil }
        let workspaces = data.compactMap { try? JSONDecoder().decode(SyncedWorkspace.self, from: $0).workspace }
        return workspaces.count == data.count ? workspaces : nil
    }

    static func signIn(address: String, newerThan forgotten: Date?) -> (link: String, workspace: RecentWorkspace)? {
        guard let entry = entry(address: address), entry.workspace.lastUsed > (forgotten ?? .distantPast),
              let url = URL(string: address),
              var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        components.fragment = "token=\(entry.token)"
        guard let value = components.url.flatMap({ Endpoint($0.absoluteString)?.url.absoluteString }) else { return nil }
        return (value, entry.workspace)
    }

    /// The token and publication time of the item this Mac wrote for `address`, if any.
    static func published(address: String) -> (token: String, publishedAt: Date)? {
        entry(address: address).map { ($0.token, $0.workspace.lastUsed) }
    }

    static func publish(workspace: RecentWorkspace, token: String) -> Bool {
        let query = query(address: workspace.address)
        if let previous = entry(address: workspace.address), previous.token == token { return true }
        guard let data = try? JSONEncoder().encode(SyncedWorkspace(workspace: workspace, token: token)) else { return false }
        // AfterFirstUnlock: a headless Mac that rebooted and was never unlocked at the screen still publishes.
        let update: [String: Any] = [kSecValueData as String: data,
                                     kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock]
        switch SecItemUpdate(query as CFDictionary, update as CFDictionary) {
        case errSecSuccess: return true
        case errSecItemNotFound:
            var item = query
            item[kSecValueData as String] = data
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
        default: return false
        }
    }

    @discardableResult
    static func remove(address: String) -> Bool {
        let result = SecItemDelete(query(address: address) as CFDictionary)
        return result == errSecSuccess || result == errSecItemNotFound
    }
}
