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
    var pendingAttempt: String? = nil

    func valid(at now: Date) -> Bool {
        token.wholeMatch(of: /hzw_[A-Za-z0-9_-]{32,128}/) != nil &&
        workspaceID.wholeMatch(of: /[a-f0-9]{64}/) != nil &&
        UUID(uuidString: generation) != nil && expiresAt > now &&
        expiresAt.timeIntervalSince(now) <= 25 * 60 * 60 &&
        (pendingAttempt.map { $0.wholeMatch(of: /[a-f0-9]{32}/) != nil } ?? true)
    }
}

enum WidgetKeychainRead {
    case data(Data)
    case missing
    case unavailable
}

protocol WidgetCredentialKeychain {
    func preflight() -> Bool
    func read() -> Data?
    func readStatus() -> WidgetKeychainRead
    func write(_ data: Data) -> Bool
    func delete() -> Bool
    func readPending(_ attempt: String) -> Data?
    func readPendingStatus(_ attempt: String) -> WidgetKeychainRead
    func createPending(_ data: Data, attempt: String) -> Bool
    func updatePending(_ data: Data, attempt: String) -> Bool
    func deletePending(_ attempt: String) -> Bool
}

extension WidgetCredentialKeychain {
    func readPendingStatus(_ attempt: String) -> WidgetKeychainRead { .unavailable }
}

/// The access group must be explicitly provisioned for both signed targets.
/// No app-container identifier or default Keychain group is used as a fallback.
struct SharedWidgetKeychain: WidgetCredentialKeychain {
    let accessGroup: String

    init?(accessGroup: String) {
        guard accessGroup.wholeMatch(of: /[A-Z0-9]{10}\.team\.heinzel\.widget/) != nil else { return nil }
        self.accessGroup = accessGroup
    }

