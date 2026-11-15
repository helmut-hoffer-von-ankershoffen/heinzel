import CryptoKit
import Foundation
#if os(iOS)
import WidgetKit
#endif

struct WidgetHeinzel: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let state: String
    let activity: String?
    let asks: Int?
    let lastApproved: Double?
    let nextWake: Double?
    let portrait: Data?

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

struct ActivityWidgetData: Codable, Equatable {
    static let kind = "HeinzelActivity"
    static let pinnedKind = "HeinzelPinned"
    static let rosterKind = "HeinzelRoster"
    static let freshness: TimeInterval = 300
    private static let group = "group.team.heinzel.apple.activity"
    private static let key = "activitySnapshot"
    private static let detailsKey = "activityDetailRequests"

    let awake: Int
    let waiting: Int
    let updatedAt: Date
    var heinzels: [WidgetHeinzel]?
    var workspaceScope: String?

    var expiresAt: Date { updatedAt.addingTimeInterval(Self.freshness) }

    var roster: [WidgetHeinzel] { heinzels ?? [] }

    static func scope(for address: String) -> String {
        SHA256.hash(data: Data(address.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    static func read() -> Self? {
        guard let data = UserDefaults(suiteName: group)?.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(Self.self, from: data)
    }

    #if os(iOS)
    static func reload() {
        for kind in [kind, pinnedKind, rosterKind] { WidgetCenter.shared.reloadTimelines(ofKind: kind) }
    }
    #endif

    static var requestedDetails: [String] {
        (UserDefaults(suiteName: group)?.stringArray(forKey: detailsKey) ?? []).filter(NativeRoute.isSessionID)
    }

    static func requestDetails(for id: String) {
        guard NativeRoute.isSessionID(id), let defaults = UserDefaults(suiteName: group) else { return }
        let current = requestedDetails
        guard !current.contains(id) else { return }
        defaults.set(Array(([id] + current).prefix(4)), forKey: detailsKey)
    }

    @discardableResult
    static func save(awake: Int, waiting: Int, heinzels: [WidgetHeinzel], workspaceScope: String, now: Date = .now) -> Bool {
        guard awake >= 0, waiting >= 0, let defaults = UserDefaults(suiteName: group) else { return false }
        if let previous = read(), previous.workspaceScope == workspaceScope, previous.awake == awake, previous.waiting == waiting, previous.roster == heinzels,
           now.timeIntervalSince(previous.updatedAt) < 90 { return false }
        let snapshot = Self(awake: awake, waiting: waiting, updatedAt: now, heinzels: heinzels, workspaceScope: workspaceScope)
        guard let data = try? JSONEncoder().encode(snapshot) else { return false }
        defaults.set(data, forKey: key)
        return true
    }

    @discardableResult
    static func clear() -> Bool {
        guard let defaults = UserDefaults(suiteName: group), defaults.object(forKey: key) != nil else { return false }
        defaults.removeObject(forKey: key)
        return true
    }
}
