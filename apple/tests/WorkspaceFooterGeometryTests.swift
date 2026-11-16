import Foundation

@main struct WorkspaceFooterGeometryTests {
    static func main() {
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
        precondition(WorkspaceVersionObservation.record(observed, origin: origin, selectedOrigin: origin,
                                                        requestRevision: 1, currentRevision: 1, into: &versions))
        versions.removeValue(forKey: origin)
        precondition(!WorkspaceVersionObservation.record(observed, origin: origin, selectedOrigin: "",
                                                         requestRevision: 1, currentRevision: 1, into: &versions))
        precondition(versions.isEmpty)
        precondition(!WorkspaceVersionObservation.record(observed, origin: origin, selectedOrigin: origin,
                                                         requestRevision: 1, currentRevision: 2, into: &versions))
        print("PASS: native footer slot is bounded to the current viewport")
    }
}
