#if os(macOS)
import CommonCrypto
import CryptoKit
import Foundation
import Security
import SQLite3

/// "Import cookies from your browser": reads the person's own Google Chrome profiles on this Mac. Values stay in this
/// process and go only into the built-in browser's own cookie store; never to the workspace, a page or the network.
struct ChromeProfile: Identifiable, Hashable {
    let id: String
    let name: String
    var label: String { "Google Chrome — \(name)" }
}

struct ChromeCookieRow {
    let host: String
    let name: String
    let value: String
    let encrypted: Data
    let path: String
    let expiresUTC: Int64
    let secure: Bool
    let httpOnly: Bool
    let sameSite: Int
    let persistent: Bool
}

struct ChromeCookieTable {
    let version: Int
    let rows: [ChromeCookieRow]
}

enum CookieCategory: String {
    case email, payments, signIn = "sign-in"
    var label: String {
        switch self {
        case .email: "Email"
        case .payments: "Bank or payments"
        case .signIn: "Sign-in provider"
        }
    }
}

struct CookieSite: Identifiable, Hashable {
    let site: String
    let count: Int
    let category: CookieCategory?
    var id: String { site }
    /// Sensitive sites start unchecked: a person picks them on purpose.
    var checked: Bool { category == nil }
}

enum ChromeCookieError: LocalizedError {
    case unknownProfile, unreadable, locked
    var errorDescription: String? {
        switch self {
        case .unknownProfile: "This Google Chrome profile was not found."
        case .unreadable: "Google Chrome's cookies could not be read."
        case .locked: "Google Chrome's cookies could not be unlocked."
        }
    }
}

enum ChromeCookies {
    /// Chrome's cookie database schema 24 (Chrome 130) puts SHA-256 of the host before each value, then encrypts.
    static let domainHashVersion = 24
    /// The path of a cookie for the whole site.
    private static let cookieRoot = "/"
    private static let windowsEpochOffset: Double = 11_644_473_600
    private static let maxRows = 50_000

    static func root(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appending(path: "Library/Application Support/Google/Chrome", directoryHint: .isDirectory)
    }

    static func isProfileID(_ id: String) -> Bool {
        id == "Default" || id.wholeMatch(of: /Profile \d{1,4}/) != nil
    }

    /// The profiles Chrome lists in `Local State` that have a cookie database.
    static func profiles(root: URL = root()) -> [ChromeProfile] {
        guard let data = try? Data(contentsOf: root.appending(path: "Local State")),
              let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let cache = (state["profile"] as? [String: Any])?["info_cache"] as? [String: Any] else { return [] }
        return cache.compactMap { id, info -> ChromeProfile? in
            guard isProfileID(id), database(profile: root.appending(path: id)) != nil else { return nil }
            let raw = ((info as? [String: Any])?["name"] as? String)?.trimmingCharacters(in: .whitespaces) ?? ""
            return ChromeProfile(id: id, name: raw.isEmpty ? id : String(raw.prefix(80)))
        }.sorted { $0.id == "Default" || ($1.id != "Default" && $0.id < $1.id) }
    }

    /// Chrome 96 and later keep cookies in `Network/Cookies`; older profiles in `Cookies`.
    static func database(profile: URL) -> URL? {
        [profile.appending(path: "Network/Cookies"), profile.appending(path: "Cookies")].first {
            var directory: ObjCBool = false
            return FileManager.default.fileExists(atPath: $0.path, isDirectory: &directory) && !directory.boolValue
        }
    }

    static func database(for profile: String, root: URL = root()) throws -> URL {
        guard isProfileID(profile), profiles(root: root).contains(where: { $0.id == profile }),
              let file = database(profile: root.appending(path: profile)) else { throw ChromeCookieError.unknownProfile }
        return file
    }

