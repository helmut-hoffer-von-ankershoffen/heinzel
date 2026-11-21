import Foundation

// Fixed fictional identities for this isolated fixture; never production configuration.
private let fixtureEndpoint = "https://example.test"
private let otherFixtureEndpoint = "https://other.test"
private let insecureFixtureEndpoint = "http://example.test"

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
        try Data("untouched".utf8).write(to: target)
        try FileManager.default.createSymbolicLink(at: lockURL, withDestinationURL: target)
        let token = ActivityCatalog.refreshToken(url: unsafeURL)!
        precondition(!ActivityWidgetData.save(awake: 0, waiting: 0, heinzels: [], entries: [],
                                              workspaceScope: scope, expectedToken: token, catalogOverride: unsafeCatalog))
        let targetContents = try Data(contentsOf: target)
        precondition(targetContents == Data("untouched".utf8))
        precondition(!ActivityCatalog.clear(url: unsafeURL).complete)
        let oldToken = ActivityCatalog.refreshToken(url: url)!
        let activation = ActivityCatalog.activate(url: url, endpoint: fixtureEndpoint)!
        precondition(catalog.snapshot() == nil)
        precondition(!ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: [], entries: [],
                                              workspaceScope: scope, expectedToken: oldToken, catalogOverride: catalog))
        let pendingToken = ActivityCatalog.refreshToken(url: url)!
        precondition(!ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: [], entries: [],
                                              workspaceScope: scope, expectedToken: pendingToken, catalogOverride: catalog))
        let workspace = String(repeating: "a", count: 64)
        let bound = ActivityCatalog.installCredential(url: url, expected: activation, workspaceID: workspace,
                                                      generation: UUID().uuidString, install: { true })!
        precondition(ActivityCatalog.activation(url: url) == bound)
        var reusedInstallCalled = false
        precondition(ActivityCatalog.installCredential(url: url, expected: bound, workspaceID: workspace,
                    generation: bound.credentialGeneration!, install: { reusedInstallCalled = true; return true }) == nil)
        precondition(!reusedInstallCalled)
        var installedGeneration = bound.credentialGeneration!
        let failedGeneration = UUID().uuidString
        precondition(ActivityCatalog.installCredential(url: url, expected: bound, workspaceID: workspace,
                    generation: failedGeneration, install: { installedGeneration = failedGeneration; return true },
                    write: { _, _ in false }) == nil)
        precondition(installedGeneration != ActivityCatalog.activation(url: url)?.credentialGeneration)
        let boundToken = ActivityCatalog.refreshToken(url: url)!
        precondition(!ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: [], entries: [],
                                              workspaceScope: scope, expectedToken: boundToken, catalogOverride: catalog))
        precondition(ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: [], entries: [],
                                             workspaceScope: bound.scope!, expectedToken: boundToken, catalogOverride: catalog))
        let staleWriter = ActivityCatalog.refreshToken(url: url)!
        let result = ActivityCatalog.invalidate(url: url, expected: bound, deleteCredential: { false })
        precondition(result.revoked && !result.complete && catalog.snapshot() == nil)
        precondition(ActivityCatalog.clear(url: url).complete)
        let retry = ActivityCatalog.invalidate(url: url, expected: bound, deleteCredential: { true })
        precondition(retry.complete)
        precondition(!ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: [], entries: [],
                                              workspaceScope: bound.scope!, expectedToken: staleWriter, catalogOverride: catalog))
        let revokedToken = ActivityCatalog.refreshToken(url: url)!
        precondition(!ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: [], entries: [],
                                              workspaceScope: bound.scope!, expectedToken: revokedToken, catalogOverride: catalog))
        let replacement = ActivityCatalog.activate(url: url, endpoint: otherFixtureEndpoint)!
        var staleDeleteCalled = false
        let stale = ActivityCatalog.invalidate(url: url, expected: bound, deleteCredential: { staleDeleteCalled = true; return true })
        precondition(!stale.revoked && !staleDeleteCalled && ActivityCatalog.activation(url: url) == replacement)
        var staleInstallCalled = false
        precondition(ActivityCatalog.installCredential(url: url, expected: activation, workspaceID: workspace,
                    generation: UUID().uuidString, install: { staleInstallCalled = true; return true }) == nil)
        precondition(!staleInstallCalled)
        precondition(ActivityCatalog.activate(url: url, endpoint: insecureFixtureEndpoint) == nil)
        var removed = false
        let failedClear = ActivityCatalog.clear(url: url, write: { _, _ in false }, remove: { _ in removed = true; return true })
        precondition(!failedClear.revoked && !removed)
        precondition(ActivityCatalog.clear(url: url).complete)
        precondition(ActivityCatalog.invalidate(url: url, expected: replacement, deleteCredential: { true }).complete)
        let afterClear = ActivityCatalog.refreshToken(url: url)!
        precondition(!ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: [], entries: [],
                                              workspaceScope: scope, expectedToken: afterClear, catalogOverride: catalog))
        try FileManager.default.removeItem(at: url.appendingPathExtension("authority"))
        let missingAuthority = ActivityCatalog.refreshToken(url: url)!
        precondition(!ActivityWidgetData.save(awake: 1, waiting: 1, heinzels: [], entries: [],
                                              workspaceScope: scope, expectedToken: missingAuthority, catalogOverride: catalog))
        let rotationURL = directory.appendingPathComponent("rotation.sqlite")
        let initial = ActivityCatalog.activate(url: rotationURL, endpoint: fixtureEndpoint)!
        let g1 = UUID().uuidString
        let g2 = UUID().uuidString
        let first = ActivityCatalog.installCredential(url: rotationURL, expected: initial, workspaceID: workspace,
                                                      generation: g1, install: { true })!
        let second = ActivityCatalog.installCredential(url: rotationURL, expected: first, workspaceID: workspace,
                                                       generation: g2, install: { true })!
        let third = ActivityCatalog.installCredential(url: rotationURL, expected: second, workspaceID: workspace,
                                                      generation: g1, install: { true })!
        precondition(third.id != first.id)
        var oldCallback = false
        precondition(!ActivityCatalog.invalidate(url: rotationURL, expected: first,
                     deleteCredential: { oldCallback = true; return true }).revoked)
        precondition(ActivityCatalog.installCredential(url: rotationURL, expected: first, workspaceID: workspace,
                     generation: g2, install: { oldCallback = true; return true }) == nil)
        precondition(!oldCallback && ActivityCatalog.activation(url: rotationURL) == third)
        print("PASS: attention ordering, lifecycle status, expiry and unknown details")
    }
}
