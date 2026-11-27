import CoreFoundation
import CoreGraphics
import Foundation
import Network
import Security

enum WorkspaceFooterGeometry {
    /// Until the web slot is accepted, the native switcher and safe area sit outside the web view.
    static func webBottomInset(safeArea: CGFloat, hasSlot: Bool) -> CGFloat {
        hasSlot ? safeArea : 0
    }

    static func deliveryIsCurrent(revision: Int, currentRevision: Int,
                                  requested: (CGFloat, CGFloat, Int), current: (CGFloat, CGFloat, Int)) -> Bool {
        revision == currentRevision && requested == current
    }

    /// Only a report for the current document and layout may replace or clear ownership.
    static func receive(_ values: [String: Any], viewport: CGSize, width: CGFloat, bottom: CGFloat,
                        revision: Int, generation: Int, report: (CGRect?) -> Void) -> Bool {
        func matches(_ key: String, _ expected: Int) -> Bool {
            guard let value = values[key] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID() else { return false }
            return value.doubleValue == Double(expected)
        }
        guard matches("revision", revision), matches("generation", generation) else { return false }
        let accepted = (values as? [String: NSNumber]).flatMap { slot($0, viewport: viewport, width: width, bottom: bottom) }
        report(accepted)
        return accepted != nil
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
    var backendObservedAt: Date?
    var backendIP: String?
    var webObservedSince: Date?
    var webRunningSince: Date?
    var daemonRunningVersion: String?
    var daemonRunningSince: Date?
    var daemonRegisteredAt: Date?
    var daemonLoaded: Bool?
    var daemonOwned: Bool?
    var daemonStatusObservedAt: Date?

    var differentInstalledVersion: String? {
        guard let installed = installedVersion?.trimmingCharacters(in: .whitespacesAndNewlines),
              !installed.isEmpty, installed.lowercased() != "unknown", installed != version else { return nil }
        return installed
    }

    /// Both timestamps came from the answering backend in one authenticated response.
    var uptimeAtObservation: TimeInterval? {
        guard let backendObservedAt, let webRunningSince, webRunningSince <= backendObservedAt else { return nil }
        return backendObservedAt.timeIntervalSince(webRunningSince)
    }

    static func fromPayload(_ data: [String: Any], now: Date) -> Self? {
        func text(_ value: Any?) -> String? {
            guard let value = value as? String, !value.isEmpty, value.count <= 64,
                  value.rangeOfCharacter(from: .controlCharacters) == nil else { return nil }
            return value
        }
        func timestamp(_ value: Any?) -> Date? {
            guard let value = value as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
                  (1...253_402_300_799_999).contains(value.doubleValue) else { return nil }
            return Date(timeIntervalSince1970: value.doubleValue / 1000)
        }
        func flag(_ key: String) -> Bool? {
            guard let number = data[key] as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
            return number.boolValue
        }
        guard let version = text(data["version"]) else { return nil }
        var observation = Self(version: version, observedAt: now)
        let reported = timestamp(data["backendObservedAt"])
        func date(_ value: Any?) -> Date? {
            guard let date = timestamp(value), date <= (reported ?? now) else { return nil }
            return date
        }
        observation.backendObservedAt = reported
        if let ip = text(data["backendIP"]), !ip.contains("%"),
           let bytes = IPv4Address(ip)?.rawValue ?? IPv6Address(ip)?.rawValue,
           bytes.contains(where: { $0 != 0 }) {
            observation.backendIP = ip
        }
        observation.webObservedSince = date(data["webObservedSince"])
        observation.webRunningSince = date(data["webRunningSince"])
        observation.installedVersion = text(data["installedVersion"])
        observation.installedUpdatedAt = observation.installedVersion == nil ? nil : date(data["installedUpdatedAt"])
        observation.daemonLastVersion = text(data["daemonLastVersion"])
        observation.daemonLastObservedAt = date(data["daemonLastObservedAt"])
        observation.daemonObservationStale = flag("daemonObservationStale")
        observation.daemonRunningVersion = text(data["daemonRunningVersion"])
        observation.daemonRunningSince = date(data["daemonRunningSince"])
        observation.daemonRegisteredAt = date(data["daemonRegisteredAt"])
        observation.daemonLoaded = flag("daemonLoaded")
        observation.daemonOwned = flag("daemonOwned")
        // The backend obtains daemon status after it timestamps the start of this request.
        observation.daemonStatusObservedAt = timestamp(data["daemonStatusObservedAt"])
        return observation
    }

    static func lastLocal(in observations: [String: Self]) -> Self? {
        observations.compactMap { address, observation in
            guard let url = URL(string: address), url.scheme == "http", url.host == "127.0.0.1" else { return nil }
            return observation
        }.max { $0.observedAt < $1.observedAt }
    }

    static func record(_ observation: Self, origin: String, selectedOrigin: String,
                       requestRevision: Int, currentRevision: Int, into values: inout [String: Self]) -> Bool {
        guard origin == selectedOrigin, requestRevision == currentRevision else { return false }
        var cached = observation
        // The socket address describes this request, and the directory only needs historical versions.
        cached.backendIP = nil
        values[origin] = cached
        return true
    }
}

/// One bounded metadata lookup at a time; failed or superseded requests never relabel cached observations.
@MainActor
final class WorkspaceVersionRefresh {
    private(set) var lastRevision = 0
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

    var isUserAdded: Bool { source == .pasted }

    var name: String { Self.displayName(for: address, hostName: hostName) }

    /// Distinct HTTPS origins can share both their display name and their resolved address.
    static func disambiguatingAddresses(for rows: [Self], probes: [URL: WorkspaceHostProbe]) -> [String: String] {
        var labels: [String: String] = [:]
        for group in Dictionary(grouping: rows, by: { $0.name.lowercased() }).values where group.count > 1 {
            let origins = group.compactMap { Endpoint($0.address) }
            let addresses = origins.map { $0.displayAddress(observedIP: probes[$0.origin]?.address) }
            let useHostNames = Set(addresses).count != addresses.count
            for (endpoint, address) in zip(origins, addresses) {
                labels[endpoint.origin.absoluteString] = useHostNames ? endpoint.displayAddress() : address
            }
        }
        return labels
    }

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

/// A failed synchronizable Keychain read leaves the last readable names available for recovery.
struct WorkspaceCloudDirectory {
    enum Access { case checking, unavailable, empty, available }
    private(set) var access: Access = .checking
    private(set) var workspaces: [RecentWorkspace] = []

    init(_ read: [RecentWorkspace]?) { update(read) }
    init() {}

    mutating func update(_ read: [RecentWorkspace]?) {
        guard let read else { access = .unavailable; return }
        workspaces = read
        access = read.isEmpty ? .empty : .available
    }

    mutating func observedSignIn(_ workspace: RecentWorkspace) {
        workspaces.removeAll { $0.address == workspace.address }
        workspaces.append(workspace)
        // Reading one credential doesn't establish that the full directory is readable.
        if access == .empty { access = .available }
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