    private func query(account: String) -> [String: Any] {
        let context = LAContext()
        context.interactionNotAllowed = true
        var result: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "team.heinzel.widget.credential",
            kSecAttrAccount as String: account,
            kSecAttrAccessGroup as String: accessGroup,
            kSecAttrSynchronizable as String: false,
            kSecUseAuthenticationContext as String: context,
        ]
        #if os(macOS)
        result[kSecUseDataProtectionKeychain as String] = true
        #endif
        return result
    }

    private var query: [String: Any] { query(account: "active-workspace") }

    private func pendingQuery(_ attempt: String) -> [String: Any]? {
        guard attempt.wholeMatch(of: /[a-f0-9]{32}/) != nil else { return nil }
        return query(account: "pending-" + attempt)
    }

    func read() -> Data? {
        if case .data(let data) = readStatus() { return data }
        return nil
    }

    func readStatus() -> WidgetKeychainRead {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return .missing }
        guard status == errSecSuccess, let data = result as? Data, data.count <= 4096 else { return .unavailable }
        return .data(data)
    }

    func preflight() -> Bool {
        var probe = query
        probe[kSecAttrAccount as String] = "probe-" + UUID().uuidString
        var attributes = probe
        attributes[kSecValueData as String] = Data([0])
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(attributes as CFDictionary, nil) == errSecSuccess else { return false }
        return SecItemDelete(probe as CFDictionary) == errSecSuccess
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

    func readPending(_ attempt: String) -> Data? {
        if case .data(let data) = readPendingStatus(attempt) { return data }
        return nil
    }

    func readPendingStatus(_ attempt: String) -> WidgetKeychainRead {
        guard var request = pendingQuery(attempt) else { return .unavailable }
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return .missing }
        guard status == errSecSuccess, let data = result as? Data, data.count <= 4096 else { return .unavailable }
        return .data(data)
    }

    func createPending(_ data: Data, attempt: String) -> Bool {
        guard data.count <= 4096, var request = pendingQuery(attempt) else { return false }
        request[kSecValueData as String] = data
        request[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(request as CFDictionary, nil) == errSecSuccess
    }

    func updatePending(_ data: Data, attempt: String) -> Bool {
        guard data.count <= 4096, let request = pendingQuery(attempt) else { return false }
        let attributes: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        return SecItemUpdate(request as CFDictionary, attributes as CFDictionary) == errSecSuccess
    }

    func deletePending(_ attempt: String) -> Bool {
        guard let request = pendingQuery(attempt) else { return false }
        let status = SecItemDelete(request as CFDictionary)
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
    static let activationChanged = Notification.Name("HeinzelWidgetActivationChanged")
    let catalogURL: URL
    let keychain: any WidgetCredentialKeychain

    /// Enrollment stays disabled unless the signed app explicitly provisions it.
    static func configured() -> WidgetCredentialStore? {
        guard Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetEnrollmentEnabled") as? Bool == true,
              let url = ActivityWidgetData.sharedCatalogURL(),
              let group = Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetKeychainGroup") as? String,
              let keychain = SharedWidgetKeychain(accessGroup: group) else { return nil }
        return WidgetCredentialStore(catalogURL: url, keychain: keychain)
    }

    /// Reserve before any network call. Pending attempts are never retried
    /// automatically because a lost response may already have rotated Core.
    func beginEnrollment(expected: ActivityCatalog.Activation) -> ActivityCatalog.Enrollment? {
        guard keychain.preflight() else { return nil }
        return ActivityCatalog.reserveEnrollment(url: catalogURL, expected: expected)
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

    /// Metadata for renewal only; never authorizes a widget request.
    func renewalCredential(expected: ActivityCatalog.Activation, now: Date = .now) -> WidgetCredential? {
        guard case .data(let data) = keychain.readStatus(),
              let credential = try? JSONDecoder().decode(WidgetCredential.self, from: data),
              credential.valid(at: credential.expiresAt <= now ? credential.expiresAt.addingTimeInterval(-1) : now),
              credential.endpoint == expected.endpoint, credential.workspaceID == expected.workspaceID,
              credential.generation == expected.credentialGeneration,
              ActivityCatalog.activation(url: catalogURL) == expected else { return nil }
        return credential
    }

    /// Stale failures may revoke only the captured credential, including on retry.
    func invalidate(_ expected: ActivityCatalog.Activation) -> ActivityCatalog.ClearResult {
        let pending = ActivityCatalog.pendingEnrollment(url: catalogURL)
        let recovery = pending.flatMap { $0.previous == expected ? $0 : nil }
        return ActivityCatalog.invalidate(url: catalogURL, expected: expected, recovery: recovery,
                                          recoveryCheck: { WidgetFencedEnrollment(store: self).recoveryEvidenceMatches($0) }) {
            var pendingDeleted = true
            if let pending = recovery {
                pendingDeleted = keychain.deletePending(pending.id.replacingOccurrences(of: "-", with: "").lowercased())
            }
            let activeDeleted = purgeCredential()
            return pendingDeleted && activeDeleted
        }
    }

    /// Keep the active item as a retry pointer if deleting its pending copy fails.
    func purgeCredential() -> Bool {
        switch keychain.readStatus() {
        case .unavailable: return false
        case .missing: return keychain.delete()
        case .data(let data):
            guard let value = try? JSONDecoder().decode(WidgetCredential.self, from: data) else { return false }
            if let attempt = value.pendingAttempt, !keychain.deletePending(attempt) { return false }
            return keychain.delete()
        }
    }

    /// Response-scoped invalidation does not consult SQLite before revocation.
    @MainActor @discardableResult static func invalidateShared(_ expected: ActivityCatalog.Activation) -> Bool {
        guard let url = ActivityWidgetData.sharedCatalogURL() else {
            NotificationCenter.default.post(name: ActivityWidgetData.clearFailed, object: nil)
            return false
        }
        let group = Bundle.main.object(forInfoDictionaryKey: "HeinzelWidgetKeychainGroup") as? String
        let keychain = group.flatMap(SharedWidgetKeychain.init(accessGroup:))
        let result = keychain.map { WidgetCredentialStore(catalogURL: url, keychain: $0).invalidate(expected) }
            ?? ActivityCatalog.invalidate(url: url, expected: expected) { false }
        if result.revoked { ActivityWidgetData.didInvalidate() }
        if !result.stale && !result.complete { NotificationCenter.default.post(name: ActivityWidgetData.clearFailed, object: nil) }
        return result.complete
    }

    /// Caller must retry if a concurrent workspace replacement makes this stale.
    func clearCurrent() -> ActivityCatalog.ClearResult {
        if let expected = ActivityCatalog.activation(url: catalogURL) ?? ActivityCatalog.pendingCleanup(url: catalogURL) {
            return invalidate(expected)
        }
        if let pending = ActivityCatalog.pendingEnrollment(url: catalogURL) {
            return WidgetFencedEnrollment(store: self).cancel(pending)
        }
        return ActivityCatalog.ClearResult(revoked: false, purged: false)
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
