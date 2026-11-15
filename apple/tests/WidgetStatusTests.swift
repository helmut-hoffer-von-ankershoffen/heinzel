import Foundation

// Standalone, isolated model probe; does not read or write shared widget defaults.
@main
struct WidgetStatusTests {
    static func main() {
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
        print("PASS: attention ordering, lifecycle status, expiry and unknown details")
    }
}
