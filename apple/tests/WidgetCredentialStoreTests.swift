import CryptoKit
import Foundation
import SQLite3

private final class FakeKeychain: WidgetCredentialKeychain {
    var data: Data?
    var canDelete = true
    var canPreflight = true
    var canRead = true
    var canDeletePending = true
    var pending: [String: Data] = [:]
    var afterRead: (() -> Void)?
    func read() -> Data? { let result = data; afterRead?(); return result }
    func readStatus() -> WidgetKeychainRead { canRead ? (data.map(WidgetKeychainRead.data) ?? .missing) : .unavailable }
    func preflight() -> Bool { canPreflight }
    func write(_ data: Data) -> Bool { self.data = data; return true }
    func delete() -> Bool { if canDelete { data = nil }; return canDelete }
    func readPending(_ attempt: String) -> Data? { pending[attempt] }
    func createPending(_ data: Data, attempt: String) -> Bool { guard pending[attempt] == nil else { return false }; pending[attempt] = data; return true }
    func updatePending(_ data: Data, attempt: String) -> Bool { guard pending[attempt] != nil else { return false }; pending[attempt] = data; return true }
    func deletePending(_ attempt: String) -> Bool { guard canDeletePending else { return false }; pending.removeValue(forKey: attempt); return true }
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
        let issuedEnrollment = WidgetFencedEnrollment(store: issuedStore)
        let issuedPending = ActivityCatalog.activate(url: issuedURL, endpoint: pending.endpoint)!
        let expiry = ISO8601DateFormatter().string(from: now.addingTimeInterval(3600))
        let fixtureEpoch = String(repeating: "1", count: 32), fixtureRevision = String(repeating: "2", count: 32)
        let fixtureStatus = Data("{\"epoch\":\"\(fixtureEpoch)\",\"revision\":\"\(fixtureRevision)\",\"workspace_id\":\"\(credential.workspaceID)\"}".utf8)
        func fixtureReceipt(_ attempt: ActivityCatalog.Enrollment) -> Data {
            let wire = attempt.id.replacingOccurrences(of: "-", with: "").lowercased()
            return Data("{\"epoch\":\"\(fixtureEpoch)\",\"revision\":\"\(String(repeating: "3", count: 32))\",\"workspace_id\":\"\(credential.workspaceID)\",\"attempt\":\"\(wire)\",\"operation\":\"issue\",\"expires_at\":\"\(expiry)\"}".utf8)
        }
        let firstAttempt = issuedEnrollment.prepare(expected: issuedPending)!
        guard case .unavailable = issuedEnrollment.bind(Data(repeating: 65, count: 4097), reservation: firstAttempt) else { fatalError() }
        guard case .ready = issuedEnrollment.bind(fixtureStatus, reservation: firstAttempt) else { fatalError() }
        guard case .installed(let installed) = issuedEnrollment.complete(fixtureReceipt(firstAttempt), reservation: firstAttempt, now: now, clock: { now }) else { fatalError() }
        precondition(observed.ids == [installed.id] && !observed.ids.contains(credential.token))
        precondition(issuedStore.capture(now: now)?.activation == installed)
        let wrongIdentity = Data(String(decoding: fixtureStatus, as: UTF8.self)
            .replacingOccurrences(of: credential.workspaceID, with: String(repeating: "b", count: 64)).utf8)
        let mismatchAttempt = issuedEnrollment.prepare(expected: installed)!
        var issuedLock: OpaquePointer?
        precondition(sqlite3_open(issuedURL.path, &issuedLock) == SQLITE_OK)
        precondition(sqlite3_exec(issuedLock, "BEGIN EXCLUSIVE", nil, nil, nil) == SQLITE_OK)
        guard case .identityMismatch(let mismatchResult) = issuedEnrollment.bind(wrongIdentity, reservation: mismatchAttempt) else { fatalError() }
        precondition(mismatchResult.revoked && !mismatchResult.complete && issuedKeychain.data == nil)
        precondition(sqlite3_exec(issuedLock, "ROLLBACK", nil, nil, nil) == SQLITE_OK)
        sqlite3_close(issuedLock)
        precondition(ActivityCatalog.pendingCleanup(url: issuedURL) == installed)
        precondition(issuedStore.invalidate(installed).complete)
        let again = ActivityCatalog.activate(url: issuedURL, endpoint: pending.endpoint)!
        let replacementAttempt = issuedEnrollment.prepare(expected: again)!
        guard case .ready = issuedEnrollment.bind(fixtureStatus, reservation: replacementAttempt) else { fatalError() }
        guard case .installed(let replacement) = issuedEnrollment.complete(fixtureReceipt(replacementAttempt), reservation: replacementAttempt, now: now, clock: { now }) else { fatalError() }
        guard case .identityMismatch(let staleMismatch) = issuedEnrollment.bind(wrongIdentity, reservation: mismatchAttempt) else { fatalError() }
        precondition(staleMismatch.stale && issuedStore.capture(now: now)?.activation == replacement)
        issuedKeychain.canPreflight = false
        precondition(issuedStore.beginEnrollment(expected: replacement) == nil)
        precondition(issuedStore.capture(now: now)?.activation == replacement)
        issuedKeychain.canPreflight = true
        let oldCapture = issuedStore.capture(now: now)!
        let reservation = issuedEnrollment.prepare(expected: replacement)!
        precondition(ActivityCatalog.pendingEnrollment(url: issuedURL) == reservation)
        precondition(issuedStore.capture(now: now) == nil)
        precondition(issuedStore.beginEnrollment(expected: replacement) == nil)
        precondition(issuedStore.invalidate(replacement).stale)
        let issuedCatalog = ActivityCatalog(url: issuedURL)!
        precondition(!issuedCatalog.publish(scope: replacement.scope!, generation: UUID().uuidString,
                     snapshot: snapshot, entries: [], expectedToken: oldCapture.writerToken))
        guard case .ready = issuedEnrollment.bind(fixtureStatus, reservation: reservation) else { fatalError() }
        guard case .installed(let rotated) = issuedEnrollment.complete(fixtureReceipt(reservation), reservation: reservation, now: now, clock: { now }) else { fatalError() }
        precondition(rotated.id != replacement.id && issuedStore.capture(now: now)?.activation == rotated)
        precondition(issuedStore.invalidate(replacement).stale)
        let pendingAgain = issuedEnrollment.prepare(expected: rotated)!
        precondition(ActivityCatalog.clear(url: issuedURL).complete)
        guard case .rejected = issuedEnrollment.complete(fixtureReceipt(pendingAgain), reservation: pendingAgain, now: now, clock: { now }) else { fatalError() }
        precondition(ActivityCatalog.activation(url: issuedURL) == nil)
        precondition(ActivityCatalog.pendingEnrollment(url: issuedURL) == pendingAgain)
        let afterReset = ActivityCatalog.activate(url: issuedURL, endpoint: pending.endpoint)!
        precondition(issuedStore.beginEnrollment(expected: afterReset) == nil)
        precondition(ActivityCatalog.pendingEnrollment(url: issuedURL) == pendingAgain)
        let clearURL = directory.appendingPathComponent("pending-clear.sqlite")
        let clearKeychain = FakeKeychain()
        let clearStore = WidgetCredentialStore(catalogURL: clearURL, keychain: clearKeychain)
        let clearActivation = ActivityCatalog.activate(url: clearURL, endpoint: pending.endpoint)!
        let clearAttempt = clearStore.beginEnrollment(expected: clearActivation)!
        precondition(clearStore.clearCurrent().complete)
        precondition(ActivityCatalog.activation(url: clearURL) == nil)
        precondition(ActivityCatalog.pendingEnrollment(url: clearURL) == clearAttempt)
        let failedURL = directory.appendingPathComponent("failed-install.sqlite")
        let failedActivation = ActivityCatalog.activate(url: failedURL, endpoint: pending.endpoint)!
        let failedAttempt = ActivityCatalog.reserveEnrollment(url: failedURL, expected: failedActivation)!
        let failedGeneration = UUID().uuidString
        precondition(ActivityCatalog.completeEnrollment(url: failedURL, expected: failedAttempt,
                     workspaceID: credential.workspaceID, generation: failedGeneration, install: { false }) == nil)
        precondition(ActivityCatalog.pendingEnrollment(url: failedURL) == failedAttempt)
        var wroteKeychain = false
        let failedInstall = ActivityCatalog.completeEnrollment(url: failedURL, expected: failedAttempt,
            workspaceID: credential.workspaceID, generation: failedGeneration,
            install: { wroteKeychain = true; return true }, write: { _, _ in false })
        precondition(failedInstall == nil && wroteKeychain && ActivityCatalog.activation(url: failedURL) == nil)
        precondition(ActivityCatalog.pendingEnrollment(url: failedURL) == failedAttempt)
        precondition(ActivityCatalog.completeEnrollment(url: failedURL, expected: failedAttempt,
                     workspaceID: credential.workspaceID, generation: failedGeneration, install: { true }) != nil)
        precondition(ActivityCatalog.pendingEnrollment(url: failedURL) == nil)
        let fencedURL = directory.appendingPathComponent("fenced.sqlite")
        let fencedKeychain = FakeKeychain()
        let fencedStore = WidgetCredentialStore(catalogURL: fencedURL, keychain: fencedKeychain)
        let fenced = WidgetFencedEnrollment(store: fencedStore)
        let fencedPending = ActivityCatalog.activate(url: fencedURL, endpoint: pending.endpoint)!
        fencedKeychain.canPreflight = false
        precondition(fenced.prepare(expected: fencedPending) == nil && ActivityCatalog.pendingEnrollment(url: fencedURL) == nil)
        fencedKeychain.canPreflight = true
        let fencedAttempt = fenced.prepare(expected: fencedPending)!
        let wireAttempt = fencedAttempt.id.replacingOccurrences(of: "-", with: "").lowercased()
        let secretData = fencedKeychain.pending[wireAttempt]!
        let secretRecord = try JSONSerialization.jsonObject(with: secretData) as! [String: Any]
        let secret = secretRecord["secret"] as! String
        let epoch = String(repeating: "a", count: 32), revision = String(repeating: "b", count: 32)
        let status = Data("{\"epoch\":\"\(epoch)\",\"revision\":\"\(revision)\",\"workspace_id\":\"\(credential.workspaceID)\"}".utf8)
        guard case .ready(let firstRequest) = fenced.bind(status, reservation: fencedAttempt) else { fatalError() }
        let request = try JSONSerialization.jsonObject(with: firstRequest) as! [String: String]
        let hash = SHA256.hash(data: Data(secret.utf8)).map { String(format: "%02x", $0) }.joined()
        precondition(request["commitment"] == hash && request["attempt"] == wireAttempt)
        precondition(!String(decoding: firstRequest, as: UTF8.self).contains(secret))
        let restarted = WidgetFencedEnrollment(store: fencedStore)
        let laterStatus = Data(String(decoding: status, as: UTF8.self).replacingOccurrences(of: revision, with: String(repeating: "c", count: 32)).utf8)
        guard case .ready(let replayRequest) = restarted.bind(laterStatus, reservation: fencedAttempt) else { fatalError() }
        precondition(replayRequest == firstRequest)
        let wrongEpoch = Data(String(decoding: status, as: UTF8.self).replacingOccurrences(of: epoch, with: String(repeating: "d", count: 32)).utf8)
        guard case .conflict = restarted.bind(wrongEpoch, reservation: fencedAttempt) else { fatalError() }
        let receipt = Data("{\"epoch\":\"\(epoch)\",\"revision\":\"\(String(repeating: "e", count: 32))\",\"workspace_id\":\"\(credential.workspaceID)\",\"attempt\":\"\(wireAttempt)\",\"operation\":\"issue\",\"expires_at\":\"\(expiry)\"}".utf8)
        let staleReceipt = Data(String(decoding: receipt, as: UTF8.self).replacingOccurrences(of: wireAttempt, with: String(repeating: "f", count: 32)).utf8)
        guard case .rejected = restarted.complete(staleReceipt, reservation: fencedAttempt, now: now, clock: { now }) else { fatalError() }
        precondition(fencedStore.capture(now: now) == nil && fencedKeychain.pending[wireAttempt] != nil)
        guard case .rejected = restarted.complete(receipt, reservation: fencedAttempt, now: now,
                                                   clock: { now.addingTimeInterval(3601) }) else { fatalError() }
        precondition(fencedKeychain.pending[wireAttempt] != nil)
        guard case .installed(let fencedActive) = restarted.complete(receipt, reservation: fencedAttempt, now: now, clock: { now }) else { fatalError() }
        precondition(fencedStore.capture(now: now)?.credential.token == secret)
        precondition(fencedStore.capture(now: now)?.activation == fencedActive && fencedKeychain.pending[wireAttempt] == nil)
        guard case .rejected = restarted.complete(receipt, reservation: fencedAttempt, now: now, clock: { now }) else { fatalError() }
        let cleanupURL = directory.appendingPathComponent("pending-cleanup.sqlite")
        let cleanupKeychain = FakeKeychain()
        let cleanupStore = WidgetCredentialStore(catalogURL: cleanupURL, keychain: cleanupKeychain)
        let cleanupEnrollment = WidgetFencedEnrollment(store: cleanupStore)
        let cleanupInitial = ActivityCatalog.activate(url: cleanupURL, endpoint: pending.endpoint)!
        let cleanupAttempt = cleanupEnrollment.prepare(expected: cleanupInitial)!
        let cleanupWire = cleanupAttempt.id.replacingOccurrences(of: "-", with: "").lowercased()
        guard case .ready = cleanupEnrollment.bind(status, reservation: cleanupAttempt) else { fatalError() }
        let cleanupReceipt = Data(String(decoding: receipt, as: UTF8.self).replacingOccurrences(of: wireAttempt, with: cleanupWire).utf8)
        cleanupKeychain.canDeletePending = false
        guard case .installed(let cleanupActive) = cleanupEnrollment.complete(cleanupReceipt, reservation: cleanupAttempt,
                                                                              now: now, clock: { now }) else { fatalError() }
        precondition(cleanupKeychain.pending[cleanupWire] != nil)
        cleanupKeychain.canRead = false
        let lockedCleanup = cleanupStore.invalidate(cleanupActive)
        precondition(lockedCleanup.revoked && !lockedCleanup.complete && cleanupKeychain.data != nil)
        cleanupKeychain.canRead = true
        let blockedCleanup = cleanupStore.invalidate(cleanupActive)
        precondition(blockedCleanup.revoked && !blockedCleanup.complete && cleanupKeychain.data != nil)
        cleanupKeychain.canDeletePending = true
        precondition(cleanupStore.invalidate(cleanupActive).complete)
        precondition(cleanupKeychain.pending[cleanupWire] == nil && cleanupKeychain.data == nil)
        let forgottenURL = directory.appendingPathComponent("forgotten-pending.sqlite")
        let forgottenKeychain = FakeKeychain()
        let forgottenStore = WidgetCredentialStore(catalogURL: forgottenURL, keychain: forgottenKeychain)
        let forgottenEnrollment = WidgetFencedEnrollment(store: forgottenStore)
        let forgottenActivation = ActivityCatalog.activate(url: forgottenURL, endpoint: pending.endpoint)!
        let forgottenAttempt = forgottenEnrollment.prepare(expected: forgottenActivation)!
        let forgottenWire = forgottenAttempt.id.replacingOccurrences(of: "-", with: "").lowercased()
        guard case .ready = forgottenEnrollment.bind(status, reservation: forgottenAttempt) else { fatalError() }
        precondition(forgottenStore.clearCurrent().complete)
        precondition(forgottenKeychain.pending[forgottenWire] == nil && !forgottenEnrollment.canResume(forgottenAttempt))
        precondition(ActivityCatalog.pendingEnrollment(url: forgottenURL) == forgottenAttempt)
        guard case .unavailable = forgottenEnrollment.bind(status, reservation: forgottenAttempt) else { fatalError() }
        print("PASS: widget credential fencing and retry")
    }
}
