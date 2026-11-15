import Foundation

// Standalone, isolated model probe; does not read or write shared widget defaults.
@main
struct WidgetStatusTests {
    static func main() throws {
        func item(_ activity: String?, state: String = "active") -> WidgetHeinzel {
            WidgetHeinzel(id: "fixture", name: "Example", state: state, activity: activity,
                          asks: nil, lastApproved: nil, nextWake: nil, portrait: nil)
        }
        for activity in ["waiting_for_you", "stuck", "overloaded"] {
            precondition(item(activity).widgetPriority < item("working").widgetPriority)
        }
        for activity in ["paused", "asleep", "idle", "waiting_on_others", "proud"] {
            precondition(item("working").widgetPriority < item(activity).widgetPriority)
        }
        precondition(item("working", state: "retired").status == "Retired")
        precondition(item("working", state: "retired").widgetPriority > item("paused").widgetPriority)
        precondition(item(nil, state: "proposed").status == "New")
        precondition(item("waiting_for_you").status == "Waiting for you")
        let date = Date(timeIntervalSince1970: 1_800_000_000)
        let snapshot = ActivityWidgetData(awake: 1, waiting: 1, updatedAt: date,
                                          heinzels: [item("working"), item("waiting_for_you")])
        precondition(snapshot.expiresAt == date.addingTimeInterval(300))
        precondition(snapshot.updatedAt == date)
        precondition(snapshot.roster.map(\.activity) == ["working", "waiting_for_you"])
        precondition(snapshot.roster.allSatisfy { $0.asks == nil && $0.nextWake == nil })
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("widget-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("catalog.sqlite")
        let catalog = ActivityCatalog(url: url)!
        let scope = ActivityWidgetData.scope(for: "fictional-workspace")
        precondition(ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: snapshot.roster,
                                             entries: [], workspaceScope: scope, expectedToken: ActivityCatalog.refreshToken(url: url)!,
                                             now: date, catalogOverride: catalog))
        let later = date.addingTimeInterval(45)
        precondition(ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: snapshot.roster,
                                             entries: [], workspaceScope: scope, expectedToken: ActivityCatalog.refreshToken(url: url)!,
                                             now: later, catalogOverride: catalog))
        let saved = try JSONDecoder().decode(ActivityWidgetData.self, from: catalog.snapshot()!)
        precondition(saved.updatedAt == later && saved.workspaceScope == scope)
        precondition(saved.expiresAt == later.addingTimeInterval(300))
        let unsafeURL = directory.appendingPathComponent("unsafe.sqlite")
        let unsafeCatalog = ActivityCatalog(url: unsafeURL)!
        let lockURL = unsafeURL.appendingPathExtension("lock")
        let target = directory.appendingPathComponent("target")
        try! Data("untouched".utf8).write(to: target)
        try! FileManager.default.createSymbolicLink(at: lockURL, withDestinationURL: target)
        let token = ActivityCatalog.refreshToken(url: unsafeURL)!
        precondition(!ActivityWidgetData.save(awake: 0, waiting: 0, heinzels: [], entries: [],
                                              workspaceScope: scope, expectedToken: token, catalogOverride: unsafeCatalog))
        precondition(try! Data(contentsOf: target) == Data("untouched".utf8))
        precondition(!ActivityCatalog.clear(url: unsafeURL).complete)
        print("PASS: attention ordering, lifecycle status, expiry and unknown details")
    }
}
