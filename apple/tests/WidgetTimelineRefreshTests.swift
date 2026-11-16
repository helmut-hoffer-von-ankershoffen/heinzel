import Foundation

// Fixed fictional identities for this isolated fixture; never production configuration.
private let fixtureEndpoint = "https://example.test"

private final class TimelineKeychain: WidgetCredentialKeychain {
    var data: Data?
    var deletes = 0
    var unavailable = false
    func preflight() -> Bool { true }
    func read() -> Data? { unavailable ? nil : data }
    func readStatus() -> WidgetKeychainRead { unavailable ? .unavailable : (data.map(WidgetKeychainRead.data) ?? .missing) }
    func write(_ value: Data) -> Bool { data = value; return true }
    func delete() -> Bool { deletes += 1; return false }
    func readPending(_: String) -> Data? { nil }
    func createPending(_: Data, attempt _: String) -> Bool { false }
    func updatePending(_: Data, attempt _: String) -> Bool { false }
    func deletePending(_: String) -> Bool { false }
}

@main struct WidgetTimelineRefreshTests {
    @MainActor static func main() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("widget-timeline-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("catalog.sqlite")
        _ = ActivityCatalog(url: url)!
        let keychain = TimelineKeychain()
        let store = WidgetCredentialStore(catalogURL: url, keychain: keychain)
        let workspace = String(repeating: "a", count: 64)
        func activate() throws -> ActivityCatalog.Activation {
            let pending = ActivityCatalog.activate(url: url, endpoint: fixtureEndpoint)!
            let credential = WidgetCredential(endpoint: pending.endpoint, workspaceID: workspace,
                generation: UUID().uuidString, token: "hzw_" + String(repeating: "x", count: 40),
                expiresAt: .now.addingTimeInterval(3600))
            let data = try JSONEncoder().encode(credential)
            return ActivityCatalog.installCredential(url: url, expected: pending, workspaceID: workspace,
                                                      generation: credential.generation, install: { keychain.write(data) })!
        }
        let first = try activate()
        let generated = ISO8601DateFormatter().string(from: .now)
        let body = Data("""
        {"version":1,"workspace_id":"\(workspace)","generated_at":"\(generated)","fresh_for_seconds":300,"awake":0,"waiting":0,"roster":[]}
        """.utf8)
        let snapshot = try JSONDecoder().decode(WidgetSnapshotEnvelope.self, from: body)
        let scheduler = WidgetTimelineRefresh()
        var requests = 0
        var pending: CheckedContinuation<WidgetSnapshotOutcome, Never>?
        var firstCapture: WidgetCredentialCapture?
        let delayed: @MainActor (WidgetCredentialCapture) async -> WidgetSnapshotOutcome = { capture in
            requests += 1
            firstCapture = capture
            return await withCheckedContinuation { pending = $0 }
        }
        let publish: @MainActor (WidgetSnapshotEnvelope, WidgetCredentialCapture) -> Bool = { _, _ in true }
        let a = Task { await scheduler.refresh(store: store, fetch: delayed, publish: publish) }
        while pending == nil { await Task.yield() }
        let b = Task { await scheduler.refresh(store: store, fetch: delayed, publish: publish) }
        for _ in 0..<10 { await Task.yield() }
        precondition(requests == 1)
        pending!.resume(returning: .received(snapshot, firstCapture!))
        let aValue = await a.value
        guard case .permitted(let aActivation, let expiry) = aValue, aActivation == first,
              case .permitted(let bActivation, _) = await b.value, bActivation == first else {
            preconditionFailure("coalesced success did not authorize the same activation")
        }
        precondition(aValue.mayDisplay(at: expiry.addingTimeInterval(-1)))
        precondition(!aValue.mayDisplay(at: expiry) && !aValue.mayDisplay(at: expiry.addingTimeInterval(1)))
        let cached = ActivityWidgetData(awake: 1, waiting: 0, updatedAt: .now,
                                        heinzels: [], workspaceScope: first.scope)
        var clockCalls = 0
        let expiredDuringRead = scheduler.snapshot(access: aValue, catalogURL: url, read: { cached }, clock: {
            clockCalls += 1
            return expiry.addingTimeInterval(clockCalls == 1 ? -1 : 1)
        })
        precondition(expiredDuringRead == nil)
        let offline = await scheduler.refresh(store: store, fetch: { _ in .unavailable }, publish: publish)
        guard case .permitted(let offlineActivation, _) = offline, offlineActivation == first else {
            preconditionFailure("offline response lost an unexpired process-local permit")
        }
        let afterRestart = await WidgetTimelineRefresh().refresh(store: store, fetch: { _ in .unavailable }, publish: publish)
        guard case .hidden = afterRestart else { preconditionFailure("offline restart exposed cached data") }
        let publicationFailure = await WidgetTimelineRefresh().refresh(store: store,
            fetch: { .received(snapshot, $0) }, publish: { _, _ in false })
        guard case .hidden = publicationFailure else { preconditionFailure("failed publication granted a permit") }
        let failedRejection = await scheduler.refresh(store: store, fetch: { capture in
            .rejected(capture, .init(revoked: false, purged: false))
        }, publish: publish)
        guard case .hidden = failedRejection else { preconditionFailure("rejection retained access") }
        precondition(scheduler.snapshot(access: aValue, catalogURL: url, read: { cached }) == nil)

        let rotationScheduler = WidgetTimelineRefresh()
        var oldPending: CheckedContinuation<WidgetSnapshotOutcome, Never>?
        var oldCapture: WidgetCredentialCapture?
        let old = Task { await rotationScheduler.refresh(store: store, fetch: { capture in
            oldCapture = capture
            return await withCheckedContinuation { oldPending = $0 }
        }, publish: publish) }
        while oldPending == nil { await Task.yield() }
        let replacement = try activate()
        let newAccess = await rotationScheduler.refresh(store: store, fetch: { .received(snapshot, $0) }, publish: publish)
        guard case .permitted(let newActivation, _) = newAccess, newActivation == replacement else {
            preconditionFailure("replacement was not independently authorized")
        }
        oldPending!.resume(returning: .rejected(oldCapture!, .init(revoked: false, purged: false, stale: true)))
        guard case .hidden = await old.value else { preconditionFailure("old rejection exposed a replaced activation") }
        let replacementOffline = await rotationScheduler.refresh(store: store, fetch: { _ in .unavailable }, publish: publish)
        guard case .permitted(let preserved, _) = replacementOffline, preserved == replacement else {
            preconditionFailure("old rejection affected replacement permission")
        }
        let rejected = await rotationScheduler.refresh(store: store, fetch: { capture in
            .rejected(capture, .init(revoked: false, purged: false))
        }, publish: publish)
        guard case .hidden = rejected else { preconditionFailure("failed cleanup retained a permit") }
        var retriedNetwork = false
        let afterRejection = await rotationScheduler.refresh(store: store, fetch: { _ in
            retriedNetwork = true
            return .unavailable
        }, publish: publish)
        guard case .hidden = afterRejection, !retriedNetwork else { preconditionFailure("rejected activation retried network or exposed data") }
        precondition(ActivityCatalog.pendingCleanup(url: url) == replacement)
        let previousDeletes = keychain.deletes
        let restartedCleanup = await WidgetTimelineRefresh().refresh(store: store, fetch: { _ in
            preconditionFailure("cleanup attempted a network fetch")
        }, publish: publish)
        guard case .hidden = restartedCleanup else { preconditionFailure("pending cleanup exposed data") }
        precondition(keychain.deletes > previousDeletes)

        let secondURL = directory.appendingPathComponent("unavailable-keychain.sqlite")
        _ = ActivityCatalog(url: secondURL)!
        let secondKeychain = TimelineKeychain()
        let secondStore = WidgetCredentialStore(catalogURL: secondURL, keychain: secondKeychain)
        let secondPending = ActivityCatalog.activate(url: secondURL, endpoint: fixtureEndpoint)!
        let secondCredential = WidgetCredential(endpoint: secondPending.endpoint, workspaceID: workspace,
            generation: UUID().uuidString, token: "hzw_" + String(repeating: "y", count: 40),
            expiresAt: .now.addingTimeInterval(3600))
        let secondData = try JSONEncoder().encode(secondCredential)
        let secondActivation = ActivityCatalog.installCredential(url: secondURL, expected: secondPending,
            workspaceID: workspace, generation: secondCredential.generation, install: { secondKeychain.write(secondData) })!
        let secondScheduler = WidgetTimelineRefresh()
        let firstRejected = await secondScheduler.refresh(store: secondStore, fetch: { capture in
            .rejected(capture, .init(revoked: false, purged: false))
        }, publish: publish)
        guard case .hidden = firstRejected else { preconditionFailure("failed revocation exposed data") }
        secondKeychain.unavailable = true
        let retryUnavailable = await secondScheduler.refresh(store: secondStore, fetch: { _ in
            preconditionFailure("unavailable credential attempted network")
        }, publish: publish)
        guard case .hidden = retryUnavailable else { preconditionFailure("unavailable cleanup exposed data") }
        precondition(ActivityCatalog.pendingCleanup(url: secondURL) == secondActivation)
        secondKeychain.unavailable = false
        let retryRecovered = await secondScheduler.refresh(store: secondStore, fetch: { _ in
            preconditionFailure("pending cleanup attempted network")
        }, publish: publish)
        guard case .hidden = retryRecovered, secondKeychain.deletes > 0 else {
            preconditionFailure("remembered cleanup did not progress after Keychain recovery")
        }
        print("PASS: coalescing, restart, publication, rotation and rejected credential fencing")
    }
}
