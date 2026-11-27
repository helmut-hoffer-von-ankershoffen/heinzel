import Foundation

@main struct WorkspaceFooterGeometryTests {
    static func checkDuplicateAddresses() {
        let first = RecentWorkspace(address: "https://first.example", hostName: "Studio", lastUsed: .now)
        let second = RecentWorkspace(address: "https://second.example", hostName: "studio", lastUsed: .now)
        let unique = RecentWorkspace(address: "https://garden.example", lastUsed: .now)
        let rows = [first, second, unique]
        let firstURL = URL(string: first.address)!
        let secondURL = URL(string: second.address)!
        let proxy = WorkspaceHostProbe(.online, nil, address: "192.0.2.18")
        let hostLabels = [first.address: "first.example", second.address: "second.example"]
        precondition(RecentWorkspace.disambiguatingAddresses(for: rows, probes: [:]) == hostLabels)
        precondition(RecentWorkspace.disambiguatingAddresses(for: rows, probes: [firstURL: proxy, secondURL: proxy]) == hostLabels)
        let direct = WorkspaceHostProbe(.online, nil, address: "2001:db8::1")
        let distinct = RecentWorkspace.disambiguatingAddresses(for: rows, probes: [firstURL: proxy, secondURL: direct])
        precondition(distinct == [first.address: "192.0.2.18", second.address: "[2001:db8::1]"])
    }