    /// Reads a private copy (with its write-ahead log): Chrome may hold a lock, and its files are never written.
    static func readRows(database: URL) throws -> ChromeCookieTable {
        let fileManager = FileManager.default
        let directory = fileManager.temporaryDirectory.appending(path: "hz-cookie-import-\(UUID().uuidString)")
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: false,
                                        attributes: [.posixPermissions: 0o700])
        defer { try? fileManager.removeItem(at: directory) }
        let copy = directory.appending(path: "Cookies")
        try fileManager.copyItem(at: database, to: copy)
        for suffix in ["-wal", "-journal"] {
            try? fileManager.copyItem(at: URL(filePath: database.path + suffix), to: URL(filePath: copy.path + suffix))
        }
        var handle: OpaquePointer?
        guard sqlite3_open_v2(copy.path, &handle, SQLITE_OPEN_READWRITE, nil) == SQLITE_OK, let db = handle else {
            sqlite3_close(handle)
            throw ChromeCookieError.unreadable
        }
        defer { sqlite3_close(db) }
        let version = Int(try query(db, "SELECT value FROM meta WHERE key = 'version'") { text($0, 0) }.first ?? "0") ?? 0
        let rows = try query(db, """
            SELECT host_key, name, value, encrypted_value, path, expires_utc, is_secure, is_httponly, samesite,
            has_expires, is_persistent FROM cookies LIMIT \(maxRows)
            """) { statement in
            ChromeCookieRow(
                host: text(statement, 0), name: text(statement, 1), value: text(statement, 2),
                encrypted: blob(statement, 3), path: text(statement, 4), expiresUTC: sqlite3_column_int64(statement, 5),
                secure: sqlite3_column_int(statement, 6) == 1, httpOnly: sqlite3_column_int(statement, 7) == 1,
                sameSite: Int(sqlite3_column_int(statement, 8)),
                persistent: sqlite3_column_int(statement, 9) == 1 || sqlite3_column_int(statement, 10) == 1
            )
        }
        return ChromeCookieTable(version: version, rows: rows)
    }

    private static func query<T>(_ db: OpaquePointer, _ sql: String, _ row: (OpaquePointer) -> T) throws -> [T] {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw ChromeCookieError.unreadable
        }
        defer { sqlite3_finalize(statement) }
        var result: [T] = []
        while true {
            let step = sqlite3_step(statement)
            if step == SQLITE_DONE { return result }
            guard step == SQLITE_ROW else { throw ChromeCookieError.unreadable }
            result.append(row(statement))
        }
    }

    private static func text(_ statement: OpaquePointer, _ column: Int32) -> String {
        guard let value = sqlite3_column_text(statement, column) else { return "" }
        return String(cString: value)
    }

    private static func blob(_ statement: OpaquePointer, _ column: Int32) -> Data {
        let count = Int(sqlite3_column_bytes(statement, column))
        guard count > 0, let bytes = sqlite3_column_blob(statement, column) else { return Data() }
        return Data(bytes: bytes, count: count)
    }

    /// The password Chrome keeps in the login Keychain. macOS asks the person first; it is never stored here.
    static func keychainPassword() throws -> Data {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "Chrome Safe Storage",
            kSecAttrAccount as String: "Chrome", kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data, !data.isEmpty
        else { throw ChromeCookieError.locked }
        return data
    }

    /// Chrome's macOS key: PBKDF2-SHA1 of the Keychain password, salt "saltysalt", 1003 rounds, 16 bytes.
    static func deriveKey(_ password: Data) -> Data {
        var secret = password.map { CChar(bitPattern: $0) }
        defer { secret.withUnsafeMutableBufferPointer { $0.update(repeating: 0) } }
        let salt = Array("saltysalt".utf8)
        var key = [UInt8](repeating: 0, count: kCCKeySizeAES128)
        defer { key.withUnsafeMutableBufferPointer { $0.update(repeating: 0) } }
        let status = CCKeyDerivationPBKDF(
            CCPBKDFAlgorithm(kCCPBKDF2), secret, secret.count, salt, salt.count,
            CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA1), 1003, &key, key.count
        )
        return status == kCCSuccess ? Data(key) : Data()
    }

    /// AES-128-CBC with PKCS#7 padding and Chrome's fixed IV of 16 spaces: the format Chrome writes, which is
    /// only ever read here (and written by the test fixture). The caller wipes the result.
    static func chromeAES(_ operation: Int, _ input: [UInt8], key: Data) -> [UInt8]? {
        var keyBytes = [UInt8](key)
        defer { keyBytes.withUnsafeMutableBufferPointer { $0.update(repeating: 0) } }
        let iv = [UInt8](repeating: 0x20, count: kCCBlockSizeAES128)
        var output = [UInt8](repeating: 0, count: input.count + kCCBlockSizeAES128)
        let capacity = output.count
        var written = 0
        let status = CCCrypt(CCOperation(operation), CCAlgorithm(kCCAlgorithmAES), CCOptions(kCCOptionPKCS7Padding),
                             keyBytes, keyBytes.count, iv, input, input.count, &output, capacity, &written)
        guard status == kCCSuccess else {
            output.withUnsafeMutableBufferPointer { $0.update(repeating: 0) }
            return nil
        }
        output.removeSubrange(written...)
        return output
    }

    /// v10 (macOS) and v11 are AES-128-CBC with an IV of 16 spaces. From schema 24 the plaintext starts with
    /// SHA-256(host), which must match the row: a value copied from another site's row is refused.
    static func decrypt(_ encrypted: Data, key: Data, version: Int, host: String) -> String? {
        guard key.count == kCCKeySizeAES128, encrypted.count >= 3 + kCCBlockSizeAES128 else { return nil }
        let prefix = String(decoding: encrypted.prefix(3), as: UTF8.self)
        guard prefix == "v10" || prefix == "v11" else { return nil }
        guard var plain = chromeAES(kCCDecrypt, [UInt8](encrypted.dropFirst(3)), key: key) else { return nil }
        defer { plain.withUnsafeMutableBufferPointer { $0.update(repeating: 0) } }
        var start = 0
        if version >= domainHashVersion {
            guard plain.count >= 32, Array(SHA256.hash(data: Data(host.utf8))) == Array(plain[0..<32]) else { return nil }
            start = 32
        }
        return String(bytes: plain[start...], encoding: .utf8)
    }

    private static let secondLevel: Set<String> = [
        "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "com.au", "net.au", "org.au", "co.jp", "ne.jp", "or.jp", "co.nz",
        "co.za", "com.br", "com.mx", "com.cn", "com.tr", "co.in", "co.kr", "com.sg", "com.hk", "co.at", "or.at", "com.ar",
        "co.il", "com.tw", "github.io", "herokuapp.com", "vercel.app", "netlify.app", "pages.dev", "workers.dev",
        "appspot.com", "cloudfront.net", "azurewebsites.net",
    ]

    /// The site a cookie host belongs to ("mail.google.com" -> "google.com"), from a small built-in suffix list.
    static func registrableDomain(_ host: String) -> String {
        var name = host.lowercased()
        if name.hasPrefix(".") { name.removeFirst() }
        if name.hasSuffix(".") { name.removeLast() }
        if name.isEmpty || name.contains(":") || name.allSatisfy({ $0.isNumber || $0 == "." }) { return name }
        let labels = name.split(separator: ".").map(String.init)
        guard labels.count > 2 else { return name }
        let tail = labels.suffix(2).joined(separator: ".")
        return labels.suffix(secondLevel.contains(tail) ? 3 : 2).joined(separator: ".")
    }

    // The same short lists as the Electron app (desktop/src/chrome-cookies.js).
    private static let signIn: Set<String> = [
        "google.com", "apple.com", "icloud.com", "microsoft.com", "microsoftonline.com", "live.com", "okta.com",
        "auth0.com", "onelogin.com", "duosecurity.com", "facebook.com", "github.com", "gitlab.com", "login.gov",
        "amazon.com", "yahoo.com", "x.com", "twitter.com", "linkedin.com", "1password.com", "bitwarden.com",
        "lastpass.com", "id.me", "jumpcloud.com", "pingidentity.com", "auth.gov", "salesforce.com", "slack.com",
        "atlassian.com",
    ]
    private static let email: Set<String> = [
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "office.com", "office365.com", "proton.me",
        "protonmail.com", "fastmail.com", "zoho.com", "gmx.de", "gmx.net", "gmx.com", "web.de", "t-online.de",
        "posteo.de", "mailbox.org", "tutanota.com", "tuta.com", "aol.com", "mail.com", "mail.ru", "yandex.com",
        "yandex.ru", "hey.com", "superhuman.com",
    ]
    private static let payments: Set<String> = [
        "paypal.com", "paypal.me", "stripe.com", "wise.com", "revolut.com", "n26.com", "klarna.com", "chase.com",
        "bankofamerica.com", "wellsfargo.com", "citi.com", "capitalone.com", "americanexpress.com", "amex.com",
        "discover.com", "usbank.com", "schwab.com", "fidelity.com", "vanguard.com", "robinhood.com", "venmo.com",
        "cash.app", "squareup.com", "adyen.com", "coinbase.com", "kraken.com", "binance.com", "monzo.com",
        "starlingbank.com", "barclays.co.uk", "hsbc.com", "hsbc.co.uk", "lloydsbank.com", "natwest.com", "santander.com",
        "ing.com", "ing.de", "dkb.de", "comdirect.de", "consorsbank.de", "postbank.de", "deutsche-bank.de", "db.com",
        "commerzbank.de", "sparkasse.de", "volksbank.de", "vr.de", "targobank.de", "trade-republic.com",
        "traderepublic.com", "scalable.capital", "bunq.com", "ubs.com", "credit-suisse.com", "postfinance.ch",
        "raiffeisen.ch", "apple.cash", "affirm.com", "afterpay.com", "mollie.com", "braintreegateway.com", "paddle.com",
        "lemonsqueezy.com",
    ]

    static func classify(site: String, hosts: [String] = []) -> CookieCategory? {
        let first = site.split(separator: ".").first.map(String.init) ?? ""
        let paymentWord = first.contains(/bank|sparkasse|raiffeisen|credit|wallet|finanz|broker/) ||
            first.hasPrefix("pay") || first.hasSuffix("pay")
        if payments.contains(site) || paymentWord { return .payments }
        if email.contains(site) { return .email }
        if hosts.contains(where: { $0.contains(/^\.?(mail|outlook|webmail|inbox)\./) }) { return .email }
        if signIn.contains(site) { return .signIn }
        return nil
    }

    private static func expires(_ row: ChromeCookieRow) -> Date {
        Date(timeIntervalSince1970: Double(row.expiresUTC) / 1_000_000 - windowsEpochOffset)
    }

    private static func expired(_ row: ChromeCookieRow, now: Date) -> Bool {
        row.persistent && row.expiresUTC > 0 && expires(row) <= now
    }

    /// The sites of a profile, without decrypting anything.
    static func siteList(_ rows: [ChromeCookieRow], now: Date = Date()) -> [CookieSite] {
        var sites: [String: (count: Int, hosts: Set<String>)] = [:]
        for row in rows where !expired(row, now: now) {
            let site = registrableDomain(row.host)
            guard !site.isEmpty else { continue }
            sites[site, default: (0, [])].count += 1
            sites[site]?.hosts.insert(row.host)
        }
        return sites.map { CookieSite(site: $0.key, count: $0.value.count, category: classify(site: $0.key, hosts: Array($0.value.hosts))) }
            .sorted { $0.site < $1.site }
    }

    /// One row as an `HTTPCookie`, or nil. Host-only cookies keep no leading dot, `__Host-` must be secure on "/"
    /// and host-only, `SameSite=None` must be secure. Not `persistent`: a session cookie, gone when the app quits.
    static func cookie(_ row: ChromeCookieRow, value: String, persistent: Bool, now: Date = Date()) -> HTTPCookie? {
        let host = row.host.lowercased()
        guard !host.isEmpty, !row.name.isEmpty, !expired(row, now: now) else { return nil }
        let hostOnly = !host.hasPrefix(".")
        let path = row.path.hasPrefix(cookieRoot) ? row.path : cookieRoot
        if row.name.hasPrefix("__Host-") && (!row.secure || !hostOnly || path != cookieRoot) { return nil }
        if row.name.hasPrefix("__Secure-") && !row.secure { return nil }
        if row.sameSite == 0 && !row.secure { return nil }
        var properties: [HTTPCookiePropertyKey: Any] = [
            .name: row.name, .value: value, .path: path, .domain: host,
        ]
        if row.secure { properties[.secure] = "TRUE" }
        if row.httpOnly { properties[HTTPCookiePropertyKey("HttpOnly")] = "TRUE" }
        switch row.sameSite {
        case 0: properties[.sameSitePolicy] = "none"
        case 1: properties[.sameSitePolicy] = HTTPCookieStringPolicy.sameSiteLax.rawValue
        case 2: properties[.sameSitePolicy] = HTTPCookieStringPolicy.sameSiteStrict.rawValue
        default: break
        }
        if persistent && row.persistent && row.expiresUTC > 0 { properties[.expires] = expires(row) }
        else { properties[.discard] = "TRUE" }
        return HTTPCookie(properties: properties)
    }

    /// The cookies of the chosen sites, with a key derived once and wiped before this returns.
    static func decryptCookies(_ table: ChromeCookieTable, password: Data, sites: Set<String>, persistent: Bool,
                               now: Date = Date()) -> (cookies: [(site: String, cookie: HTTPCookie)], skipped: Int) {
        var key = deriveKey(password)
        defer { key.resetBytes(in: 0..<key.count) }
        var cookies: [(site: String, cookie: HTTPCookie)] = []
        var skipped = 0
        for row in table.rows {
            let site = registrableDomain(row.host)
            guard sites.contains(site) else { continue }
            let value = row.value.isEmpty ? decrypt(row.encrypted, key: key, version: table.version, host: row.host) : row.value
            if let value, let cookie = cookie(row, value: value, persistent: persistent, now: now) {
                cookies.append((site, cookie))
            } else { skipped += 1 }
        }
        return (cookies, skipped)
    }

    /// The one audit line of an import: counts and site names, never a cookie name or value.
    static func auditLine(profile: String, persistent: Bool, imported: Int, skipped: Int, sites: Set<String>) -> String {
        let entry: [String: Any] = [
            "event": "native.cookies.import", "profile": profile, "persistent": persistent, "imported": imported,
            "skipped": skipped, "sites": sites.sorted(),
        ]
        let data = (try? JSONSerialization.data(withJSONObject: entry, options: [.sortedKeys])) ?? Data()
        return String(decoding: data, as: UTF8.self)
    }
}
#endif
