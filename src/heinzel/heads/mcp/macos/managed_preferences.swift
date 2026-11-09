// Prototype for a separate App Sandbox process. Do not link this into the Seatbelt driver or use
// the result as the observer's complete managed-policy evidence.

import CoreFoundation
import Darwin
import Foundation
import Security

enum ManagedPreferenceScope {
    case codex
    case claude

    var domain: String {
        switch self {
        case .codex: "com.openai.codex"
        case .claude: "com.anthropic.claudecode"
        }
    }

    var keys: [String] {
        switch self {
        case .codex:
            ["config_toml_base64", "requirements_toml_base64"]
        case .claude:
            ["allowAllClaudeAiMcps", "allowManagedMcpServersOnly", "allowedMcpServers",
             "deniedMcpServers", "disableClaudeAiConnectors", "managedMcpServers",
             "managedSourcesBehavior", "policyHelper"]
        }
    }
}

enum ManagedPreferenceFailure: Error, Equatable {
    case missingConfinement
    case unavailable
    case changedDuringRead
}

// These are presence observations for fixed keys. Even an empty result leaves vendor policy unverified.
struct ManagedPreferencePresence: Equatable {
    let domain: String
    let observedKeys: [String]
    let forcedKeys: [String]
}

func readManagedPreferencePresence(_ scope: ManagedPreferenceScope) throws -> ManagedPreferencePresence {
    guard getuid() != 0, getuid() == geteuid(), getgid() == getegid(),
        let task = SecTaskCreateFromSelf(nil),
        entitlement(task, "com.apple.security.app-sandbox") as? Bool == true,
        entitlement(task, "com.apple.security.temporary-exception.shared-preference.read-only") as? [String]
            == [scope.domain],
        entitlement(task, "com.apple.security.temporary-exception.shared-preference.read-write") == nil
    else { throw ManagedPreferenceFailure.missingConfinement }

    let domain = scope.domain as CFString
    return try sampleManagedPreferencePresence(
        scope,
        synchronize: { CFPreferencesAppSynchronize(domain) },
        isForced: { CFPreferencesAppValueIsForced($0 as CFString, domain) }
    )
}

private func entitlement(_ task: SecTask, _ name: String) -> CFTypeRef? {
    SecTaskCopyValueForEntitlement(task, name as CFString, nil)
}

func sampleManagedPreferencePresence(
    _ scope: ManagedPreferenceScope,
    synchronize: () -> Bool,
    isForced: (String) -> Bool
) throws -> ManagedPreferencePresence {
    guard synchronize() else { throw ManagedPreferenceFailure.unavailable }
    let keys = scope.keys
    let forced = keys.filter(isForced)
    guard synchronize() else { throw ManagedPreferenceFailure.unavailable }
    guard forced == keys.filter(isForced) else { throw ManagedPreferenceFailure.changedDuringRead }
    return ManagedPreferencePresence(domain: scope.domain, observedKeys: keys, forcedKeys: forced)
}
