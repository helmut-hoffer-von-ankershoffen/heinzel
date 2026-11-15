import Foundation
import SQLite3
import Darwin

struct CatalogHeinzel: Codable, Equatable, Identifiable {
    let id: String
    let name: String
    let state: String
    let activity: String?
    let asks: Int?
    let lastApproved: Double?

    init(id: String, name: String, state: String, activity: String? = nil, asks: Int? = nil, lastApproved: Double? = nil) {
        self.id = id; self.name = name; self.state = state
        self.activity = activity; self.asks = asks; self.lastApproved = lastApproved
    }
}

// The snapshot and its compact catalog are committed together. Readers see either
// the old generation or the new one, even if publication is interrupted.
final class ActivityCatalog {
    private var db: OpaquePointer?
    private let databaseURL: URL
    private let authorityURL: URL
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    private struct Authority: Codable, Equatable {
        let scope: String
        let generation: String
    }
    private struct WriterToken: Codable, Equatable {
        let authority: Data
        let epoch: String
    }

    private static func authorityURL(for url: URL) -> URL { url.appendingPathExtension("authority") }

    struct ClearResult {
        let revoked: Bool
        let purged: Bool
        var complete: Bool { revoked && purged }
    }

    private static func writeAuthority(_ data: Data, _ url: URL) -> Bool {
        do { try data.write(to: url, options: .atomic); return true } catch { return false }
    }

    private static func removeAuthority(_ url: URL) -> Bool {
        do {
            if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
            return true
        } catch { return false }
    }

