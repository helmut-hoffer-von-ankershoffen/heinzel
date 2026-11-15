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

    var name: String {
        let host = hostName ?? URL(string: address)?.host() ?? address
        if host.hasSuffix(".ts.net") || host.hasSuffix(".local") {
            let firstLabel = String(host.split(separator: ".").first ?? Substring(host))
            return firstLabel.replacingOccurrences(of: "-", with: " ").localizedCapitalized
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

    static func workspaces() -> [RecentWorkspace] {
        var query = query()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitAll
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? [Data] else { return [] }
        return data.compactMap { try? JSONDecoder().decode(SyncedWorkspace.self, from: $0).workspace }
    }

    static func signInLink(address: String, newerThan forgotten: Date?) -> String? {
        guard let entry = entry(address: address), entry.workspace.lastUsed > (forgotten ?? .distantPast),
              let url = URL(string: address),
              var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        components.fragment = "token=\(entry.token)"
        return components.url.flatMap { Endpoint($0.absoluteString)?.url.absoluteString }
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
