import Foundation

// Fixed fictional identities for this isolated fixture; never production configuration.
private let fixtureEndpoint = "https://example.test"
private let otherFixtureEndpoint = "https://other.test"
private let insecureFixtureEndpoint = "http://example.test"

// Standalone, isolated model probe; does not read or write shared widget defaults.
@main
struct WidgetStatusTests {
    static func testMenuStatesAndWorkspaceInvalidation() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        func snapshot(_ activity: String, state: String = "active") -> ActivityWidgetData {
            ActivityWidgetData(awake: 1, waiting: 0, updatedAt: now,
                heinzels: [WidgetHeinzel(id: "example", name: "Example", state: state, activity: activity,
                    asks: nil, lastApproved: nil, nextWake: nil, portrait: nil)], workspaceScope: "example")
        }
        for (activity, expected) in [("idle", MacMenuStatus.healthy), ("working", .working),
                                      ("waiting_for_you", .attention), ("stuck", .attention), ("overloaded", .attention)] {
            precondition(MacMenuStatus.resolve(snapshot(activity), scope: "example", available: true, now: now) == expected)
            precondition(!expected.label.isEmpty && !expected.symbol.isEmpty)
        }
        precondition(MacMenuStatus.resolve(snapshot("working", state: "retired"), scope: "example", available: true, now: now) == .healthy)
        precondition(MacMenuStatus.resolve(snapshot("working"), scope: "other", available: true, now: now) == .stale)
        precondition(MacMenuStatus.resolve(snapshot("working"), scope: "example", available: false, now: now) == .offline)
        precondition(MacMenuStatus.resolve(snapshot("working"), scope: "example", available: true, now: now.addingTimeInterval(300)) == .stale)
    }

    static func testMenuMixedRosterGrouping() {
        func item(_ id: String, _ activity: String, state: String = "active") -> WidgetHeinzel {
            WidgetHeinzel(id: id, name: "Example", state: state, activity: activity,
                asks: nil, lastApproved: nil, nextWake: nil, portrait: nil)
        }
        let snapshot = ActivityWidgetData(awake: 1, waiting: 1,
            updatedAt: Date(timeIntervalSince1970: 1_800_000_000),
            heinzels: [item("a", "working"), item("b", "waiting_for_you"), item("c", "stuck", state: "retired")])
        precondition(snapshot.menuNeedsAttention.map(\.id) == ["b"])
        precondition(snapshot.roster.map(\.id) == ["a", "b", "c"])
    }

    static func testMenuOpenClosedCadence() {
        precondition(MacMenuStatus.interval(open: true) == 30)
        precondition(MacMenuStatus.interval(open: false) == 300)
    }

    #if os(macOS)
    @MainActor
    static func testHostRefreshLifecycle() async {
        final class Source {}
        final class Job {
            let delay: TimeInterval
            let action: @MainActor () async -> Void
            var cancelled = false
            var task: Task<Void, Never>?
            @MainActor func run() async {
                let running = Task { @MainActor in await action() }
                task = running
                await running.value
            }
            init(_ delay: TimeInterval, _ action: @escaping @MainActor () async -> Void) {
                self.delay = delay; self.action = action
            }
        }
        var jobs: [Job] = []
        let scheduler = MacHostRefresh { delay, action in
            let job = Job(delay, action)
            jobs.append(job)
            return { job.cancelled = true; job.task?.cancel() }
        }
        let first = Source(), second = Source()
        var requests: [ObjectIdentifier] = []
        var publications = 0
        scheduler.didRefresh = { publications += 1 }
        func install(_ source: Source, revision: Int = 0) {
            scheduler.install(owner: ObjectIdentifier(source), revision: revision) {
                requests.append(ObjectIdentifier(source))
            }
        }
        install(first)
        precondition(jobs.count == 1 && jobs[0].delay == 0)
        await jobs[0].action()
        precondition(requests == [ObjectIdentifier(first)] && jobs.last!.delay == 300)
        let closed = jobs.last!
        scheduler.setVisible(true, surface: "menu")
        precondition(closed.cancelled && jobs.last!.delay == 0)
        await jobs.last!.action()
        precondition(jobs.last!.delay == 30)
        let open = jobs.last!
        scheduler.setVisible(true, surface: "quick-chat")
        scheduler.setVisible(true, surface: "workspace")
        scheduler.setVisible(false, surface: "menu")
        scheduler.setVisible(false, surface: "workspace")
        precondition(jobs.last === open && !open.cancelled)
        // Repeated visibility and reattachment preserve the host source and timer.
        scheduler.setVisible(true, surface: "quick-chat")
        install(first)
        precondition(jobs.last === open)
        scheduler.setVisible(false, surface: "quick-chat")
        precondition(open.cancelled)
        await jobs.last!.action()
        precondition(jobs.last!.delay == 300)
        let old = jobs.last!
        install(second)
        precondition(old.cancelled)
        let count = requests.count
        await old.action() // A late wake from the old workspace cannot fetch.
        precondition(requests.count == count)
        scheduler.remove(owner: ObjectIdentifier(first))
        precondition(!jobs.last!.cancelled)
        await jobs.last!.action()
        precondition(requests.last == ObjectIdentifier(second))
        let before = publications
        var cancelledInFlight = false
        scheduler.install(owner: ObjectIdentifier(second), revision: 1) {
            requests.append(ObjectIdentifier(second))
            // Replacement while a refresh is suspended must fence its completion.
            install(first, revision: 2)
            cancelledInFlight = Task.isCancelled
        }
        await jobs.last!.run()
        precondition(cancelledInFlight && publications == before)
        let replacement = jobs.last!
        await replacement.action()
        precondition(requests.last == ObjectIdentifier(first) && publications == before + 1)
        let pending = jobs.last!
        scheduler.remove(owner: ObjectIdentifier(first))
        precondition(pending.cancelled)
        let stoppedCount = requests.count
        await pending.action()
        precondition(requests.count == stoppedCount)

        // A cancelled WebKit operation can remain suspended. Visibility changes
        // and owner replacements must wait for its actual completion.
        for opening in [true, false] {
            var release: CheckedContinuation<Void, Never>?
            var started: CheckedContinuation<Void, Never>?
            var active = 0
            var maximumActive = 0
            let beforeDelay = publications
            scheduler.install(owner: ObjectIdentifier(first), revision: opening ? 10 : 11) {
                active += 1
                maximumActive = max(maximumActive, active)
                await withCheckedContinuation { continuation in
                    release = continuation
                    started?.resume()
                }
                precondition(Task.isCancelled)
                active -= 1
            }
            let delayedJob = jobs.last!
            let running = Task { @MainActor in await delayedJob.run() }
            await withCheckedContinuation { continuation in
                if release != nil { continuation.resume() } else { started = continuation }
            }
            let jobCount = jobs.count
            scheduler.setVisible(opening, surface: "new-window")
            precondition(delayedJob.cancelled && jobs.count == jobCount)
            // Replace the workspace while the old operation still owns the slot.
            scheduler.remove(owner: ObjectIdentifier(first))
            scheduler.install(owner: ObjectIdentifier(second), revision: opening ? 20 : 21) {
                active += 1
                maximumActive = max(maximumActive, active)
                requests.append(ObjectIdentifier(second))
                active -= 1
            }
            scheduler.remove(owner: ObjectIdentifier(first))
            precondition(jobs.count == jobCount && active == 1)
            release!.resume()
            await running.value
            precondition(publications == beforeDelay && jobs.count == jobCount + 1)
            precondition(jobs.last!.delay == 0)
            await jobs.last!.run()
            precondition(maximumActive == 1 && publications == beforeDelay + 1)
            precondition(requests.last == ObjectIdentifier(second))
            precondition(jobs.last!.delay == (opening ? 30 : 300))
            scheduler.remove(owner: ObjectIdentifier(second))
        }
    }
    #endif

    @MainActor
    static func main() async throws {
        #if os(macOS)
        await testHostRefreshLifecycle()
        #endif
        testMenuStatesAndWorkspaceInvalidation()
        testMenuOpenClosedCadence()
        testMenuMixedRosterGrouping()
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
        var menuSnapshot = snapshot
        menuSnapshot.workspaceScope = "fixture-workspace"
        precondition(menuSnapshot.visibleInStatusMenu(scope: "fixture-workspace", at: date))
        precondition(!menuSnapshot.visibleInStatusMenu(scope: nil, at: date))
        precondition(!menuSnapshot.visibleInStatusMenu(scope: "other", at: date))
        precondition(!menuSnapshot.visibleInStatusMenu(scope: "fixture-workspace", at: snapshot.expiresAt))
        precondition(!menuSnapshot.visibleInStatusMenu(scope: "fixture-workspace", at: date.addingTimeInterval(-1)))
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