    private static func withWriterLock<T>(url: URL, failure: T, _ body: () -> T) -> T {
        let fd = open(url.appendingPathExtension("lock").path,
                      O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { return failure }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_uid == getuid(), info.st_nlink == 1, info.st_mode & 0o077 == 0,
              flock(fd, LOCK_EX) == 0 else { return failure }
        defer { flock(fd, LOCK_UN) }
        return body()
    }

    static func refreshToken(url: URL) -> Data? {
        ActivityCatalog(url: url)?.refreshToken()
    }

    private func refreshToken() -> Data? {
        let authority: Data
        if FileManager.default.fileExists(atPath: authorityURL.path) {
            guard let data = try? Data(contentsOf: authorityURL) else { return nil }
            authority = data
        } else { authority = Data() }
        guard let statement = prepare("SELECT value FROM writer_epoch WHERE id=1") else { return nil }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW, let value = sqlite3_column_text(statement, 0) else { return nil }
        return try? JSONEncoder().encode(WriterToken(authority: authority, epoch: String(cString: value)))
    }

    private func matches(_ expected: Data) -> Bool {
        guard let current = refreshToken(), let first = try? JSONDecoder().decode(WriterToken.self, from: current),
              let second = try? JSONDecoder().decode(WriterToken.self, from: expected) else { return false }
        return first == second
    }

    private func authorized(_ authority: Authority) -> Bool {
        guard let data = try? Data(contentsOf: authorityURL),
              let current = try? JSONDecoder().decode(Authority.self, from: data) else { return false }
        return current == authority
    }

    init?(url: URL) {
        databaseURL = url
        authorityURL = Self.authorityURL(for: url)
        guard sqlite3_open_v2(url.path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else { return nil }
        sqlite3_busy_timeout(db, 2000)
        guard run("PRAGMA secure_delete=ON"),
              run("CREATE TABLE IF NOT EXISTS snapshot (scope TEXT PRIMARY KEY, generation TEXT NOT NULL, data BLOB NOT NULL)"),
              run("CREATE TABLE IF NOT EXISTS catalog_v2 (id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL, activity TEXT, asks INTEGER, approved REAL, ordinal INTEGER NOT NULL)"),
              run("CREATE TABLE IF NOT EXISTS snapshots_v2 (scope TEXT NOT NULL, generation TEXT PRIMARY KEY, data BLOB NOT NULL)"),
              run("CREATE TABLE IF NOT EXISTS writer_epoch (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"),
              run("INSERT OR IGNORE INTO writer_epoch VALUES (1, 'initial')"),
              run("CREATE TABLE IF NOT EXISTS catalog_v3 (generation TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, state TEXT NOT NULL, activity TEXT, asks INTEGER, approved REAL, ordinal INTEGER NOT NULL, PRIMARY KEY (generation, id))"),
              run("CREATE INDEX IF NOT EXISTS catalog_v3_name ON catalog_v3(generation, name COLLATE NOCASE, id)"),
              run("CREATE INDEX IF NOT EXISTS catalog_v3_id ON catalog_v3(generation, id COLLATE NOCASE)"),
              run("INSERT OR IGNORE INTO snapshots_v2 SELECT scope, generation, data FROM snapshot"),
              run("INSERT OR IGNORE INTO catalog_v3 SELECT snapshot.generation, catalog_v2.id, catalog_v2.name, catalog_v2.state, catalog_v2.activity, catalog_v2.asks, catalog_v2.approved, catalog_v2.ordinal FROM catalog_v2 CROSS JOIN snapshot") else { return nil }
    }

    deinit { sqlite3_close(db) }

    private func run(_ sql: String) -> Bool { sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK }

    private func prepare(_ sql: String) -> OpaquePointer? {
        var statement: OpaquePointer?
        return sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK ? statement : nil
    }

    private func bind(_ text: String, to statement: OpaquePointer?, at index: Int32) {
        sqlite3_bind_text(statement, index, text, -1, Self.transient)
    }

    func publish(scope: String, generation: String, snapshot: Data, entries: [CatalogHeinzel], expectedToken: Data,
                 write: (Data, URL) -> Bool = ActivityCatalog.writeAuthority) -> Bool {
        Self.withWriterLock(url: databaseURL, failure: false) {
            publishLocked(scope: scope, generation: generation, snapshot: snapshot, entries: entries,
                          expectedToken: expectedToken, write: write)
        }
    }

    private func publishLocked(scope: String, generation: String, snapshot: Data, entries: [CatalogHeinzel],
                               expectedToken: Data, write: (Data, URL) -> Bool) -> Bool {
        guard matches(expectedToken) else { return false }
        guard run("BEGIN IMMEDIATE") else { return false }
        var committed = false
        defer { if !committed { _ = run("ROLLBACK") } }
        let token = try? JSONDecoder().decode(WriterToken.self, from: expectedToken)
        let current = token.flatMap { try? JSONDecoder().decode(Authority.self, from: $0.authority) }?.generation ?? ""
        guard run("INSERT OR IGNORE INTO snapshots_v2 SELECT scope, generation, data FROM snapshot"),
              run("INSERT OR IGNORE INTO catalog_v3 SELECT snapshot.generation, catalog_v2.id, catalog_v2.name, catalog_v2.state, catalog_v2.activity, catalog_v2.asks, catalog_v2.approved, catalog_v2.ordinal FROM catalog_v2 CROSS JOIN snapshot"),
              let cleanItems = prepare("DELETE FROM catalog_v3 WHERE generation != ?"),
              let cleanSnapshots = prepare("DELETE FROM snapshots_v2 WHERE generation != ?") else { return false }
        defer { sqlite3_finalize(cleanItems); sqlite3_finalize(cleanSnapshots) }
        bind(current, to: cleanItems, at: 1); bind(current, to: cleanSnapshots, at: 1)
        guard sqlite3_step(cleanItems) == SQLITE_DONE, sqlite3_step(cleanSnapshots) == SQLITE_DONE,
              let insert = prepare("INSERT INTO catalog_v3 VALUES (?, ?, ?, ?, ?, ?, ?, ?)") else { return false }
        defer { sqlite3_finalize(insert) }
        for (ordinal, item) in entries.enumerated() {
            bind(generation, to: insert, at: 1); bind(item.id, to: insert, at: 2)
            bind(item.name, to: insert, at: 3); bind(item.state, to: insert, at: 4)
            if let activity = item.activity { bind(activity, to: insert, at: 5) } else { sqlite3_bind_null(insert, 5) }
            if let asks = item.asks { sqlite3_bind_int64(insert, 6, Int64(asks)) } else { sqlite3_bind_null(insert, 6) }
            if let approved = item.lastApproved { sqlite3_bind_double(insert, 7, approved) } else { sqlite3_bind_null(insert, 7) }
            sqlite3_bind_int64(insert, 8, Int64(ordinal))
            guard sqlite3_step(insert) == SQLITE_DONE else { return false }
            sqlite3_reset(insert); sqlite3_clear_bindings(insert)
        }
        guard let statement = prepare("INSERT INTO snapshots_v2 VALUES (?, ?, ?)") else { return false }
        defer { sqlite3_finalize(statement) }
        bind(scope, to: statement, at: 1)
        bind(generation, to: statement, at: 2)
        snapshot.withUnsafeBytes { bytes in
            _ = sqlite3_bind_blob(statement, 3, bytes.baseAddress, Int32(bytes.count), Self.transient)
        }
        guard sqlite3_step(statement) == SQLITE_DONE, run("COMMIT") else { return false }
        committed = true
        guard matches(expectedToken) else { return false }
        guard let authority = try? JSONEncoder().encode(Authority(scope: scope, generation: generation)) else { return false }
        guard write(authority, authorityURL) else { return false }
        _ = run("DELETE FROM catalog_v2")
        _ = run("DELETE FROM snapshot")
        _ = run("DROP TABLE IF EXISTS catalog")
        return true
    }

    // Revoke the cross-process authority before opening or locking SQLite. A failed
    // purge can leave bytes on disk, but no reader may use that generation.
    static func clear(url: URL, write: (Data, URL) -> Bool = ActivityCatalog.writeAuthority,
                      remove: (URL) -> Bool = ActivityCatalog.removeAuthority) -> ClearResult {
        withWriterLock(url: url, failure: ClearResult(revoked: false, purged: false)) {
            let authority = authorityURL(for: url)
            let tombstone = try? JSONEncoder().encode(Authority(scope: "", generation: UUID().uuidString))
            let revoked = tombstone.map { write($0, authority) } == true || remove(authority)
            // Cleanup is independent: even if authority revocation failed, removing
            // every generation makes the previous authority resolve to no data.
            let purged = ActivityCatalog(url: url)?.purge() ?? false
            return ClearResult(revoked: revoked, purged: purged)
        }
    }

    private func purge() -> Bool {
        guard run("BEGIN IMMEDIATE") else { return false }
        guard let epoch = prepare("UPDATE writer_epoch SET value=? WHERE id=1") else { _ = run("ROLLBACK"); return false }
        defer { sqlite3_finalize(epoch) }
        bind(UUID().uuidString, to: epoch, at: 1)
        guard sqlite3_step(epoch) == SQLITE_DONE else { _ = run("ROLLBACK"); return false }
        guard run("DROP TABLE IF EXISTS catalog"), run("DELETE FROM catalog_v2"), run("DELETE FROM snapshot"),
              run("DELETE FROM catalog_v3"), run("DELETE FROM snapshots_v2"), run("COMMIT") else { _ = run("ROLLBACK"); return false }
        return true
    }

    func snapshot() -> Data? {
        guard let token = try? Data(contentsOf: authorityURL),
              let authority = try? JSONDecoder().decode(Authority.self, from: token),
              let statement = prepare("SELECT data FROM snapshots_v2 WHERE scope=? AND generation=?") else { return nil }
        defer { sqlite3_finalize(statement) }
        bind(authority.scope, to: statement, at: 1); bind(authority.generation, to: statement, at: 2)
        guard sqlite3_step(statement) == SQLITE_ROW, let bytes = sqlite3_column_blob(statement, 0) else { return nil }
        guard authorized(authority) else { return nil }
        return Data(bytes: bytes, count: Int(sqlite3_column_bytes(statement, 0)))
    }

    func search(scope: String, generation: String, query: String = "", offset: Int = 0, limit: Int = 50) -> [CatalogHeinzel] {
        let authority = Authority(scope: scope, generation: generation)
        guard authorized(authority) else { return [] }
        guard let statement = prepare("SELECT id, name, state, activity, asks, approved FROM catalog_v3 WHERE EXISTS (SELECT 1 FROM snapshots_v2 WHERE scope=? AND generation=?) AND generation=? AND (name LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\') ORDER BY name COLLATE NOCASE, id LIMIT ? OFFSET ?") else { return [] }
        defer { sqlite3_finalize(statement) }
        bind(scope, to: statement, at: 1)
        bind(generation, to: statement, at: 2)
        let escaped = query.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "%", with: "\\%").replacingOccurrences(of: "_", with: "\\_")
        bind(generation, to: statement, at: 3)
        bind(escaped + "%", to: statement, at: 4)
        bind(escaped + "%", to: statement, at: 5)
        sqlite3_bind_int(statement, 6, Int32(max(0, min(limit, 50))))
        sqlite3_bind_int64(statement, 7, Int64(max(0, offset)))
        let result = rows(statement)
        return authorized(authority) ? result : []
    }

    func find(scope: String, generation: String, id: String) -> CatalogHeinzel? {
        let authority = Authority(scope: scope, generation: generation)
        guard authorized(authority) else { return nil }
        guard let statement = prepare("SELECT id, name, state, activity, asks, approved FROM catalog_v3 WHERE id=? AND EXISTS (SELECT 1 FROM snapshots_v2 WHERE scope=? AND generation=?) AND generation=?") else { return nil }
        defer { sqlite3_finalize(statement) }
        bind(id, to: statement, at: 1); bind(scope, to: statement, at: 2)
        bind(generation, to: statement, at: 3)
        bind(generation, to: statement, at: 4)
        let result = rows(statement).first
        return authorized(authority) ? result : nil
    }

    private func rows(_ statement: OpaquePointer) -> [CatalogHeinzel] {
        var result: [CatalogHeinzel] = []
        while sqlite3_step(statement) == SQLITE_ROW {
            guard let id = sqlite3_column_text(statement, 0), let name = sqlite3_column_text(statement, 1),
                  let state = sqlite3_column_text(statement, 2) else { continue }
            let activity = sqlite3_column_text(statement, 3).map { String(cString: $0) }
            let asks = sqlite3_column_type(statement, 4) == SQLITE_NULL ? nil : Int(sqlite3_column_int64(statement, 4))
            let approved = sqlite3_column_type(statement, 5) == SQLITE_NULL ? nil : sqlite3_column_double(statement, 5)
            result.append(CatalogHeinzel(id: String(cString: id), name: String(cString: name), state: String(cString: state),
                                         activity: activity, asks: asks, lastApproved: approved))
        }
        return result
    }
}