    @MainActor static func main() async throws {
        let address = "https://studio.example"
        let manual = RecentWorkspace(address: address, lastUsed: .now, source: .pasted)
        let synced = RecentWorkspace(address: address, lastUsed: .now, source: .iCloud)
        let legacy = RecentWorkspace(address: address, lastUsed: .distantPast)
        precondition(manual.isUserAdded && !synced.isUserAdded && !legacy.isUserAdded)
        checkDuplicateAddresses()
        let privateURL = address + "/#token=" + String(repeating: "x", count: 40)
        precondition(Endpoint.signInInputIssue(privateURL, expectedOrigin: nil) == nil)
        precondition(Endpoint.signInInputIssue(privateURL, expectedOrigin: URL(string: address)) == nil)
        precondition(Endpoint.signInInputIssue(address, expectedOrigin: nil) != nil)
        precondition(Endpoint.signInInputIssue(privateURL, expectedOrigin: URL(string: "https://other.example")) != nil)
        precondition(Endpoint.signInInputIssue("heinzel://connect?server=https://studio.example", expectedOrigin: nil) != nil)
        let older = WorkspaceVersionObservation(version: "0.9.50", observedAt: .distantPast, installedVersion: "0.9.51")
        let newer = WorkspaceVersionObservation(version: "0.9.52", observedAt: .now)
        precondition(older.differentInstalledVersion == "0.9.51")
        precondition(newer.differentInstalledVersion == nil)
        for value in ["0.9.52", "", "Unknown"] {
            var observation = newer
            observation.installedVersion = value
            precondition(observation.differentInstalledVersion == nil)
        }
        precondition(WorkspaceVersionObservation.lastLocal(in: [address: newer]) == nil)
        precondition(WorkspaceVersionObservation.lastLocal(in: [address: newer, "http://127.0.0.1:8081": older])?.version == "0.9.50")
        precondition(WorkspaceVersionObservation.lastLocal(in: ["http://127.0.0.1:8081": older, "http://127.0.0.1:8082": newer]) == newer)
        let online = WorkspaceHostProbe(.online, nil)
        let offline = WorkspaceHostProbe(error: URLError(.cannotConnectToHost))
        precondition(WorkspaceHostProbe.remoteReachability(networkAvailable: nil, probe: online) == .online)
        precondition(WorkspaceHostProbe.remoteReachability(networkAvailable: false, probe: offline) == .unknown)
        precondition(WorkspaceHostProbe.remoteReachability(networkAvailable: true, probe: nil) == .unknown)
        precondition(WorkspaceHostProbe.remoteReachability(networkAvailable: true, probe: online) == .online)
        precondition(WorkspaceHostProbe.remoteReachability(networkAvailable: true, probe: offline) == .offline)
        precondition(WorkspaceHostProbe(error: URLError(.notConnectedToInternet)).reachability == .unknown)
        precondition(WorkspaceHostProbe.canAttemptConnection(networkAvailable: nil, probe: nil))
        precondition(WorkspaceHostProbe.canAttemptConnection(networkAvailable: false, probe: online))
        precondition(!WorkspaceHostProbe.canAttemptConnection(networkAvailable: true, probe: offline))
        precondition(WorkspaceHostProbe.canAttemptConnection(networkAvailable: true, probe: nil))
        precondition(WorkspaceHostProbe.canAttemptConnection(networkAvailable: true, probe: online))
        precondition(WorkspaceHostProbe.remoteReachability(networkAvailable: false, probe: online) == .online)
        precondition(!WorkspaceHostProbe.canAttemptConnection(networkAvailable: false, probe: nil))
        precondition(!WorkspaceHostProbe.canAttemptConnection(networkAvailable: false, probe: offline))
        var cloud = WorkspaceCloudDirectory()
        precondition(cloud.access == .checking)
        cloud.update([])
        precondition(cloud.access == .empty)
        cloud.update([synced])
        precondition(cloud.access == .available && cloud.workspaces.count == 1)
        cloud.update(nil)
        precondition(cloud.access == .unavailable && cloud.workspaces.first?.address == address)
        cloud.observedSignIn(synced)
        precondition(cloud.access == .unavailable && cloud.workspaces.count == 1)
        cloud.update([])
        precondition(cloud.access == .empty && cloud.workspaces.isEmpty)
        verifyFooterHandoff()
        verifyFooterReceiver()
        let viewport = CGSize(width: 390, height: 844)
        let valid: [String: NSNumber] = ["x": 0, "y": 762, "width": 100, "height": 48,
                                                "viewportWidth": 390, "viewportHeight": 844]
        precondition(WorkspaceFooterGeometry.slot(valid, viewport: viewport, width: 100, bottom: 34) != nil)
        for (key, value) in [("x", -1.0), ("y", 200.0), ("width", 181.0), ("height", 20.0),
                             ("viewportWidth", 100.0), ("viewportHeight", Double.nan)] {
            var invalid = valid
            invalid[key] = NSNumber(value: value)
            precondition(WorkspaceFooterGeometry.slot(invalid, viewport: viewport, width: 100, bottom: 34) == nil)
        }
        var boolean = valid
        boolean["x"] = NSNumber(value: true)
        precondition(WorkspaceFooterGeometry.slot(boolean, viewport: viewport, width: 100, bottom: 34) == nil)
        let landscape: [String: NSNumber] = ["x": 44, "y": 308, "width": 120, "height": 48,
                                                    "viewportWidth": 844, "viewportHeight": 390]
        precondition(WorkspaceFooterGeometry.slot(landscape, viewport: CGSize(width: 844, height: 390),
                                                   width: 120, bottom: 34) != nil)
        precondition(WorkspaceFooterGeometry.deliveryIsCurrent(revision: 1, currentRevision: 1,
                                                                requested: (180, 34, 5), current: (180, 34, 5)))
        precondition(!WorkspaceFooterGeometry.deliveryIsCurrent(revision: 1, currentRevision: 1,
                                                                 requested: (180, 34, 5), current: (180, 34, 6)))
        precondition(!WorkspaceFooterGeometry.deliveryIsCurrent(revision: 1, currentRevision: 2,
                                                                 requested: (180, 34, 5), current: (180, 34, 5)))
        var versions: [String: WorkspaceVersionObservation] = [:]
        let origin = "https://workspace.example"
        let observed = WorkspaceVersionObservation(version: "0.9.49", observedAt: .now)
        let now = Date(timeIntervalSince1970: 2_000_000)
        let parsed = WorkspaceVersionObservation.fromPayload(["version": "0.9.50", "installedVersion": "0.9.50",
            "installedUpdatedAt": NSNumber(value: 1_999_000_000), "daemonLastVersion": "0.9.49",
            "daemonLastObservedAt": NSNumber(value: 1_998_000_000), "daemonObservationStale": true], now: now)
        precondition(parsed?.installedUpdatedAt == Date(timeIntervalSince1970: 1_999_000))
        precondition(parsed?.daemonLastObservedAt == Date(timeIntervalSince1970: 1_998_000))
        let future = WorkspaceVersionObservation.fromPayload(["version": "0.9.50", "installedVersion": "0.9.50",
            "installedUpdatedAt": NSNumber(value: 3_000_000_000)], now: now)
        precondition(future?.installedUpdatedAt == nil)
        let slightlyFuture = WorkspaceVersionObservation.fromPayload(["version": "0.9.50", "installedVersion": "0.9.50",
            "installedUpdatedAt": NSNumber(value: 2_000_001_000)], now: now)
        precondition(slightlyFuture?.installedUpdatedAt == nil)
        try verifyBackendReport(now: now)
        precondition(WorkspaceVersionObservation.record(observed, origin: origin, selectedOrigin: origin,
                                                        requestRevision: 1, currentRevision: 1, into: &versions))
        versions.removeValue(forKey: origin)
        precondition(!WorkspaceVersionObservation.record(observed, origin: origin, selectedOrigin: "",
                                                         requestRevision: 1, currentRevision: 1, into: &versions))
        precondition(versions.isEmpty)
        precondition(!WorkspaceVersionObservation.record(observed, origin: origin, selectedOrigin: origin,
                                                         requestRevision: 1, currentRevision: 2, into: &versions))
        try verifyAppBuildObservation(now: now)
        let token = ClientIdentity.header(appVersion: "0.9.54", backendVersion: nil, machine: "Demo's Air (2)")
        precondition(token == "flavor=native;app=0.9.54;backend=unknown;machine=Demo%27s%20Air%20%282%29")
        precondition(ClientIdentity.header(appVersion: nil, backendVersion: "", machine: nil)
                     == "flavor=native;app=unknown;backend=unknown;machine=unknown")
        let bounded = String(repeating: "𐐀 ", count: 32)
        precondition(ClientIdentity.header(appVersion: nil, backendVersion: nil, machine: bounded + "extra")
                     == "flavor=native;app=unknown;backend=unknown;machine="
                        + bounded.addingPercentEncoding(withAllowedCharacters: .alphanumerics)!)
        ClientIdentity.backendVersion = "0.9.54"
        precondition(ClientIdentity.backendVersion == "0.9.54")
        ClientIdentity.backendVersion = nil
        verifyCloudSyncPreference()
        await verifyVersionRefresh(now: now)
        print("PASS: native footer slot is bounded to the current viewport")
    }

