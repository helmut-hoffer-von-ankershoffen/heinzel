import CryptoKit
import Foundation
import SwiftUI
import WidgetKit

struct WidgetHeinzel: Codable, Identifiable, Equatable {
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
    private static var defaults: UserDefaults? {
        #if os(macOS)
        // A team-prefixed group allows the signed desktop app and extension to share
        // snapshots without adding a provisioning-profile dependency to the Mac installer.
        guard let group = Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetGroup") as? String,
              group.wholeMatch(of: /[A-Z0-9]{10}\.team\.heinzel\.apple\.activity/) != nil else { return nil }
        #else
        let group = "group.team.heinzel.apple.activity"
        #endif
        return UserDefaults(suiteName: group)
    }
    private static let key = "activitySnapshot"
    private static let detailsKey = "activityDetailRequests"

    let awake: Int
    let waiting: Int
    let updatedAt: Date
    var heinzels: [WidgetHeinzel]?
    var workspaceScope: String?

    var expiresAt: Date { updatedAt.addingTimeInterval(Self.freshness) }

    var roster: [WidgetHeinzel] { heinzels ?? [] }

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

    static func read() -> Self? {
        guard let data = defaults?.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(Self.self, from: data)
    }

    static func reload() {
        for kind in [kind, pinnedKind, rosterKind] { WidgetCenter.shared.reloadTimelines(ofKind: kind) }
    }

    static var requestedDetails: [String] {
        (defaults?.stringArray(forKey: detailsKey) ?? []).filter(NativeRoute.isSessionID)
    }

    static func requestDetails(for id: String) {
        guard NativeRoute.isSessionID(id), let defaults else { return }
        let current = requestedDetails
        guard !current.contains(id) else { return }
        defaults.set(Array(([id] + current).prefix(4)), forKey: detailsKey)
    }

    @discardableResult
    static func save(awake: Int, waiting: Int, heinzels: [WidgetHeinzel], workspaceScope: String, now: Date = .now) -> Bool {
        guard awake >= 0, waiting >= 0, let defaults else { return false }
        if let previous = read(), previous.workspaceScope == workspaceScope, previous.awake == awake, previous.waiting == waiting, previous.roster == heinzels,
           now.timeIntervalSince(previous.updatedAt) < 90 { return false }
        let snapshot = Self(awake: awake, waiting: waiting, updatedAt: now, heinzels: heinzels, workspaceScope: workspaceScope)
        guard let data = try? JSONEncoder().encode(snapshot) else { return false }
        defaults.set(data, forKey: key)
        return true
    }

    @discardableResult
    static func clear() -> Bool {
        guard let defaults, defaults.object(forKey: key) != nil else { return false }
        defaults.removeObject(forKey: key)
        return true
    }
}
