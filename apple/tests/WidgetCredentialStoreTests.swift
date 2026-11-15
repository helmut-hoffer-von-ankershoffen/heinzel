import Foundation

private final class FakeKeychain: WidgetCredentialKeychain {
    var data: Data?
    var canDelete = true
    var afterRead: (() -> Void)?
    func read() -> Data? { let result = data; afterRead?(); return result }
    func write(_ data: Data) -> Bool { self.data = data; return true }
    func delete() -> Bool { if canDelete { data = nil }; return canDelete }
}

@main struct WidgetCredentialStoreTests {
    static func main() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("widget-credentials-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("catalog.sqlite")
        let catalog = ActivityCatalog(url: url)!
        let keychain = FakeKeychain()
        let store = WidgetCredentialStore(catalogURL: url, keychain: keychain)
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let pending = ActivityCatalog.activate(url: url, endpoint: "https://example.test")!
        precondition(ActivityCatalog.workspaceScope(url: url, endpoint: pending.endpoint, legacy: "legacy") == nil)
        let credential = WidgetCredential(endpoint: pending.endpoint, workspaceID: String(repeating: "a", count: 64),
            generation: UUID().uuidString, token: "hzw_" + String(repeating: "x", count: 40), expiresAt: now.addingTimeInterval(3600))
        let bound = store.install(credential, expected: pending, now: now)!
        precondition(ActivityCatalog.workspaceScope(url: url, endpoint: pending.endpoint, legacy: "legacy") == bound.scope)
        precondition(ActivityCatalog.workspaceScope(url: url, endpoint: "https://other.test", legacy: "legacy") == nil)
        let capture = store.capture(now: now)!
        precondition(capture.activation == bound && capture.credential == credential)
        precondition(store.capture(now: now.addingTimeInterval(3601)) == nil)
        let snapshot = Data("fictional".utf8)
        precondition(catalog.publish(scope: bound.scope!, generation: UUID().uuidString, snapshot: snapshot,
                                     entries: [], expectedToken: capture.writerToken))
        keychain.afterRead = { _ = ActivityCatalog.activate(url: url, endpoint: "https://other.test") }
        precondition(store.capture(now: now) == nil)
        keychain.afterRead = nil
        precondition(!store.invalidate(bound).revoked && keychain.data != nil)
        let next = ActivityCatalog.activation(url: url)!
        let nextCredential = WidgetCredential(endpoint: next.endpoint, workspaceID: credential.workspaceID,
            generation: UUID().uuidString, token: credential.token, expiresAt: credential.expiresAt)
        let nextBound = store.install(nextCredential, expected: next, now: now)!
        keychain.canDelete = false
        let revoked = store.clearCurrent()
        precondition(revoked.revoked && !revoked.complete && store.capture(now: now) == nil)
        precondition(ActivityCatalog.workspaceScope(url: url, endpoint: next.endpoint, legacy: "legacy") == nil)
        precondition(ActivityCatalog.pendingCleanup(url: url) == nextBound)
        keychain.canDelete = true
        precondition(store.clearCurrent().complete && keychain.data == nil)
        precondition(SharedWidgetKeychain(accessGroup: "group.team.heinzel.apple.activity") == nil)
        precondition(SharedWidgetKeychain(accessGroup: "ABCDEFGHIJ.team.heinzel.widget") != nil)
        print("PASS: widget credential fencing and retry")
    }
}
