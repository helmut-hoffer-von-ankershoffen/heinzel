import Foundation
import LocalAuthentication
import Security

/// Only the short-lived, widget-scoped bearer belongs here, never a WebKit token.
struct WidgetCredential: Codable, Equatable {
    let endpoint: String
    let workspaceID: String
    let generation: String
    let token: String
    let expiresAt: Date

    func valid(at now: Date) -> Bool {
        token.wholeMatch(of: /hzw_[A-Za-z0-9_-]{32,128}/) != nil &&
        workspaceID.wholeMatch(of: /[a-f0-9]{64}/) != nil &&
        UUID(uuidString: generation) != nil && expiresAt > now &&
        expiresAt.timeIntervalSince(now) <= 25 * 60 * 60
    }
}

private struct IssuedWidgetCredential: Decodable {
    let token: String
    let workspaceID: String
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case token, workspaceID = "workspace_id", expiresAt = "expires_at"
    }

    var expiry: Date? {
        guard expiresAt.hasSuffix("Z") || expiresAt.hasSuffix("+00:00") else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: expiresAt) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: expiresAt)
    }
}

protocol WidgetCredentialKeychain {
    func read() -> Data?
    func write(_ data: Data) -> Bool
    func delete() -> Bool
}

/// The access group must be explicitly provisioned for both signed targets.
/// No app-container identifier or default Keychain group is used as a fallback.
struct SharedWidgetKeychain: WidgetCredentialKeychain {
    let accessGroup: String

    init?(accessGroup: String) {
        guard accessGroup.wholeMatch(of: /[A-Z0-9]{10}\.team\.heinzel\.widget/) != nil else { return nil }
        self.accessGroup = accessGroup
    }

    private var query: [String: Any] {
        let context = LAContext()
        context.interactionNotAllowed = true
        var result: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "team.heinzel.widget.credential",
            kSecAttrAccount as String: "active-workspace",
            kSecAttrAccessGroup as String: accessGroup,
            kSecAttrSynchronizable as String: false,
            kSecUseAuthenticationContext as String: context,
        ]
        #if os(macOS)
        result[kSecUseDataProtectionKeychain as String] = true
        #endif
        return result
    }

    func read() -> Data? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(request as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data, data.count <= 4096 else { return nil }
        return data
    }

    func write(_ data: Data) -> Bool {
        guard data.count <= 4096 else { return false }
        let attributes: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecSuccess { return true }
        guard status == errSecItemNotFound else { return false }
        var request = query
        for (key, value) in attributes { request[key] = value }
        return SecItemAdd(request as CFDictionary, nil) == errSecSuccess
    }

    func delete() -> Bool {
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }
}

/// Captured before network access; publication must use the unchanged writer token.
struct WidgetCredentialCapture {
    let activation: ActivityCatalog.Activation
    let credential: WidgetCredential
    let writerToken: Data
}

enum WidgetIssuanceResult {
    case installed(ActivityCatalog.Activation)
    case rejected
    case identityMismatch(ActivityCatalog.ClearResult)
}

struct WidgetCredentialStore {
    let catalogURL: URL
    let keychain: any WidgetCredentialKeychain

    /// Accept only the limited issuance response after the caller has bounded
    /// the transport. Paired-device and WebKit session credentials stay in WebKit.
    func installIssued(_ data: Data, expected: ActivityCatalog.Activation,
                       now: Date = .now) -> WidgetIssuanceResult {
        guard data.count <= 4096, let issued = try? JSONDecoder().decode(IssuedWidgetCredential.self, from: data),
              let expiry = issued.expiry else { return .rejected }
        let credential = WidgetCredential(endpoint: expected.endpoint, workspaceID: issued.workspaceID,
            generation: UUID().uuidString, token: issued.token, expiresAt: expiry)
        guard credential.valid(at: now) else { return .rejected }
        if let established = expected.workspaceID, established != issued.workspaceID {
            return .identityMismatch(invalidate(expected))
        }
        guard let installed = install(credential, expected: expected, now: now) else { return .rejected }
        return .installed(installed)
    }

