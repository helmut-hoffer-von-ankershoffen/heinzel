import CryptoKit
import Foundation
import SwiftUI
import WidgetKit

/// Delivered rows are a lower bound when the advertised total is stale.
struct WidgetRosterOverflow {
    let total: Int
    let remaining: Int

    init(deliveredCount: Int, totalCount: Int?, visibleCount: Int) {
        total = max(deliveredCount, totalCount ?? 0)
        remaining = total - visibleCount
    }
}

/// Bounded columns for the Mac extra-large roster, retaining the snapshot's priority order.
struct WidgetRosterColumns {
    let columns: [[WidgetHeinzel]]
    let remaining: Int

    init(items: [WidgetHeinzel], totalCount: Int?, rowsPerColumn: Int) {
        let rows = min(6, max(1, rowsPerColumn))
        let visible = Array(items.prefix(rows * 2))
        columns = stride(from: 0, to: visible.count, by: rows).map {
            Array(visible[$0..<min($0 + rows, visible.count)])
        }
        remaining = WidgetRosterOverflow(deliveredCount: items.count, totalCount: totalCount,
                                         visibleCount: visible.count).remaining
    }
}

struct WidgetHeinzel: Codable, Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let state: String
    let activity: String?
    let asks: Int?
    let lastApproved: Double?
    let nextWake: Double?
    let portrait: Data?

    var portraitImage: Image? {
        guard let portrait else { return nil }
        #if os(macOS)
        return NSImage(data: portrait).map(Image.init(nsImage:))
        #else
        return UIImage(data: portrait).map(Image.init(uiImage:))
        #endif
    }

    /// Attention first, then work in progress; lifecycle state wins over stale activity.
    var widgetPriority: Int {
        if state == "retired" { return 3 }
        switch activity {
        case "waiting_for_you", "stuck", "overloaded": return 0
        case "working": return 1
        default: return 2
        }
    }

    var status: String {
        if state == "retired" { return "Retired" }
        if state == "proposed", activity == nil || activity == "idle" { return "New" }
        switch activity {
        case "paused": return "Paused"
        case "stuck": return "Stuck"
        case "waiting_for_you": return "Waiting for you"
        case "overloaded": return "Overloaded"
        case "working": return "Working"
        case "waiting_on_others": return "Waiting on others"
        case "proud": return "Done and proud"
        case "asleep": return "Asleep"
        case "idle": return "Idle"
        default: return state.capitalized
        }
    }
}

extension CatalogHeinzel {
    var widgetHeinzel: WidgetHeinzel {
        WidgetHeinzel(id: id, name: name, state: state, activity: activity, asks: asks,
                      lastApproved: lastApproved, nextWake: nil, portrait: nil)
    }
}

