import Foundation

@main struct WidgetSnapshotTests {
    static func main() throws {
        let workspace = String(repeating: "a", count: 64)
        let id = "hz-" + String(repeating: "0", count: 26)
        let now = ISO8601DateFormatter().date(from: "2026-11-16T00:00:00Z")!
        func decode(_ changes: (String) -> String = { $0 }) throws -> WidgetSnapshotEnvelope {
            let json = """
            {"version":1,"workspace_id":"\(workspace)","generated_at":"2026-11-15T23:59:30Z","fresh_for_seconds":300,
             "awake":1,"waiting":1,"roster":[{"id":"\(id)","name":"Example","state":"awake","activity":"working","asks":1}]}
            """
            return try JSONDecoder().decode(WidgetSnapshotEnvelope.self, from: Data(changes(json).utf8))
        }
        let valid = try decode()
        precondition(valid.valid(now: now))
        let version = try decode { $0.replacingOccurrences(of: "\"version\":1", with: "\"version\":2") }
        precondition(!version.valid(now: now))
        let old = try decode { $0.replacingOccurrences(of: "23:59:30Z", with: "23:50:00Z") }
        precondition(!old.valid(now: now))
        let future = try decode { $0.replacingOccurrences(of: "2026-11-15T23:59:30Z", with: "2026-11-16T00:01:00Z") }
        precondition(!future.valid(now: now))
        let nonUTC = try decode { $0.replacingOccurrences(of: "23:59:30Z", with: "23:59:30+01:00") }
        precondition(!nonUTC.valid(now: now))
        let rich = try decode { $0.replacingOccurrences(of: "\"asks\":1", with: "\"asks\":1,\"portrait\":\"eA==\"") }
        precondition(!rich.valid(now: now))
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("widget-partial-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("catalog.sqlite")
        let catalog = ActivityCatalog(url: url)!
        let pending = ActivityCatalog.activate(url: url, endpoint: "https://example.test")!
        let credential = WidgetCredential(endpoint: pending.endpoint, workspaceID: workspace, generation: UUID().uuidString,
            token: "hzw_" + String(repeating: "x", count: 40), expiresAt: now.addingTimeInterval(3600))
        let active = ActivityCatalog.installCredential(url: url, expected: pending, workspaceID: workspace,
                                                       generation: credential.generation, install: { true })!
        let rows = (0..<60).map { CatalogHeinzel(id: "item-\($0)", name: "Name \($0)", state: "awake") }
        precondition(ActivityWidgetData.save(awake: 1, waiting: 0, heinzels: [], entries: rows,
                     workspaceScope: active.scope!, expectedToken: ActivityCatalog.refreshToken(url: url)!,
                     now: now.addingTimeInterval(-60), catalogOverride: catalog))
        let capture = WidgetCredentialCapture(activation: active, credential: credential,
                                              writerToken: ActivityCatalog.refreshToken(url: url)!)
        precondition(ActivityWidgetData.savePartial(valid, capture: capture, now: now, catalogOverride: catalog, clock: { now }))
        let saved = try JSONDecoder().decode(ActivityWidgetData.self, from: catalog.snapshot()!)
        precondition(saved.updatedAt == valid.timestamp && saved.roster.count == 1)
        precondition(saved.catalogUpdatedAt == now.addingTimeInterval(-60))
        precondition(catalog.search(scope: active.scope!, generation: saved.generation!, offset: 50).count == 11)
        precondition(catalog.find(scope: active.scope!, generation: saved.generation!, id: id)?.name == "Example")
        precondition(!ActivityWidgetData.savePartial(valid, capture: capture, now: now, catalogOverride: catalog, clock: { now }))
        let freshCapture = WidgetCredentialCapture(activation: active, credential: credential,
                                                   writerToken: ActivityCatalog.refreshToken(url: url)!)
        precondition(!ActivityWidgetData.savePartial(old, capture: freshCapture, now: now, catalogOverride: catalog, clock: { now }))
        let later = try decode { $0.replacingOccurrences(of: "23:59:30Z", with: "23:59:45Z") }
        let expiring = WidgetCredential(endpoint: credential.endpoint, workspaceID: workspace, generation: credential.generation,
                                        token: credential.token, expiresAt: now.addingTimeInterval(10))
        let expiryCapture = WidgetCredentialCapture(activation: active, credential: expiring, writerToken: freshCapture.writerToken)
        precondition(!ActivityWidgetData.savePartial(later, capture: expiryCapture, now: now, catalogOverride: catalog,
                                                    clock: { now.addingTimeInterval(11) }))
        let afterExpiry = try JSONDecoder().decode(ActivityWidgetData.self, from: catalog.snapshot()!)
        precondition(afterExpiry.updatedAt == valid.timestamp)
        print("PASS: widget snapshot version, freshness and data bounds")
    }
}
