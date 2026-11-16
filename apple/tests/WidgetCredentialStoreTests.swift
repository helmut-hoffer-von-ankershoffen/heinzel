import Foundation
import SQLite3

private final class FakeKeychain: WidgetCredentialKeychain {
    var data: Data?
    var canDelete = true
    var canPreflight = true
    var afterRead: (() -> Void)?
    func read() -> Data? { let result = data; afterRead?(); return result }
    func preflight() -> Bool { canPreflight }
    func write(_ data: Data) -> Bool { self.data = data; return true }
    func delete() -> Bool { if canDelete { data = nil }; return canDelete }
}

private final class NotificationCapture: @unchecked Sendable {
    var ids: [String] = []
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
        func seed(_ credential: WidgetCredential, expected: ActivityCatalog.Activation, at path: URL, in keychain: FakeKeychain) -> ActivityCatalog.Activation {
            let data = try! JSONEncoder().encode(credential)
            return ActivityCatalog.installCredential(url: path, expected: expected, workspaceID: credential.workspaceID,
                                                       generation: credential.generation, install: { keychain.write(data) })!
        }
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let pending = ActivityCatalog.activate(url: url, endpoint: "https://example.test")!
        precondition(ActivityCatalog.workspaceScope(url: url, endpoint: pending.endpoint, legacy: "legacy") == nil)
        let credential = WidgetCredential(endpoint: pending.endpoint, workspaceID: String(repeating: "a", count: 64),
            generation: UUID().uuidString, token: "hzw_" + String(repeating: "x", count: 40), expiresAt: now.addingTimeInterval(3600))
        let observed = NotificationCapture()
        let observer = NotificationCenter.default.addObserver(forName: WidgetCredentialStore.activationChanged, object: nil, queue: nil) { note in
            if let id = note.object as? String { observed.ids.append(id) }
        }
        defer { NotificationCenter.default.removeObserver(observer) }
        let bound = seed(credential, expected: pending, at: url, in: keychain)
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
        let nextBound = seed(nextCredential, expected: next, at: url, in: keychain)
        let staleClear = ActivityCatalog.clear(url: url, expectedToken: capture.writerToken)
        precondition(staleClear.stale && ActivityCatalog.activation(url: url) == nextBound && keychain.data != nil)
        keychain.canDelete = false
        let revoked = store.clearCurrent()
        precondition(revoked.revoked && !revoked.complete && store.capture(now: now) == nil)
        precondition(ActivityCatalog.workspaceScope(url: url, endpoint: next.endpoint, legacy: "legacy") == nil)
        precondition(ActivityCatalog.pendingCleanup(url: url) == nextBound)
        keychain.canDelete = true
        precondition(store.clearCurrent().complete && keychain.data == nil)
        precondition(SharedWidgetKeychain(accessGroup: "group.team.heinzel.apple.activity") == nil)
        precondition(SharedWidgetKeychain(accessGroup: "ABCDEFGHIJ.team.heinzel.widget") != nil)
        let lockedURL = directory.appendingPathComponent("locked.sqlite")
        let lockedCatalog = ActivityCatalog(url: lockedURL)!
        let lockedKeychain = FakeKeychain()
        let lockedStore = WidgetCredentialStore(catalogURL: lockedURL, keychain: lockedKeychain)
        let lockedPending = ActivityCatalog.activate(url: lockedURL, endpoint: pending.endpoint)!
        let lockedBound = seed(credential, expected: lockedPending, at: lockedURL, in: lockedKeychain)
        let beforePublication = lockedStore.capture(now: now)!
        precondition(lockedCatalog.publish(scope: lockedBound.scope!, generation: UUID().uuidString, snapshot: snapshot,
                     entries: [], expectedToken: beforePublication.writerToken))
        var db: OpaquePointer?
        precondition(sqlite3_open(lockedURL.path, &db) == SQLITE_OK)
        precondition(sqlite3_exec(db, "BEGIN EXCLUSIVE", nil, nil, nil) == SQLITE_OK)
        let unreadable = ActivityCatalog.clear(url: lockedURL, expectedToken: beforePublication.writerToken)
        precondition(!unreadable.stale && !unreadable.complete)
        let failedPurge = lockedStore.invalidate(beforePublication.activation)
        precondition(failedPurge.revoked && !failedPurge.complete)
        precondition(sqlite3_exec(db, "ROLLBACK", nil, nil, nil) == SQLITE_OK)
        sqlite3_close(db)
        precondition(lockedCatalog.snapshot() == nil && ActivityCatalog.pendingCleanup(url: lockedURL) == lockedBound)
        precondition(lockedStore.invalidate(lockedBound).complete)
        let issuedURL = directory.appendingPathComponent("issued.sqlite")
        let issuedKeychain = FakeKeychain()
        let issuedStore = WidgetCredentialStore(catalogURL: issuedURL, keychain: issuedKeychain)
        let issuedPending = ActivityCatalog.activate(url: issuedURL, endpoint: pending.endpoint)!
        let expiry = ISO8601DateFormatter().string(from: now.addingTimeInterval(3600))
        let issued = Data("""
        {"token":"\(credential.token)","workspace_id":"\(credential.workspaceID)","expires_at":"\(expiry)"}
        """.utf8)
        let firstAttempt = issuedStore.beginEnrollment(expected: issuedPending)!
        guard case .rejected = issuedStore.completeIssued(Data(repeating: 65, count: 4097), expected: firstAttempt, now: now) else { fatalError() }
        guard case .installed(let installed) = issuedStore.completeIssued(issued, expected: firstAttempt, now: now) else { fatalError() }
        precondition(observed.ids == [installed.id] && !observed.ids.contains(credential.token))
        precondition(issuedStore.capture(now: now)?.activation == installed)
        let wrongIdentity = Data(String(decoding: issued, as: UTF8.self)
            .replacingOccurrences(of: credential.workspaceID, with: String(repeating: "b", count: 64)).utf8)
        let mismatchAttempt = issuedStore.beginEnrollment(expected: installed)!
        var issuedLock: OpaquePointer?
        precondition(sqlite3_open(issuedURL.path, &issuedLock) == SQLITE_OK)
        precondition(sqlite3_exec(issuedLock, "BEGIN EXCLUSIVE", nil, nil, nil) == SQLITE_OK)
        guard case .identityMismatch(let mismatchResult) = issuedStore.completeIssued(wrongIdentity, expected: mismatchAttempt, now: now) else { fatalError() }
        precondition(mismatchResult.revoked && !mismatchResult.complete && issuedKeychain.data == nil)
        precondition(sqlite3_exec(issuedLock, "ROLLBACK", nil, nil, nil) == SQLITE_OK)
        sqlite3_close(issuedLock)
        precondition(ActivityCatalog.pendingCleanup(url: issuedURL) == installed)
        precondition(issuedStore.invalidate(installed).complete)
        let again = ActivityCatalog.activate(url: issuedURL, endpoint: pending.endpoint)!
        let replacementAttempt = issuedStore.beginEnrollment(expected: again)!
        guard case .installed(let replacement) = issuedStore.completeIssued(issued, expected: replacementAttempt, now: now) else { fatalError() }
        guard case .identityMismatch(let staleMismatch) = issuedStore.completeIssued(wrongIdentity, expected: mismatchAttempt, now: now) else { fatalError() }
        precondition(staleMismatch.stale && issuedStore.capture(now: now)?.activation == replacement)
        issuedKeychain.canPreflight = false
        precondition(issuedStore.beginEnrollment(expected: replacement) == nil)
        precondition(issuedStore.capture(now: now)?.activation == replacement)
        issuedKeychain.canPreflight = true
        let oldCapture = issuedStore.capture(now: now)!
        let reservation = issuedStore.beginEnrollment(expected: replacement)!
        precondition(ActivityCatalog.pendingEnrollment(url: issuedURL) == reservation)
        precondition(issuedStore.capture(now: now) == nil)
        precondition(issuedStore.beginEnrollment(expected: replacement) == nil)
        precondition(issuedStore.invalidate(replacement).stale)
        let issuedCatalog = ActivityCatalog(url: issuedURL)!
        precondition(!issuedCatalog.publish(scope: replacement.scope!, generation: UUID().uuidString,
                     snapshot: snapshot, entries: [], expectedToken: oldCapture.writerToken))
        guard case .installed(let rotated) = issuedStore.completeIssued(issued, expected: reservation, now: now) else { fatalError() }
        precondition(rotated.id != replacement.id && issuedStore.capture(now: now)?.activation == rotated)
        precondition(issuedStore.invalidate(replacement).stale)
        let pendingAgain = issuedStore.beginEnrollment(expected: rotated)!
        precondition(ActivityCatalog.clear(url: issuedURL).complete)
        guard case .rejected = issuedStore.completeIssued(issued, expected: pendingAgain, now: now) else { fatalError() }
        precondition(ActivityCatalog.activation(url: issuedURL) == nil)
        print("PASS: widget credential fencing and retry")
    }
}