    func install(_ credential: WidgetCredential, expected: ActivityCatalog.Activation,
                 now: Date = .now) -> ActivityCatalog.Activation? {
        guard credential.valid(at: now), credential.endpoint == expected.endpoint,
              let data = try? JSONEncoder().encode(credential) else { return nil }
        return ActivityCatalog.installCredential(url: catalogURL, expected: expected,
            workspaceID: credential.workspaceID, generation: credential.generation) { keychain.write(data) }
    }

    func capture(now: Date = .now) -> WidgetCredentialCapture? {
        guard let activation = ActivityCatalog.activation(url: catalogURL),
              let data = keychain.read(), let credential = try? JSONDecoder().decode(WidgetCredential.self, from: data),
              credential.valid(at: now), credential.endpoint == activation.endpoint,
              credential.workspaceID == activation.workspaceID,
              credential.generation == activation.credentialGeneration,
              let writerToken = ActivityCatalog.refreshToken(url: catalogURL),
              ActivityCatalog.activation(url: catalogURL) == activation else { return nil }
        return WidgetCredentialCapture(activation: activation, credential: credential, writerToken: writerToken)
    }

    /// Stale failures may revoke only the captured credential, including on retry.
    func invalidate(_ expected: ActivityCatalog.Activation) -> ActivityCatalog.ClearResult {
        ActivityCatalog.invalidate(url: catalogURL, expected: expected) { keychain.delete() }
    }

    /// Response-scoped invalidation does not consult SQLite before revocation.
    @MainActor @discardableResult static func invalidateShared(_ expected: ActivityCatalog.Activation) -> Bool {
        guard let url = ActivityWidgetData.sharedCatalogURL() else {
            NotificationCenter.default.post(name: ActivityWidgetData.clearFailed, object: nil)
            return false
        }
        let group = Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetKeychainGroup") as? String
        let keychain = group.flatMap(SharedWidgetKeychain.init(accessGroup:))
        let result = ActivityCatalog.invalidate(url: url, expected: expected) { keychain?.delete() ?? false }
        if result.revoked { ActivityWidgetData.didInvalidate() }
        if !result.stale && !result.complete { NotificationCenter.default.post(name: ActivityWidgetData.clearFailed, object: nil) }
        return result.complete
    }

    /// Caller must retry if a concurrent workspace replacement makes this stale.
    func clearCurrent() -> ActivityCatalog.ClearResult {
        guard let expected = ActivityCatalog.activation(url: catalogURL) ?? ActivityCatalog.pendingCleanup(url: catalogURL) else {
            return ActivityCatalog.ClearResult(revoked: false, purged: false)
        }
        return invalidate(expected)
    }

    /// Used by foreground logout and authentication failure. Revocation is
    /// published first; missing entitlements leave a retryable cleanup warning.
    @MainActor @discardableResult static func clearShared(expectedToken: Data? = nil) -> Bool {
        let snapshotCleared = ActivityWidgetData.clear(expectedToken: expectedToken)
        if expectedToken != nil && !snapshotCleared { return false }
        guard let url = ActivityWidgetData.sharedCatalogURL(),
              let pending = ActivityCatalog.pendingCleanup(url: url) else { return snapshotCleared }
        guard let group = Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetKeychainGroup") as? String,
              let keychain = SharedWidgetKeychain(accessGroup: group) else {
            NotificationCenter.default.post(name: ActivityWidgetData.clearFailed, object: nil)
            return false
        }
        let cleared = WidgetCredentialStore(catalogURL: url, keychain: keychain).invalidate(pending).complete
        if !cleared { NotificationCenter.default.post(name: ActivityWidgetData.clearFailed, object: nil) }
        return snapshotCleared && cleared
    }
}
