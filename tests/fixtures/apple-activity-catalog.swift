import Foundation
import SQLite3

@main struct CatalogTest {
    static func flag(_ url: URL, _ name: String) -> URL { url.appendingPathExtension(name) }

    static func waitFor(_ url: URL) {
        for _ in 0..<500 {
            if FileManager.default.fileExists(atPath: url.path) { return }
            Thread.sleep(forTimeInterval: 0.01)
        }
        fatalError("timed out waiting for \(url.lastPathComponent)")
    }

    static func main() throws {
        let url = URL(fileURLWithPath: CommandLine.arguments[1])
        if CommandLine.arguments.count > 2 {
            let mode = CommandLine.arguments[2]
            if mode == "writer" {
                guard let store = ActivityCatalog(url: url), let token = ActivityCatalog.refreshToken(url: url) else { fatalError("writer") }
                let ok = store.publish(scope: "one", generation: "racing", snapshot: Data("racing".utf8), entries: [], expectedToken: token) { data, marker in
                    try! Data().write(to: flag(url, "ready"))
                    waitFor(flag(url, "release"))
                    do { try data.write(to: marker, options: .atomic); return true } catch { return false }
                }
                precondition(ok)
            } else if mode == "crash" {
                guard let store = ActivityCatalog(url: url), let token = ActivityCatalog.refreshToken(url: url) else { fatalError("crash") }
                _ = store.publish(scope: "one", generation: "interrupted", snapshot: Data("interrupted".utf8), entries: [], expectedToken: token) { _, _ in
                    try! Data().write(to: flag(url, "crash-ready"))
                    waitFor(flag(url, "never"))
                    return false
                }
                fatalError("interrupted publisher unexpectedly returned")
            } else if mode == "stale" || mode == "stale-authority" {
                guard let store = ActivityCatalog(url: url), let token = ActivityCatalog.refreshToken(url: url) else { fatalError("stale") }
                try Data().write(to: flag(url, mode + "-ready"))
                waitFor(flag(url, mode + "-release"))
                precondition(!store.publish(scope: "one", generation: "stale-child", snapshot: Data("stale".utf8), entries: [], expectedToken: token))
            } else if mode == "clear" {
                try Data().write(to: flag(url, "started"))
                precondition(ActivityCatalog.clear(url: url).complete)
                try Data().write(to: flag(url, "done"))
            } else if mode == "secure-install" {
                let expected = try JSONDecoder().decode(ActivityCatalog.Activation.self, from: Data(contentsOf: flag(url, "activation")))
                let installed = ActivityCatalog.installCredential(url: url, expected: expected,
                    workspaceID: String(repeating: "a", count: 64), generation: UUID().uuidString) {
                    try! Data().write(to: flag(url, "install-ready"))
                    waitFor(flag(url, "install-release"))
                    return true
                }
                precondition(installed != nil)
            } else if mode == "secure-invalidate" {
                let expected = try JSONDecoder().decode(ActivityCatalog.Activation.self, from: Data(contentsOf: flag(url, "activation")))
                try Data().write(to: flag(url, "invalidate-started"))
                let result = ActivityCatalog.invalidate(url: url, expected: expected, deleteCredential: {
                    try! Data().write(to: flag(url, "unexpected-delete")); return true
                })
                precondition(!result.revoked)
                try Data().write(to: flag(url, "invalidate-done"))
            } else if mode == "secure-crash-invalidate" {
                let expected = try JSONDecoder().decode(ActivityCatalog.Activation.self, from: Data(contentsOf: flag(url, "activation")))
                _ = ActivityCatalog.invalidate(url: url, expected: expected, deleteCredential: {
                    try! Data().write(to: flag(url, "tombstone-ready"))
                    waitFor(flag(url, "never"))
                    return false
                })
                fatalError("interrupted invalidation unexpectedly returned")
            } else if mode == "reserve" {
                let name = CommandLine.arguments[3]
                let expected = try JSONDecoder().decode(ActivityCatalog.Activation.self, from: Data(contentsOf: flag(url, "activation")))
                try Data().write(to: flag(url, "reserve-ready-" + name))
                waitFor(flag(url, "reserve-release"))
                let result = ActivityCatalog.reserveEnrollment(url: url, expected: expected)
                try Data((result == nil ? "lost" : "won").utf8).write(to: flag(url, "reserve-result-" + name))
            } else if mode == "pending-check" {
                let expected = try JSONDecoder().decode(ActivityCatalog.Enrollment.self, from: Data(contentsOf: flag(url, "expected-attempt")))
                precondition(ActivityCatalog.pendingEnrollment(url: url) == expected)
            }
            return
        }
        try? FileManager.default.removeItem(at: url)
        try? FileManager.default.removeItem(at: url.appendingPathExtension("authority"))
        guard let store = ActivityCatalog(url: url) else { fatalError("open") }
        let rows = (0..<125).map { CatalogHeinzel(id: "id-\($0)", name: $0 == 124 || $0 == 2 ? "Duplicate" : "Heinzel \($0)", state: "awake", activity: $0 == 124 ? "stuck" : nil, asks: $0 == 124 ? 3 : nil, lastApproved: $0 == 124 ? 1234 : nil) }
        precondition(store.publish(scope: "one", generation: "g1", snapshot: Data("one".utf8), entries: rows, expectedToken: ActivityCatalog.refreshToken(url: url)!))
        let oldToken = ActivityCatalog.refreshToken(url: url)!
        precondition(store.search(scope: "one", generation: "g1").count == 50)
        precondition(store.search(scope: "one", generation: "g1", offset: 100).count == 25)
        precondition(store.search(scope: "one", generation: "g1", query: "Duplicate").map(\.id) == ["id-124", "id-2"])
        precondition(store.search(scope: "one", generation: "g1", query: "id-124").map(\.id) == ["id-124"])
        precondition(store.find(scope: "one", generation: "g1", id: "id-124") == rows[124])
        let fallback = store.find(scope: "one", generation: "g1", id: "id-124")!.widgetHeinzel
        precondition(fallback.status == "Stuck" && fallback.asks == 3 && fallback.lastApproved == 1234)
        precondition(fallback.portrait == nil && fallback.nextWake == nil)
        precondition(store.find(scope: "two", generation: "g1", id: "id-124") == nil)
        precondition(!store.publish(scope: "two", generation: "g2", snapshot: Data("two".utf8), entries: [rows[0], rows[0]], expectedToken: oldToken))
        precondition(store.snapshot() == Data("one".utf8))
        precondition(store.find(scope: "one", generation: "g1", id: "id-124") == rows[124])
        precondition(store.publish(scope: "one", generation: "g2", snapshot: Data("two".utf8), entries: [rows[124]], expectedToken: oldToken))
        precondition(store.find(scope: "one", generation: "g1", id: "id-124") == nil)
        precondition(store.find(scope: "one", generation: "g2", id: "id-124") == rows[124])
        let currentToken = ActivityCatalog.refreshToken(url: url)!
        guard let reader = ActivityCatalog(url: url) else { fatalError("reader") }
        var lock: OpaquePointer?
        precondition(sqlite3_open(url.path, &lock) == SQLITE_OK)
        precondition(sqlite3_exec(lock, "BEGIN EXCLUSIVE", nil, nil, nil) == SQLITE_OK)
        let busyClear = ActivityCatalog.clear(url: url)
        precondition(busyClear.revoked && !busyClear.purged)
        precondition(sqlite3_exec(lock, "ROLLBACK", nil, nil, nil) == SQLITE_OK)
        sqlite3_close(lock)
        precondition(reader.snapshot() == nil && reader.find(scope: "one", generation: "g2", id: "id-124") == nil)
        guard let reopened = ActivityCatalog(url: url) else { fatalError("reopen") }
        precondition(reopened.snapshot() == nil && reopened.search(scope: "one", generation: "g2").isEmpty)
        precondition(!store.publish(scope: "one", generation: "stale", snapshot: Data("stale".utf8), entries: rows, expectedToken: currentToken))
        let token = ActivityCatalog.refreshToken(url: url)!
        precondition(store.publish(scope: "one", generation: "g3", snapshot: Data("fresh".utf8), entries: rows, expectedToken: token))
        // Simulate authority replacement and removal both failing; purge still
        // independently makes the old authority resolve to no rows.
        let staleAuthority = Process()
        staleAuthority.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        staleAuthority.arguments = [url.path, "stale-authority"]
        try staleAuthority.run()
        waitFor(flag(url, "stale-authority-ready"))
        let failedAuthority = ActivityCatalog.clear(url: url, write: { _, _ in false }, remove: { _ in false })
        precondition(!failedAuthority.revoked && failedAuthority.purged)
        try Data().write(to: flag(url, "stale-authority-release"))
        staleAuthority.waitUntilExit()
        precondition(staleAuthority.terminationStatus == 0)
        precondition(store.snapshot() == nil && store.find(scope: "one", generation: "g2", id: "id-124") == nil)
        precondition(store.publish(scope: "one", generation: "g4", snapshot: Data("four".utf8), entries: rows,
                                   expectedToken: ActivityCatalog.refreshToken(url: url)!))
        let token4 = ActivityCatalog.refreshToken(url: url)!
        precondition(!store.publish(scope: "one", generation: "failed", snapshot: Data("bad".utf8), entries: [],
                                    expectedToken: token4, write: { _, _ in false }))
        precondition(store.snapshot() == Data("four".utf8))
        guard let afterFailure = ActivityCatalog(url: url) else { fatalError("reopen") }
        precondition(afterFailure.snapshot() == Data("four".utf8))
        precondition(afterFailure.find(scope: "one", generation: "g4", id: "id-124") == rows[124])
        var secondLock: OpaquePointer?
        precondition(sqlite3_open(url.path, &secondLock) == SQLITE_OK)
        precondition(sqlite3_exec(secondLock, "BEGIN EXCLUSIVE", nil, nil, nil) == SQLITE_OK)
        let failedBoth = ActivityCatalog.clear(url: url, write: { _, _ in false }, remove: { _ in false })
        precondition(!failedBoth.revoked && !failedBoth.purged)
        precondition(sqlite3_exec(secondLock, "ROLLBACK", nil, nil, nil) == SQLITE_OK)
        sqlite3_close(secondLock)
        precondition(afterFailure.snapshot() == Data("four".utf8))
        let interrupted = Process()
        interrupted.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        interrupted.arguments = [url.path, "crash"]
        try interrupted.run()
        waitFor(flag(url, "crash-ready"))
        interrupted.terminate()
        interrupted.waitUntilExit()
        precondition(afterFailure.snapshot() == Data("four".utf8))
        precondition(afterFailure.find(scope: "one", generation: "g4", id: "id-124") == rows[124])
        let writer = Process()
        writer.executableURL = URL(fileURLWithPath: CommandLine.arguments[0])
        writer.arguments = [url.path, "writer"]
        try writer.run()
        waitFor(flag(url, "ready"))
        let clearer = Process()
        clearer.executableURL = writer.executableURL
        clearer.arguments = [url.path, "clear"]
        try clearer.run()
        waitFor(flag(url, "started"))
        Thread.sleep(forTimeInterval: 0.2)
        precondition(!FileManager.default.fileExists(atPath: flag(url, "done").path))
        try Data().write(to: flag(url, "release"))
        writer.waitUntilExit(); clearer.waitUntilExit()
        precondition(writer.terminationStatus == 0 && clearer.terminationStatus == 0)
        precondition(afterFailure.snapshot() == nil)
        let stale = Process()
        stale.executableURL = writer.executableURL
        stale.arguments = [url.path, "stale"]
        try stale.run()
        waitFor(flag(url, "stale-ready"))
        precondition(ActivityCatalog.clear(url: url).complete)
        try Data().write(to: flag(url, "stale-release"))
        stale.waitUntilExit()
        precondition(stale.terminationStatus == 0 && afterFailure.snapshot() == nil)
        let pending = ActivityCatalog.activate(url: url, endpoint: "https://example.test")!
        try JSONEncoder().encode(pending).write(to: flag(url, "activation"))
        let pendingToken = ActivityCatalog.refreshToken(url: url)!
        let installer = Process()
        installer.executableURL = writer.executableURL
        installer.arguments = [url.path, "secure-install"]
        try installer.run()
        waitFor(flag(url, "install-ready"))
        let invalidator = Process()
        invalidator.executableURL = writer.executableURL
        invalidator.arguments = [url.path, "secure-invalidate"]
        try invalidator.run()
        waitFor(flag(url, "invalidate-started"))
        Thread.sleep(forTimeInterval: 0.2)
        precondition(!FileManager.default.fileExists(atPath: flag(url, "invalidate-done").path))
        try Data().write(to: flag(url, "install-release"))
        installer.waitUntilExit(); invalidator.waitUntilExit()
        precondition(installer.terminationStatus == 0 && invalidator.terminationStatus == 0)
        precondition(!FileManager.default.fileExists(atPath: flag(url, "unexpected-delete").path))
        let active = ActivityCatalog.activation(url: url)!
        precondition(active.credentialGeneration != nil)
        precondition(!store.publish(scope: active.scope!, generation: "old", snapshot: Data("old".utf8), entries: [], expectedToken: pendingToken))
        try JSONEncoder().encode(active).write(to: flag(url, "activation"))
        let interruptedInvalidation = Process()
        interruptedInvalidation.executableURL = writer.executableURL
        interruptedInvalidation.arguments = [url.path, "secure-crash-invalidate"]
        try interruptedInvalidation.run()
        waitFor(flag(url, "tombstone-ready"))
        interruptedInvalidation.terminate()
        interruptedInvalidation.waitUntilExit()
        precondition(ActivityCatalog.activation(url: url) == nil)
        let afterCrash = ActivityCatalog.refreshToken(url: url)!
        precondition(!store.publish(scope: active.scope!, generation: "after-crash", snapshot: Data("old".utf8), entries: [], expectedToken: afterCrash))
        precondition(ActivityCatalog.invalidate(url: url, expected: active, deleteCredential: { true }).complete)
        let toReserve = ActivityCatalog.activate(url: url, endpoint: "https://example.test")!
        try JSONEncoder().encode(toReserve).write(to: flag(url, "activation"))
        let reservations = ["one", "two"].map { name -> Process in
            let process = Process()
            process.executableURL = writer.executableURL
            process.arguments = [url.path, "reserve", name]
            return process
        }
        for process in reservations { try process.run() }
        waitFor(flag(url, "reserve-ready-one")); waitFor(flag(url, "reserve-ready-two"))
        try Data().write(to: flag(url, "reserve-release"))
        for process in reservations { process.waitUntilExit(); precondition(process.terminationStatus == 0) }
        let outcomes = try ["one", "two"].map { try String(contentsOf: flag(url, "reserve-result-" + $0), encoding: .utf8) }
        precondition(outcomes.filter { $0 == "won" }.count == 1)
        let unresolved = ActivityCatalog.pendingEnrollment(url: url)!
        precondition(unresolved.previous == toReserve)
        precondition(ActivityCatalog.clear(url: url).complete)
        let reset = ActivityCatalog.activate(url: url, endpoint: "https://example.test")!
        precondition(ActivityCatalog.reserveEnrollment(url: url, expected: reset) == nil)
        try JSONEncoder().encode(unresolved).write(to: flag(url, "expected-attempt"))
        let restarted = Process()
        restarted.executableURL = writer.executableURL
        restarted.arguments = [url.path, "pending-check"]
        try restarted.run()
        restarted.waitUntilExit()
        precondition(restarted.terminationStatus == 0)
        print("catalog checks passed")
    }
}
