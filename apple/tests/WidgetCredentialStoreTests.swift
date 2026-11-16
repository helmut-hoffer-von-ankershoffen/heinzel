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
    func readPendingStatus(_ attempt: String) -> WidgetKeychainRead {
        canRead ? (pending[attempt].map(WidgetKeychainRead.data) ?? .missing) : .unavailable
    }
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
        precondition(store.renewalCredential(expected: bound, now: now.addingTimeInterval(3601)) == credential)
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
        let journalURL = issuedURL.appendingPathExtension("enrollment-pending")
        let preparedJournal = try JSONDecoder().decode(ActivityCatalog.EnrollmentJournal.self, from: Data(contentsOf: journalURL))
        precondition(preparedJournal.phase == .preparing && preparedJournal.reservation == firstAttempt && preparedJournal.request == nil)
        guard case .unavailable = issuedEnrollment.bind(Data(repeating: 65, count: 4097), reservation: firstAttempt) else { fatalError() }
        guard case .ready(let armedRequest) = issuedEnrollment.bind(fixtureStatus, reservation: firstAttempt) else { fatalError() }
        let armedData = try Data(contentsOf: journalURL)
        let armedJournal = try JSONDecoder().decode(ActivityCatalog.EnrollmentJournal.self, from: armedData)
        precondition(armedJournal.phase == .armed && armedJournal.request == armedRequest && armedJournal.workspaceID == credential.workspaceID)
        let firstWire = firstAttempt.id.replacingOccurrences(of: "-", with: "").lowercased()
        let pendingObject = try JSONSerialization.jsonObject(with: issuedKeychain.pending[firstWire]!) as! [String: Any]
        let pendingSecret = pendingObject["secret"] as! String
        precondition(!String(decoding: armedRequest, as: UTF8.self).contains(pendingSecret))
        precondition(!ActivityCatalog.armEnrollment(url: issuedURL, expected: firstAttempt, workspaceID: credential.workspaceID,
                                                    request: Data("different".utf8)))
        guard case .installed(let installed) = issuedEnrollment.complete(fixtureReceipt(firstAttempt), reservation: firstAttempt, now: now, clock: { now }) else { fatalError() }
        precondition(observed.ids == [installed.id] && !observed.ids.contains(credential.token))
        precondition(issuedStore.capture(now: now)?.activation == installed)
        let replacement = installed
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
        let prepURL = directory.appendingPathComponent("interrupted-preparation.sqlite")
        let prepKeychain = FakeKeychain()
        let prepStore = WidgetCredentialStore(catalogURL: prepURL, keychain: prepKeychain)
        let prepEnrollment = WidgetFencedEnrollment(store: prepStore)
        let prepInitial = ActivityCatalog.activate(url: prepURL, endpoint: pending.endpoint)!
        let prepAttempt = prepStore.beginEnrollment(expected: prepInitial)!
        var prepDeleteCalled = false
        precondition(!ActivityCatalog.abandonPreparing(url: prepURL, expected: prepAttempt,
            syncAuthority: { _, _ in false }) { prepDeleteCalled = true; return true })
        precondition(!prepDeleteCalled && ActivityCatalog.pendingEnrollment(url: prepURL) == prepAttempt)
        prepKeychain.canDeletePending = false
        precondition(!prepEnrollment.abandonInterruptedPreparation(prepAttempt))
        precondition(ActivityCatalog.pendingEnrollment(url: prepURL) == prepAttempt)
        prepKeychain.canDeletePending = true
        precondition(prepEnrollment.abandonInterruptedPreparation(prepAttempt))
        precondition(ActivityCatalog.pendingEnrollment(url: prepURL) == nil && ActivityCatalog.activation(url: prepURL) == prepInitial)
        let tombstoneAttempt = prepStore.beginEnrollment(expected: prepInitial)!
        precondition(prepStore.clearCurrent().complete)
        precondition(prepEnrollment.abandonInterruptedPreparation(tombstoneAttempt))
        precondition(ActivityCatalog.activation(url: prepURL) == nil && ActivityCatalog.pendingCleanup(url: prepURL) == prepInitial)
        let armedPrepURL = directory.appendingPathComponent("armed-preparation.sqlite")
        let armedPrepKeychain = FakeKeychain()
        let armedPrepStore = WidgetCredentialStore(catalogURL: armedPrepURL, keychain: armedPrepKeychain)
        let armedPrepEnrollment = WidgetFencedEnrollment(store: armedPrepStore)
        let armedPrepInitial = ActivityCatalog.activate(url: armedPrepURL, endpoint: pending.endpoint)!
        let armedPrepAttempt = armedPrepEnrollment.prepare(expected: armedPrepInitial)!
        guard case .ready = armedPrepEnrollment.bind(fixtureStatus, reservation: armedPrepAttempt) else { fatalError() }
        precondition(!armedPrepEnrollment.abandonInterruptedPreparation(armedPrepAttempt))
        precondition(armedPrepEnrollment.canResume(armedPrepAttempt))
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
        precondition(cleanupEnrollment.prepare(expected: cleanupActive) == nil)
        precondition(cleanupEnrollment.finishInterruptedInstallation(cleanupAttempt) == nil)
        cleanupKeychain.canDeletePending = true
        precondition(cleanupEnrollment.finishInterruptedInstallation(cleanupAttempt) == cleanupActive)
        precondition(cleanupKeychain.pending[cleanupWire] == nil)
        let replacementPending = cleanupEnrollment.prepare(expected: cleanupActive)!
        let replacementWire = replacementPending.id.replacingOccurrences(of: "-", with: "").lowercased()
        guard case .ready = cleanupEnrollment.bind(status, reservation: replacementPending) else { fatalError() }
        let replacementReceipt = Data(String(decoding: receipt, as: UTF8.self).replacingOccurrences(of: wireAttempt, with: replacementWire).utf8)
        guard case .installed(let replacementActive) = cleanupEnrollment.complete(replacementReceipt, reservation: replacementPending,
                                                                                   now: now, clock: { now }) else { fatalError() }
        precondition(cleanupKeychain.pending[cleanupWire] == nil)
        precondition(cleanupStore.invalidate(replacementActive).complete)
        // Exercise a fresh failed cleanup and its retry below.
        let cleanupAgain = ActivityCatalog.activate(url: cleanupURL, endpoint: pending.endpoint)!
        let retryAttempt = cleanupEnrollment.prepare(expected: cleanupAgain)!
        let retryWire = retryAttempt.id.replacingOccurrences(of: "-", with: "").lowercased()
        guard case .ready = cleanupEnrollment.bind(status, reservation: retryAttempt) else { fatalError() }
        let retryReceipt = Data(String(decoding: receipt, as: UTF8.self).replacingOccurrences(of: wireAttempt, with: retryWire).utf8)
        cleanupKeychain.canDeletePending = false
        guard case .installed(let retryActive) = cleanupEnrollment.complete(retryReceipt, reservation: retryAttempt,
                                                                            now: now, clock: { now }) else { fatalError() }
        cleanupKeychain.canRead = false
        let lockedCleanup = cleanupStore.invalidate(retryActive)
        precondition(lockedCleanup.revoked && !lockedCleanup.complete && cleanupKeychain.data != nil)
        cleanupKeychain.canRead = true
        let blockedCleanup = cleanupStore.invalidate(retryActive)
        precondition(blockedCleanup.revoked && !blockedCleanup.complete && cleanupKeychain.data != nil)
        cleanupKeychain.canDeletePending = true
        precondition(cleanupStore.invalidate(retryActive).complete)
        precondition(cleanupKeychain.pending[retryWire] == nil && cleanupKeychain.data == nil)
        let cancelledURL = directory.appendingPathComponent("cancelled.sqlite")
        let cancelledKeychain = FakeKeychain()
        let cancelledStore = WidgetCredentialStore(catalogURL: cancelledURL, keychain: cancelledKeychain)
        let cancelledEnrollment = WidgetFencedEnrollment(store: cancelledStore)
        let cancelledInitial = ActivityCatalog.activate(url: cancelledURL, endpoint: pending.endpoint)!
        let cancelledAttempt = cancelledEnrollment.prepare(expected: cancelledInitial)!
        let cancelledWire = cancelledAttempt.id.replacingOccurrences(of: "-", with: "").lowercased()
        guard case .ready(let cancelledRequest) = cancelledEnrollment.bind(fixtureStatus, reservation: cancelledAttempt) else { fatalError() }
        var cancelledLock: OpaquePointer?
        precondition(sqlite3_open(cancelledURL.path, &cancelledLock) == SQLITE_OK)
        precondition(sqlite3_exec(cancelledLock, "BEGIN EXCLUSIVE", nil, nil, nil) == SQLITE_OK)
        let cancelled = cancelledEnrollment.cancel(cancelledAttempt)
        precondition(cancelled.revoked && !cancelled.complete && cancelledKeychain.pending[cancelledWire] == nil)
        let cancelledJournal = try JSONDecoder().decode(ActivityCatalog.EnrollmentJournal.self,
            from: Data(contentsOf: cancelledURL.appendingPathExtension("enrollment-pending")))
        precondition(cancelledJournal.phase == .armed && cancelledJournal.request == cancelledRequest)
        precondition(sqlite3_exec(cancelledLock, "ROLLBACK", nil, nil, nil) == SQLITE_OK)
        sqlite3_close(cancelledLock)
        precondition(cancelledStore.invalidate(cancelledInitial).complete)
        precondition(ActivityCatalog.pendingEnrollment(url: cancelledURL) == cancelledAttempt)
        let replacementAfterCancel = ActivityCatalog.activate(url: cancelledURL, endpoint: pending.endpoint)!
        precondition(cancelledEnrollment.cancel(cancelledAttempt).stale)
        precondition(ActivityCatalog.activation(url: cancelledURL) == replacementAfterCancel)
        let lockedCancelURL = directory.appendingPathComponent("locked-cancel.sqlite")
        let lockedCancelKeychain = FakeKeychain()
        let lockedCancelStore = WidgetCredentialStore(catalogURL: lockedCancelURL, keychain: lockedCancelKeychain)
        let lockedCancelEnrollment = WidgetFencedEnrollment(store: lockedCancelStore)
        let lockedCancelInitial = ActivityCatalog.activate(url: lockedCancelURL, endpoint: pending.endpoint)!
        let lockedCancelAttempt = lockedCancelEnrollment.prepare(expected: lockedCancelInitial)!
        let lockedCancelWire = lockedCancelAttempt.id.replacingOccurrences(of: "-", with: "").lowercased()
        guard case .ready(let lockedCancelRequest) = lockedCancelEnrollment.bind(fixtureStatus, reservation: lockedCancelAttempt) else { fatalError() }
        lockedCancelKeychain.canDeletePending = false
        let refusedDelete = lockedCancelEnrollment.cancel(lockedCancelAttempt)
        precondition(refusedDelete.revoked && !refusedDelete.complete && lockedCancelKeychain.pending[lockedCancelWire] != nil)
        var deletionCalled = false
        let failedSync = ActivityCatalog.invalidate(url: lockedCancelURL, expected: lockedCancelInitial,
            recovery: lockedCancelAttempt, journalSync: { _, _ in false }, recoveryCheck: { _ in true }) {
                deletionCalled = true
                return lockedCancelKeychain.deletePending(lockedCancelWire)
            }
        precondition(failedSync.revoked && !failedSync.complete && !deletionCalled)
        precondition(lockedCancelKeychain.pending[lockedCancelWire] != nil)
        let syncedControl = ActivityCatalog.invalidate(url: lockedCancelURL, expected: lockedCancelInitial,
            recovery: lockedCancelAttempt, journalSync: { _, _ in true }, recoveryCheck: { _ in true }) {
                deletionCalled = true
                return false
            }
        precondition(syncedControl.revoked && !syncedControl.complete && deletionCalled)
        lockedCancelKeychain.canDeletePending = true
        let interruptedActive = WidgetCredential(endpoint: credential.endpoint, workspaceID: credential.workspaceID,
            generation: UUID().uuidString, token: credential.token, expiresAt: credential.expiresAt,
            pendingAttempt: lockedCancelWire)
        lockedCancelKeychain.data = try JSONEncoder().encode(interruptedActive)
        let lockedMarkerURL = lockedCancelURL.appendingPathExtension("enrollment-pending")
        let savedMarker = try Data(contentsOf: lockedMarkerURL)
        try Data("damaged".utf8).write(to: lockedMarkerURL)
        precondition(!lockedCancelStore.invalidate(lockedCancelInitial).complete)
        precondition(lockedCancelKeychain.pending[lockedCancelWire] != nil && lockedCancelKeychain.data != nil)
        let realJournal = try JSONDecoder().decode(ActivityCatalog.EnrollmentJournal.self, from: savedMarker)
        var changedRequest = try JSONSerialization.jsonObject(with: lockedCancelRequest) as! [String: Any]
        changedRequest["commitment"] = String(repeating: "0", count: 64)
        let changedBytes = try JSONSerialization.data(withJSONObject: changedRequest)
        let mismatchedJournal = ActivityCatalog.EnrollmentJournal(version: 1, reservation: lockedCancelAttempt,
            revision: realJournal.revision, phase: .armed, workspaceID: realJournal.workspaceID, request: changedBytes)
        try JSONEncoder().encode(mismatchedJournal).write(to: lockedMarkerURL)
        precondition(!lockedCancelStore.invalidate(lockedCancelInitial).complete)
        precondition(lockedCancelKeychain.pending[lockedCancelWire] != nil)
        try savedMarker.write(to: lockedMarkerURL)
        precondition(lockedCancelStore.invalidate(lockedCancelInitial).complete)
        precondition(lockedCancelKeychain.pending[lockedCancelWire] == nil)
        let retainedJournal = try JSONDecoder().decode(ActivityCatalog.EnrollmentJournal.self,
            from: Data(contentsOf: lockedCancelURL.appendingPathExtension("enrollment-pending")))
        precondition(retainedJournal.request == lockedCancelRequest && retainedJournal.phase == .armed)
        let legacyURL = directory.appendingPathComponent("legacy-cancel.sqlite")
        let legacyKeychain = FakeKeychain()
        let legacyStore = WidgetCredentialStore(catalogURL: legacyURL, keychain: legacyKeychain)
        let legacyEnrollment = WidgetFencedEnrollment(store: legacyStore)
        let legacyInitial = ActivityCatalog.activate(url: legacyURL, endpoint: pending.endpoint)!
        let legacyAttempt = legacyEnrollment.prepare(expected: legacyInitial)!
        guard case .ready(let legacyRequest) = legacyEnrollment.bind(fixtureStatus, reservation: legacyAttempt) else { fatalError() }
        try JSONEncoder().encode(legacyAttempt).write(to: legacyURL.appendingPathExtension("enrollment-pending"))
        precondition(legacyEnrollment.cancel(legacyAttempt).complete)
        let migrated = try JSONDecoder().decode(ActivityCatalog.EnrollmentJournal.self,
            from: Data(contentsOf: legacyURL.appendingPathExtension("enrollment-pending")))
        precondition(migrated.phase == .armed && migrated.request == legacyRequest)
        let mismatchURL = directory.appendingPathComponent("mismatch.sqlite")
        let mismatchKeychain = FakeKeychain()
        let mismatchStore = WidgetCredentialStore(catalogURL: mismatchURL, keychain: mismatchKeychain)
        let mismatchEnrollment = WidgetFencedEnrollment(store: mismatchStore)
        let mismatchInitial = ActivityCatalog.activate(url: mismatchURL, endpoint: pending.endpoint)!
        let mismatchActive = seed(credential, expected: mismatchInitial, at: mismatchURL, in: mismatchKeychain)
        let mismatchAttempt = mismatchEnrollment.prepare(expected: mismatchActive)!
        let wrongIdentity = Data(String(decoding: fixtureStatus, as: UTF8.self)
            .replacingOccurrences(of: credential.workspaceID, with: String(repeating: "b", count: 64)).utf8)
        var mismatchLock: OpaquePointer?
        precondition(sqlite3_open(mismatchURL.path, &mismatchLock) == SQLITE_OK)
        precondition(sqlite3_exec(mismatchLock, "BEGIN EXCLUSIVE", nil, nil, nil) == SQLITE_OK)
        guard case .identityMismatch(let mismatchResult) = mismatchEnrollment.bind(wrongIdentity, reservation: mismatchAttempt) else { fatalError() }
        precondition(mismatchResult.revoked && !mismatchResult.complete && mismatchKeychain.data == nil)
        precondition(sqlite3_exec(mismatchLock, "ROLLBACK", nil, nil, nil) == SQLITE_OK)
        sqlite3_close(mismatchLock)
        precondition(mismatchStore.invalidate(mismatchActive).complete)
        precondition(ActivityCatalog.pendingEnrollment(url: mismatchURL) == mismatchAttempt)
        let mismatchReplacement = ActivityCatalog.activate(url: mismatchURL, endpoint: pending.endpoint)!
        let replacementBound = seed(credential, expected: mismatchReplacement, at: mismatchURL, in: mismatchKeychain)
        guard case .identityMismatch(let staleMismatch) = mismatchEnrollment.bind(wrongIdentity, reservation: mismatchAttempt) else { fatalError() }
        precondition(staleMismatch.stale && mismatchStore.capture(now: now)?.activation == replacementBound)
        let transactionURL = directory.appendingPathComponent("receipt-transaction.sqlite")
        let transactionKeychain = FakeKeychain()
        let transactionStore = WidgetCredentialStore(catalogURL: transactionURL, keychain: transactionKeychain)
        let transactionEnrollment = WidgetFencedEnrollment(store: transactionStore)
        let transactionInitial = ActivityCatalog.activate(url: transactionURL, endpoint: pending.endpoint)!
        let transactionAttempt = transactionEnrollment.prepare(expected: transactionInitial)!
        let transactionWire = transactionAttempt.id.replacingOccurrences(of: "-", with: "").lowercased()
        guard case .ready(let transactionRequest) = transactionEnrollment.bind(fixtureStatus, reservation: transactionAttempt) else { fatalError() }
        let transactionPending = try JSONSerialization.jsonObject(with: transactionKeychain.pending[transactionWire]!) as! [String: Any]
        let transactionGeneration = transactionPending["generation"] as! String
        let transactionReceipt = fixtureReceipt(transactionAttempt)
        guard case .issue(let admitted) = ActivityCatalog.admitEnrollmentIssue(url: transactionURL,
            expected: transactionAttempt, request: transactionRequest) else { fatalError() }
        precondition(admitted == transactionRequest)
        let plan = ActivityCatalog.recordEnrollmentReceipt(url: transactionURL, expected: transactionAttempt,
            workspaceID: credential.workspaceID, request: transactionRequest, receipt: transactionReceipt,
            generation: transactionGeneration)!
        let repeatedPlan = ActivityCatalog.recordEnrollmentReceipt(url: transactionURL, expected: transactionAttempt,
            workspaceID: credential.workspaceID, request: transactionRequest, receipt: transactionReceipt,
            generation: transactionGeneration)
        precondition(repeatedPlan == plan && ActivityCatalog.savedEnrollmentReceipt(url: transactionURL, expected: transactionAttempt) == transactionReceipt)
        guard case .receipt(let admittedReceipt) = ActivityCatalog.admitEnrollmentIssue(url: transactionURL,
            expected: transactionAttempt, request: transactionRequest) else { fatalError() }
        precondition(admittedReceipt == transactionReceipt)
        var transactionWrites = 0
        let failedAuthority = ActivityCatalog.installReceipted(url: transactionURL, expected: transactionAttempt, receipt: plan,
            syncAuthority: { _, _ in false }, install: { transactionWrites += 1; return true }, authorize: { true }, cleanup: { true })
        precondition(failedAuthority == nil && transactionWrites == 1)
        precondition(ActivityCatalog.savedEnrollmentReceipt(url: transactionURL, expected: transactionAttempt) == transactionReceipt)
        let expiredAuthority = ActivityCatalog.installReceipted(url: transactionURL, expected: transactionAttempt, receipt: plan,
            install: { true }, authorize: { false }, cleanup: { true })
        precondition(expiredAuthority == nil && ActivityCatalog.activation(url: transactionURL) == nil)
        let published = ActivityCatalog.installReceipted(url: transactionURL, expected: transactionAttempt, receipt: plan,
            install: { true }, authorize: { true }, cleanup: { false })!
        precondition(!published.1 && published.0.id == plan.activationID)
        precondition(ActivityCatalog.pendingEnrollment(url: transactionURL) == transactionAttempt)
        precondition(ActivityCatalog.finishReceipted(url: transactionURL, expected: transactionAttempt,
            verify: { _, _ in false }, cleanup: { true }) == nil)
        precondition(ActivityCatalog.finishReceipted(url: transactionURL, expected: transactionAttempt,
            verify: { _, _ in true }, cleanup: { true }) == published.0)
        precondition(ActivityCatalog.pendingEnrollment(url: transactionURL) == nil)
        let expiredURL = directory.appendingPathComponent("expired-receipt.sqlite")
        let expiredKeychain = FakeKeychain()
        let expiredStore = WidgetCredentialStore(catalogURL: expiredURL, keychain: expiredKeychain)
        let expiredEnrollment = WidgetFencedEnrollment(store: expiredStore)
        let expiredInitial = ActivityCatalog.activate(url: expiredURL, endpoint: pending.endpoint)!
        let expiredAttempt = expiredEnrollment.prepare(expected: expiredInitial)!
        let expiredWire = expiredAttempt.id.replacingOccurrences(of: "-", with: "").lowercased()
        guard case .ready(let expiredRequest) = expiredEnrollment.bind(fixtureStatus, reservation: expiredAttempt) else { fatalError() }
        let expiredReceipt = fixtureReceipt(expiredAttempt)
        let expiredPending = try JSONSerialization.jsonObject(with: expiredKeychain.pending[expiredWire]!) as! [String: Any]
        let expiredGeneration = expiredPending["generation"] as! String
        _ = ActivityCatalog.recordEnrollmentReceipt(url: expiredURL, expected: expiredAttempt, workspaceID: credential.workspaceID,
            request: expiredRequest, receipt: expiredReceipt, generation: expiredGeneration)!
        let interruptedItem = WidgetCredential(endpoint: pending.endpoint, workspaceID: credential.workspaceID,
            generation: expiredGeneration, token: expiredPending["secret"] as! String,
            expiresAt: now.addingTimeInterval(3600), pendingAttempt: expiredWire)
        expiredKeychain.data = try JSONEncoder().encode(interruptedItem)
        expiredKeychain.canDeletePending = false
        guard case .identityMismatch(let expiredClear) = WidgetFencedEnrollment(store: expiredStore).complete(expiredReceipt,
            reservation: expiredAttempt, now: now.addingTimeInterval(3601), clock: { now.addingTimeInterval(3601) }) else { fatalError() }
        precondition(expiredClear.revoked && !expiredClear.complete && expiredKeychain.pending[expiredWire] != nil)
        expiredKeychain.canDeletePending = true
        let resumedExpired = WidgetFencedEnrollment(store: expiredStore).finishExpiredReceipt(expiredAttempt,
            clock: { now.addingTimeInterval(3601) })!
        precondition(resumedExpired.complete && expiredKeychain.pending[expiredWire] == nil && expiredKeychain.data == nil)
        let expiredPin = ActivityCatalog.activation(url: expiredURL)!
        precondition(ActivityCatalog.pendingEnrollment(url: expiredURL) == nil && expiredPin.workspaceID == credential.workspaceID &&
                     expiredPin.credentialGeneration == nil && expiredStore.capture(now: now) == nil)
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