    private static func verifyBackendReport(now: Date) throws {
        // The responding host is a day ahead of the client. Only its own two clocks determine uptime.
        let received = now.addingTimeInterval(86_400).timeIntervalSince1970 * 1000
        let payload: [String: Any] = [
            "version": "0.9.58", "backendObservedAt": received, "webRunningSince": received - 301_000,
            "backendIP": "2001:db8::58",
            "webObservedSince": received - 250_000, "installedVersion": "0.9.57", "installedUpdatedAt": received - 50_000,
            "daemonRegisteredAt": received - 100_000, "daemonRunningVersion": "0.9.56",
            "daemonRunningSince": received - 600_000, "daemonStatusObservedAt": received + 50,
            "daemonOwned": true, "daemonLoaded": false, "daemonObservationStale": 1,
        ]
        guard let version = WorkspaceVersionObservation.fromPayload(payload, now: now) else {
            preconditionFailure("An authenticated version report must survive client clock skew")
        }
        precondition(version.uptimeAtObservation == 301 && version.observedAt == now)
        precondition(version.backendIP == "2001:db8::58")
        var ipv4 = payload
        ipv4["backendIP"] = "127.0.0.1"
        precondition(WorkspaceVersionObservation.fromPayload(ipv4, now: now)?.backendIP == "127.0.0.1")
        var records: [String: WorkspaceVersionObservation] = [:]
        precondition(WorkspaceVersionObservation.record(version, origin: "local", selectedOrigin: "local",
                                                        requestRevision: 1, currentRevision: 1, into: &records))
        precondition(records["local"]?.version == version.version && records["local"]?.backendIP == nil)
        for invalid in ["", "0.0.0.0", "::", "studio.example", "192.0.2.1:8080", "fe80::a%en0", "[::1]"] {
            var bad = payload
            bad["backendIP"] = invalid
            precondition(WorkspaceVersionObservation.fromPayload(bad, now: now)?.backendIP == nil)
        }
        precondition(version.installedUpdatedAt == Date(timeIntervalSince1970: (received - 50_000) / 1000))
        precondition(version.daemonRegisteredAt != nil && version.daemonRunningVersion == "0.9.56")
        precondition(version.daemonRunningSince != nil && version.daemonStatusObservedAt != nil)
        precondition(version.daemonLoaded == false && version.daemonOwned == true && version.daemonObservationStale == nil)
        let invalidStarts: [Any] = [received + 1_000, NSNumber(value: true), Double.infinity]
        for invalid in invalidStarts {
            var bad = payload
            bad["webRunningSince"] = invalid
            precondition(WorkspaceVersionObservation.fromPayload(bad, now: now)?.uptimeAtObservation == nil)
        }
        var noProcessStart = payload
        noProcessStart.removeValue(forKey: "webRunningSince")
        precondition(WorkspaceVersionObservation.fromPayload(noProcessStart, now: now)?.uptimeAtObservation == nil)
        var noServerClock = payload
        noServerClock.removeValue(forKey: "backendObservedAt")
        precondition(WorkspaceVersionObservation.fromPayload(noServerClock, now: now)?.uptimeAtObservation == nil)
        let legacy = try JSONDecoder().decode(WorkspaceVersionObservation.self,
                                              from: Data(#"{"version":"0.9.55","observedAt":0}"#.utf8))
        precondition(legacy.version == "0.9.55" && legacy.uptimeAtObservation == nil && legacy.backendIP == nil)
        precondition(WorkspaceVersionObservation.fromPayload(["version": "0.9.55"], now: now)?.backendIP == nil)
    }

    private static func verifyFooterHandoff() {
        for (width, height, safeArea, left) in [(390, 844, 34, 0), (844, 390, 21, 44), (390, 844, 0, 0)] {
            let row = 48
            let fallbackHeight = height - safeArea - row
            let fallbackViewport = CGSize(width: width, height: fallbackHeight)
            let integratedViewport = CGSize(width: width, height: height)
            let fallback: [String: NSNumber] = ["x": .init(value: left), "y": .init(value: fallbackHeight - row),
                "width": 100, "height": 48, "viewportWidth": .init(value: width),
                "viewportHeight": .init(value: fallbackHeight)]
            let integrated: [String: NSNumber] = ["x": .init(value: left), "y": .init(value: fallbackHeight),
                "width": 100, "height": 48, "viewportWidth": .init(value: width), "viewportHeight": .init(value: height)]
            let nativeBottom = CGFloat(safeArea)
            let fallbackBottom = WorkspaceFooterGeometry.webBottomInset(safeArea: nativeBottom, hasSlot: false)
            let integratedBottom = WorkspaceFooterGeometry.webBottomInset(safeArea: nativeBottom, hasSlot: true)
            let fallbackSlot = WorkspaceFooterGeometry.slot(fallback, viewport: fallbackViewport, width: 100, bottom: fallbackBottom)
            let integratedSlot = WorkspaceFooterGeometry.slot(integrated, viewport: integratedViewport, width: 100, bottom: integratedBottom)
            precondition(fallbackSlot?.maxY == fallbackViewport.height)
            precondition(integratedSlot?.minY == fallbackViewport.height)
            precondition(integratedSlot?.maxY == integratedViewport.height - nativeBottom)
            precondition(WorkspaceFooterGeometry.slot(fallback, viewport: integratedViewport, width: 100, bottom: integratedBottom) == nil)
            precondition(WorkspaceFooterGeometry.slot(integrated, viewport: fallbackViewport, width: 100, bottom: fallbackBottom) == nil)
        }
    }

    private static func verifyFooterReceiver() {
        let viewport = CGSize(width: 390, height: 844)
        let valid: [String: Any] = ["revision": 9, "generation": 4, "x": 0, "y": 762,
            "width": 100, "height": 48, "viewportWidth": 390, "viewportHeight": 844]
        var slot: CGRect?
        var updates = 0
        func receive(_ values: [String: Any]) -> Bool {
            WorkspaceFooterGeometry.receive(values, viewport: viewport, width: 100, bottom: 34,
                revision: 9, generation: 4) {
                slot = $0
                updates += 1
            }
        }
        precondition(receive(valid))
        let accepted = CGRect(x: 0, y: 762, width: 100, height: 48)
        precondition(slot == accepted && updates == 1)
        for (key, value) in [("revision", 8), ("generation", 3)] {
            var stale = valid
            stale[key] = value
            stale["viewportHeight"] = 762
            precondition(!receive(stale) && slot == accepted && updates == 1)
        }
        var unversioned = valid
        unversioned.removeValue(forKey: "generation")
        precondition(!receive(unversioned) && slot == accepted && updates == 1)
        var invalid = valid
        invalid["y"] = "invalid"
        precondition(!receive(invalid) && slot == nil && updates == 2)
    }

    private static func verifyCloudSyncPreference() {
        let suite = "team.heinzel.tests.cloud-sync-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        precondition(CloudSyncPreference.isEnabled(defaults), "missing value migrates to on")
        CloudSyncPreference.set(false, in: defaults)
        precondition(!CloudSyncPreference.isEnabled(defaults), "an explicit off stays off")
        CloudSyncPreference.set(true, in: defaults)
        precondition(CloudSyncPreference.isEnabled(defaults))
        let now = Date(timeIntervalSince1970: 3_000_000)
        let fresh = now.addingTimeInterval(-3600)
        let stale = now.addingTimeInterval(-15 * 24 * 3600)
        precondition(CloudSignInPlan.decide(existingToken: nil, publishedAt: nil, hostRefusedIt: false, now: now) == .mint)
        precondition(CloudSignInPlan.decide(existingToken: "session-token", publishedAt: fresh, hostRefusedIt: false, now: now) == .mint)
        precondition(CloudSignInPlan.decide(existingToken: "hzd_abc", publishedAt: fresh, hostRefusedIt: false, now: now) == .keep)
        precondition(CloudSignInPlan.decide(existingToken: "hzd_abc", publishedAt: fresh, hostRefusedIt: true, now: now) == .mint)
        precondition(CloudSignInPlan.decide(existingToken: "hzd_abc", publishedAt: stale, hostRefusedIt: false, now: now) == .mint)
        print("PASS: iCloud sign-in uses a renewable device token")
        print("PASS: iCloud sign-in sync defaults on and keeps an explicit off")
    }

    @MainActor private static func verifyVersionRefresh(now: Date) async {
        let refresh = WorkspaceVersionRefresh()
        let origin = "https://workspace.example"
        var selected = origin
        var document = 1
        let cached = WorkspaceVersionObservation(version: "0.9.49", observedAt: now)
        var latest = WorkspaceVersionObservation(version: "0.9.50", observedAt: now.addingTimeInterval(60))
        var versions = [origin: cached]
        var reads = 0
        let report: @MainActor (WorkspaceVersionObservation) -> Void = { versions[origin] = $0 }
        let current: @MainActor () -> Bool = { selected == origin && document == 1 }

        // Opening a healthy pane requests fresh data; unrelated SwiftUI updates with the same revision do not.
        let opened = refresh.request(revision: 1, read: { reads += 1; return latest }, isCurrent: current, report: report)!
        await opened.value
        precondition(reads == 1 && versions[origin] == latest)
        precondition(refresh.request(revision: 1, read: { reads += 1; return nil }, isCurrent: current, report: report) == nil)
        precondition(reads == 1)

        // Explicit status refresh reads again, while offline failure preserves the prior observation timestamp.
        latest = WorkspaceVersionObservation(version: "0.9.51", observedAt: now.addingTimeInterval(120))
        let explicit = refresh.request(revision: 2, read: { reads += 1; return latest }, isCurrent: current, report: report)!
        await explicit.value
        let offline = refresh.request(revision: 3, read: { reads += 1; return nil }, isCurrent: current, report: report)!
        await offline.value
        precondition(reads == 3 && versions[origin] == latest)

        var pending: CheckedContinuation<WorkspaceVersionObservation?, Never>?
        let delayed: @MainActor () async -> WorkspaceVersionObservation? = {
            await withCheckedContinuation { pending = $0 }
        }
        let switched = refresh.request(revision: 4, read: delayed, isCurrent: current, report: report)!
        for _ in 0..<1000 { if pending != nil { break }; await Task.yield() }
        precondition(pending != nil)
        selected = "https://other.example"
        pending!.resume(returning: cached); pending = nil
        await switched.value
        precondition(versions[origin] == latest && versions[selected] == nil)

        selected = origin
        let navigated = refresh.request(revision: 5, read: delayed, isCurrent: current, report: report)!
        for _ in 0..<1000 { if pending != nil { break }; await Task.yield() }
        precondition(pending != nil)
        document = 2
        pending!.resume(returning: cached); pending = nil
        await navigated.value
        precondition(versions[origin] == latest)

        document = 1
        let superseded = refresh.request(revision: 6, read: delayed, isCurrent: current, report: report)!
        for _ in 0..<1000 { if pending != nil { break }; await Task.yield() }
        precondition(pending != nil)
        let newest = refresh.request(revision: 7, read: { latest }, isCurrent: current, report: report)!
        await newest.value
        pending!.resume(returning: cached); pending = nil
        await superseded.value
        precondition(superseded.isCancelled && versions[origin] == latest)

        let closed = refresh.request(revision: 8, read: delayed, isCurrent: current, report: report)!
        for _ in 0..<1000 { if pending != nil { break }; await Task.yield() }
        precondition(pending != nil)
        refresh.cancel()
        pending!.resume(returning: cached)
        await closed.value
        precondition(closed.isCancelled && versions[origin] == latest)
        print("PASS: version refresh preserves offline cache and rejects superseded workspaces")
    }

    private static func verifyAppBuildObservation(now: Date) throws {
        let original = AppBuildObservation.Build(version: "0.9.50", number: "28", revision: "abcdef012345")!
        let next = AppBuildObservation.Build(version: "0.9.50", number: "29", revision: "abcdef012345")!
        let rebuilt = AppBuildObservation.Build(version: "0.9.50", number: "28", revision: "fedcba987654")!
        let first = AppBuildObservation.recording(original, previous: nil, now: now)
        let later = now.addingTimeInterval(60)
        let reopened = AppBuildObservation.recording(original, previous: first, now: later)
        precondition(first.observedSince == now && reopened == first)
        precondition(AppBuildObservation.recording(next, previous: first, now: later).observedSince == later)
        precondition(AppBuildObservation.recording(rebuilt, previous: first, now: later).observedSince == later)
        precondition(AppBuildObservation.recording(original, previous: first, now: now.addingTimeInterval(-1)).observedSince < now)
        precondition(AppBuildObservation.Build(version: nil, number: "28", revision: nil) == nil)
        precondition(AppBuildObservation.Build(version: "0.9.50", number: "28", revision: "$(HEINZEL_SOURCE_REVISION)")?.revision == nil)
        let saved = try JSONEncoder().encode(first)
        let restored = try JSONDecoder().decode(AppBuildObservation.self, from: saved)
        precondition(restored == first)
    }
}