struct ActivityWidgetData: Codable, Equatable {
    static let changed = Notification.Name("HeinzelWidgetSnapshotChanged")
    static let clearFailed = Notification.Name("HeinzelWidgetClearFailed")
    static let kind = "HeinzelActivity"
    static let pinnedKind = "HeinzelPinned"
    static let rosterKind = "HeinzelRoster"
    static let freshness: TimeInterval = 300
    @MainActor private(set) static var invalidation = 0
    private static var group: String? {
        #if os(macOS)
        // A team-prefixed group allows the signed desktop app and extension to share
        // snapshots without adding a provisioning-profile dependency to the Mac installer.
        guard let group = Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetGroup") as? String,
              group.wholeMatch(of: /[A-Z0-9]{10}\.team\.heinzel\.apple\.activity/) != nil else { return nil }
        #else
        let group = "group.team.heinzel.apple.activity"
        #endif
        return group
    }
    private static var defaults: UserDefaults? { group.flatMap(UserDefaults.init(suiteName:)) }
    private static var catalogURL: URL? {
        guard let group, let directory = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group) else { return nil }
        return directory.appendingPathComponent("activity-catalog.sqlite")
    }
    private static var catalog: ActivityCatalog? { catalogURL.flatMap(ActivityCatalog.init(url:)) }
    static func sharedCatalogURL() -> URL? { catalogURL }
    static func backgroundCatalog() -> ActivityCatalog? { catalog }
    static func refreshToken() -> Data? { catalogURL.flatMap(ActivityCatalog.refreshToken(url:)) }
    static func legacyRefreshToken() -> Data? { catalogURL.flatMap(ActivityCatalog.legacyRefreshToken(url:)) }
    static func currentActivation() -> ActivityCatalog.Activation? { catalogURL.flatMap(ActivityCatalog.activation(url:)) }
    private static let detailsKey = "activityDetailRequests"

    let awake: Int
    let waiting: Int
    let updatedAt: Date
    var heinzels: [WidgetHeinzel]?
    var workspaceScope: String?
    var generation: String?
    var catalogUpdatedAt: Date? = nil
    var rosterCount: Int? = nil

    var expiresAt: Date { updatedAt.addingTimeInterval(Self.freshness) }

    func visibleInStatusMenu(scope: String?, at date: Date) -> Bool {
        guard let scope else { return false }
        return workspaceScope == scope && updatedAt <= date && date < expiresAt
    }

    var roster: [WidgetHeinzel] { heinzels ?? [] }
    var menuNeedsAttention: [WidgetHeinzel] { roster.filter { $0.widgetPriority == 0 } }

    func observationTime(for id: String) -> Date {
        roster.contains(where: { $0.id == id }) ? updatedAt : (catalogUpdatedAt ?? updatedAt)
    }

    static func scope(for origin: URL) -> String {
        #if os(macOS)
        // The local service chooses a fresh loopback port on each start.
        if origin.scheme == "http" { return scope(for: "heinzel:local-workspace") }
        #endif
        return scope(for: origin.absoluteString)
    }

    static func scope(for address: String) -> String {
        SHA256.hash(data: Data(address.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    static func currentScope(for origin: URL) -> String? {
        guard let catalogURL else { return nil }
        return ActivityCatalog.workspaceScope(url: catalogURL, endpoint: origin.absoluteString, legacy: scope(for: origin))
    }

    static func read() -> Self? {
        guard let data = catalog?.snapshot() else { return nil }
        return try? JSONDecoder().decode(Self.self, from: data)
    }

    static func reload() {
        NotificationCenter.default.post(name: changed, object: nil)
        for kind in [kind, pinnedKind, rosterKind] { WidgetCenter.shared.reloadTimelines(ofKind: kind) }
    }

    static func requestedDetails(scope: String) -> [String] {
        (defaults?.stringArray(forKey: detailsKey) ?? []).compactMap { value in
            let prefix = scope + "/"
            guard value.hasPrefix(prefix) else { return nil }
            let id = String(value.dropFirst(prefix.count))
            return NativeRoute.isSessionID(id) ? id : nil
        }
    }

    static func requestDetails(for id: String, scope: String) {
        guard NativeRoute.isSessionID(id), let defaults else { return }
        let current = defaults.stringArray(forKey: detailsKey) ?? []
        let key = scope + "/" + id
        guard !current.contains(key) else { return }
        defaults.set(Array(([key] + current).prefix(4)), forKey: detailsKey)
    }

    static func search(scope: String, generation: String? = nil, query: String = "", offset: Int = 0) -> [CatalogHeinzel] {
        guard let generation = generation ?? read()?.generation else { return [] }
        return catalog?.search(scope: scope, generation: generation, query: query, offset: offset) ?? []
    }

    static func find(scope: String, generation: String? = nil, id: String) -> CatalogHeinzel? {
        guard let generation = generation ?? read()?.generation else { return nil }
        return catalog?.find(scope: scope, generation: generation, id: id)
    }

    @discardableResult
    static func save(awake: Int, waiting: Int, heinzels: [WidgetHeinzel], entries: [CatalogHeinzel], workspaceScope: String, expectedToken: Data, now: Date = .now, catalogOverride: ActivityCatalog? = nil) -> Bool {
        guard awake >= 0, waiting >= 0, let catalog = catalogOverride ?? catalog else { return false }
        let generation = UUID().uuidString
        let snapshot = Self(awake: awake, waiting: waiting, updatedAt: now, heinzels: heinzels, workspaceScope: workspaceScope, generation: generation, catalogUpdatedAt: now)
        guard let data = try? JSONEncoder().encode(snapshot) else { return false }
        guard catalog.publish(scope: workspaceScope, generation: generation, snapshot: data, entries: entries, expectedToken: expectedToken) else { return false }
        if catalogOverride == nil { defaults?.removeObject(forKey: "activitySnapshot") }
        return true
    }

    @discardableResult
    @MainActor static func clear(expectedToken: Data? = nil) -> Bool {
        guard let catalogURL else {
            NSLog("Heinzel widget catalog could not be invalidated: shared container unavailable")
            NotificationCenter.default.post(name: clearFailed, object: nil)
            return false
        }
        let result = ActivityCatalog.clear(url: catalogURL, expectedToken: expectedToken)
        if result.stale { return false }
        didInvalidate()
        if !result.complete {
            NSLog("Heinzel widget catalog cleanup incomplete; revoked=%d purged=%d", result.revoked ? 1 : 0, result.purged ? 1 : 0)
            NotificationCenter.default.post(name: clearFailed, object: nil)
        }
        return result.complete
    }

    @MainActor static func didInvalidate() {
        invalidation &+= 1
        defaults?.removeObject(forKey: detailsKey)
        defaults?.removeObject(forKey: "activitySnapshot")
    }
}

/// Menu policy uses the same scoped snapshot as widgets; no credentials live here.
enum MacMenuStatus: String {
    case healthy, working, attention, offline, stale
    static func resolve(_ snapshot: ActivityWidgetData?, scope: String?, available: Bool, now: Date) -> Self {
        guard available, scope != nil else { return .offline }
        guard let snapshot, snapshot.visibleInStatusMenu(scope: scope, at: now) else { return .stale }
        if snapshot.waiting > 0 || snapshot.roster.contains(where: { $0.widgetPriority == 0 }) { return .attention }
        if snapshot.roster.contains(where: { $0.state != "retired" && $0.activity == "working" }) { return .working }
        return .healthy
    }
    var symbol: String {
        switch self {
        case .healthy: return "checkmark.circle"
        case .working: return "gearshape.2"
        case .attention: return "exclamationmark.circle.fill"
        case .offline: return "network.slash"
        case .stale: return "clock.badge.exclamationmark"
        }
    }
    var label: String {
        switch self {
        case .healthy: return "Heinzel: up to date"
        case .working: return "Heinzel: working"
        case .attention: return "Heinzel: needs attention"
        case .offline: return "Heinzel: offline"
        case .stale: return "Heinzel: status out of date"
        }
    }
    static func interval(open: Bool) -> TimeInterval { open ? 30 : 300 }
}

#if os(macOS)
/// Installed only by the authorized app host. The menu cannot enroll or read credentials.
@MainActor final class MacHostRefresh {
    typealias Schedule = (TimeInterval, @escaping @MainActor () async -> Void) -> (() -> Void)
    private let schedule: Schedule
    private var cancel: (() -> Void)?
    private var owner: ObjectIdentifier?
    private var revision = 0
    private var generation = 0
    private var inFlight = false
    private var refresh: (() async -> Void)?
    private var surfaces: Set<String> = []
    var didRefresh: (() -> Void)?

    init(schedule: @escaping Schedule = { delay, action in
        let task = Task { @MainActor in
            do { try await Task.sleep(for: .seconds(delay)) } catch { return }
            guard !Task.isCancelled else { return }
            await action()
        }
        return { task.cancel() }
    }) { self.schedule = schedule }

    func install(owner: ObjectIdentifier, revision: Int, refresh: @escaping () async -> Void) {
        guard self.owner != owner || self.revision != revision else { return }
        self.owner = owner
        self.revision = revision
        self.refresh = refresh
        restart()
    }

    func remove(owner: ObjectIdentifier) {
        guard self.owner == owner else { return }
        generation += 1
        cancel?()
        cancel = nil
        self.owner = nil
        refresh = nil
    }

    func setVisible(_ visible: Bool, surface: String) {
        let wasOpen = !surfaces.isEmpty
        if visible { surfaces.insert(surface) } else { surfaces.remove(surface) }
        if wasOpen != !surfaces.isEmpty { restart() }
    }

    private func restart() {
        generation += 1
        cancel?()
        cancel = nil
        // Cancellation fences publication but WebKit may keep fetching until its
        // callback returns. Keep the slot through that await, across owners too.
        guard refresh != nil, !inFlight else { return }
        enqueue(after: 0, generation: generation)
    }

    private func enqueue(after delay: TimeInterval, generation: Int) {
        cancel = schedule(delay) { [weak self] in
            guard let self, !Task.isCancelled, !self.inFlight,
                  self.generation == generation, let refresh = self.refresh else { return }
            self.inFlight = true
            await refresh()
            self.inFlight = false
            self.cancel = nil
            if self.generation != generation {
                if self.refresh != nil { self.enqueue(after: 0, generation: self.generation) }
                return
            }
            guard !Task.isCancelled, self.generation == generation else { return }
            self.didRefresh?()
            self.enqueue(after: MacMenuStatus.interval(open: !self.surfaces.isEmpty), generation: generation)
        }
    }
}

@MainActor enum MacMenuHostSource {
    static let scheduler = MacHostRefresh()
}
#endif
