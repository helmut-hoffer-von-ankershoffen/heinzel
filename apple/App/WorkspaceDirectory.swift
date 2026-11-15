import Foundation
import Security

struct RecentWorkspace: Codable, Identifiable {
    let address: String
    var hostName: String?
    var lastUsed: Date
    var source: Source?

    enum Source: String, Codable {
        case pasted, iCloud
    }

    var id: String { address }

    var name: String { Self.displayName(for: address, hostName: hostName) }

    static func displayName(for address: String, hostName: String? = nil) -> String {
        let host = hostName ?? URL(string: address)?.host() ?? address
        if host.hasSuffix(".ts.net") || host.hasSuffix(".local") {
            let firstLabel = host.split(separator: ".").first ?? Substring(host)
            return firstLabel.split(separator: "-").map { word in
                word.lowercased() == "macbook" ? "MacBook" : String(word).localizedCapitalized
            }.joined(separator: " ")
        }
        return host
    }
}

private struct SyncedWorkspace: Codable {
    let workspace: RecentWorkspace
    let token: String
}

enum WorkspaceKeychain {
    private static let service = "team.heinzel.apple.workspace-sign-in.v1"

    private static func query(address: String? = nil) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrSynchronizable as String: true
        ]
        #if os(macOS)
        query[kSecUseDataProtectionKeychain as String] = true
        #endif
        if let address { query[kSecAttrAccount as String] = address }
        return query
    }

    private static func entry(address: String) -> SyncedWorkspace? {
        var query = query(address: address)
        query[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(SyncedWorkspace.self, from: data)
    }

    // nil means Keychain could not answer. Only an authoritative empty result clears the directory.
    static func workspaces() -> [RecentWorkspace]? {
        var query = query()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitAll
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return [] }
        guard status == errSecSuccess, let data = result as? [Data] else { return nil }
        let workspaces = data.compactMap { try? JSONDecoder().decode(SyncedWorkspace.self, from: $0).workspace }
        return workspaces.count == data.count ? workspaces : nil
    }

    static func signIn(address: String, newerThan forgotten: Date?) -> (link: String, workspace: RecentWorkspace)? {
        guard let entry = entry(address: address), entry.workspace.lastUsed > (forgotten ?? .distantPast),
              let url = URL(string: address),
              var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        components.fragment = "token=\(entry.token)"
        guard let value = components.url.flatMap({ Endpoint($0.absoluteString)?.url.absoluteString }) else { return nil }
        return (value, entry.workspace)
    }

    static func publish(workspace: RecentWorkspace, token: String) -> Bool {
        let query = query(address: workspace.address)
        if let previous = entry(address: workspace.address), previous.token == token { return true }
        guard let data = try? JSONEncoder().encode(SyncedWorkspace(workspace: workspace, token: token)) else { return false }
        switch SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary) {
        case errSecSuccess: return true
        case errSecItemNotFound:
            var item = query
            item[kSecValueData as String] = data
            item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked
            return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
        default: return false
        }
    }

    @discardableResult
    static func remove(address: String) -> Bool {
        let result = SecItemDelete(query(address: address) as CFDictionary)
        return result == errSecSuccess || result == errSecItemNotFound
    }
}
