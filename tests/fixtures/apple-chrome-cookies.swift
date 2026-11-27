import CommonCrypto
import CryptoKit
import Foundation
import SQLite3

// A fixture Chrome profile, encrypted with a made-up password: no Keychain, no real Chrome profile.
// The cookie path of the fixture shop, under the site root.
let cartPath = ["", "cart"].joined(separator: "/")
let password = Data("fixture-safe-storage-password".utf8)
let now = Date(timeIntervalSince1970: 1_795_000_000)

func check(_ condition: Bool, _ message: String) {
    if !condition { FileHandle.standardError.write(Data("FAIL: \(message)\n".utf8)); exit(1) }
}

func encrypt(_ value: String, host: String, version: Int) -> Data {
    let key = ChromeCookies.deriveKey(password)
    var plain = Data(value.utf8)
    if version >= 24 { plain = Data(SHA256.hash(data: Data(host.utf8))) + plain }
    return Data("v10".utf8) + (ChromeCookies.chromeAES(kCCEncrypt, [UInt8](plain), key: key) ?? [])
}

func chromeTime(_ date: Date) -> Int64 { Int64((date.timeIntervalSince1970 + 11_644_473_600) * 1_000_000) }

@main
struct ChromeCookieProbe {
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "hz-chrome-fixture-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root.appending(path: "Default/Network"), withIntermediateDirectories: true)
        try Data(#"{"profile":{"info_cache":{"Default":{"name":"Ada"},"Profile 7":{"name":"Gone"},"../x":{"name":"x"}}}}"#.utf8)
            .write(to: root.appending(path: "Local State"))

        var db: OpaquePointer?
        check(sqlite3_open(root.appending(path: "Default/Network/Cookies").path, &db) == SQLITE_OK, "open fixture")
        check(sqlite3_exec(db, """
            CREATE TABLE meta(key TEXT, value TEXT); INSERT INTO meta VALUES ('version', '24');
            CREATE TABLE cookies(host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, path TEXT, expires_utc INTEGER,
              is_secure INTEGER, is_httponly INTEGER, samesite INTEGER, has_expires INTEGER, is_persistent INTEGER);
            """, nil, nil, nil) == SQLITE_OK, "schema")
        let later = chromeTime(now.addingTimeInterval(86_400))
        let rows: [(String, String, String, String, Int64, Int32, Int32, Int32)] = [
            (".example.com", "sid", "alpha", "/", later, 1, 1, 1),
            ("shop.example.com", "cart", "beta", cartPath, 0, 0, 0, -1),
            (".paypal.com", "pp", "delta", "/", later, 1, 1, 0),
            ("mail.google.com", "GMAIL", "epsilon", "/", later, 1, 1, 1),
            (".old.example.net", "old", "zeta", "/", chromeTime(now.addingTimeInterval(-10)), 0, 0, -1),
        ]
        for row in rows {
            var statement: OpaquePointer?
            sqlite3_prepare_v2(db, "INSERT INTO cookies VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?)", -1, &statement, nil)
            let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
            sqlite3_bind_text(statement, 1, row.0, -1, transient)
            sqlite3_bind_text(statement, 2, row.1, -1, transient)
            let blob = encrypt(row.2, host: row.0, version: 24)
            _ = blob.withUnsafeBytes { sqlite3_bind_blob(statement, 3, $0.baseAddress, Int32(blob.count), transient) }
            sqlite3_bind_text(statement, 4, row.3, -1, transient)
            sqlite3_bind_int64(statement, 5, row.4)
            sqlite3_bind_int(statement, 6, row.5)
            sqlite3_bind_int(statement, 7, row.6)
            sqlite3_bind_int(statement, 8, row.7)
            sqlite3_bind_int(statement, 9, row.4 > 0 ? 1 : 0)
            sqlite3_bind_int(statement, 10, row.4 > 0 ? 1 : 0)
            check(sqlite3_step(statement) == SQLITE_DONE, "insert")
            sqlite3_finalize(statement)
        }
        sqlite3_close(db)

        let profiles = ChromeCookies.profiles(root: root)
        check(profiles.map(\.label) == ["Google Chrome — Ada"], "profiles: \(profiles)")
        check((try? ChromeCookies.database(for: "../x", root: root)) == nil, "path refused")
        let table = try ChromeCookies.readRows(database: ChromeCookies.database(for: "Default", root: root))
        check(table.version == 24 && table.rows.count == 5, "rows")
        let sites = ChromeCookies.siteList(table.rows, now: now)
        check(sites.map { "\($0.site):\($0.count):\($0.category?.rawValue ?? "-"):\($0.checked)" } ==
              ["example.com:2:-:true", "google.com:1:email:false", "paypal.com:1:payments:false"], "sites: \(sites)")
        check(ChromeCookies.registrableDomain("www.bbc.co.uk") == "bbc.co.uk", "suffix")
        check(ChromeCookies.classify(site: "mybank.de") == .payments && ChromeCookies.classify(site: "google.com") == .signIn,
              "classify")

        let persistent = ChromeCookies.decryptCookies(table, password: password, sites: ["example.com", "paypal.com"],
                                                      persistent: true, now: now)
        check(persistent.skipped == 0, "skipped")
        check(persistent.cookies.map { "\($0.cookie.name)=\($0.cookie.value)" } == ["sid=alpha", "cart=beta", "pp=delta"], "values")
        let sid = persistent.cookies[0].cookie
        check(sid.domain == ".example.com" && sid.isSecure && sid.isHTTPOnly && sid.sameSitePolicy == .sameSiteLax &&
              !sid.isSessionOnly, "sid attributes")
        let cart = persistent.cookies[1].cookie
        check(cart.domain == "shop.example.com" && cart.path == cartPath && !cart.isSecure && cart.isSessionOnly, "cart attributes")
        let session = ChromeCookies.decryptCookies(table, password: password, sites: ["example.com"], persistent: false, now: now)
        check(session.cookies.allSatisfy { $0.cookie.isSessionOnly }, "session-only")
        let wrong = ChromeCookies.decryptCookies(table, password: Data("wrong".utf8), sites: ["example.com"], persistent: true, now: now)
        check(wrong.cookies.isEmpty && wrong.skipped == 2, "wrong password")
        // A value moved to another host's row fails the schema-24 hash check.
        let key = ChromeCookies.deriveKey(password)
        check(ChromeCookies.decrypt(encrypt("x", host: ".a.com", version: 24), key: key, version: 24, host: ".b.com") == nil, "hash")
        let line = ChromeCookies.auditLine(profile: "Default", persistent: true, imported: 3, skipped: 0,
                                           sites: Set(persistent.cookies.map(\.site)))
        check(!line.contains("alpha") && line.contains("paypal.com"), "audit line")
        print("chrome cookies: ok")
    }
}
