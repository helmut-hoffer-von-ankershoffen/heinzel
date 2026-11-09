import Darwin
import Foundation

@main
struct ManagedPreferencesTest {
    static func main() {
        do {
            try run()
            print("Managed preference prototype checks passed")
        } catch {
            FileHandle.standardError.write(Data("Managed preference prototype check failed: \(error)\n".utf8))
            exit(1)
        }
    }

    private struct Unexpected: Error {
        let check: String
    }

    private static func require(_ matches: Bool, _ check: String) throws {
        guard matches else { throw Unexpected(check: check) }
    }

    private static func requireFailure(
        _ expected: ManagedPreferenceFailure,
        _ operation: () throws -> ManagedPreferencePresence
    ) throws {
        do {
            _ = try operation()
            throw Unexpected(check: "Lookup succeeded instead of \(expected)")
        } catch let failure as ManagedPreferenceFailure {
            try require(failure == expected, "Expected \(expected), got \(failure)")
        }
    }

    private static func run() throws {
        let codexKeys = ["config_toml_base64", "requirements_toml_base64"]
        var examined: [String] = []
        let codex = try sampleManagedPreferencePresence(.codex, synchronize: { true }) { key in
            examined.append(key)
            return key == "requirements_toml_base64"
        }
        try require(codex.domain == "com.openai.codex", "Codex preference domain")
        try require(codex.observedKeys == codexKeys, "Codex preference keys")
        try require(codex.forcedKeys == ["requirements_toml_base64"], "Forced Codex requirement")
        try require(examined == codexKeys + codexKeys, "Forced state verified twice")

        let claude = try sampleManagedPreferencePresence(.claude, synchronize: { true }, isForced: { _ in false })
        try require(claude.domain == "com.anthropic.claudecode", "Claude Code preference domain")
        try require(claude.observedKeys.contains("policyHelper"), "Claude Code policy helper considered")
        try require(claude.forcedKeys.isEmpty, "No Claude Code keys observed as forced")

        examined = []
        try requireFailure(.unavailable) {
            try sampleManagedPreferencePresence(.codex, synchronize: { false }) { key in
                examined.append(key)
                return false
            }
        }
        try require(examined.isEmpty, "Denied synchronization skips queries")

        var syncCount = 0
        try requireFailure(.unavailable) {
            try sampleManagedPreferencePresence(.codex, synchronize: {
                syncCount += 1
                return syncCount == 1
            }, isForced: { _ in false })
        }

        syncCount = 0
        try requireFailure(.changedDuringRead) {
            try sampleManagedPreferencePresence(.codex, synchronize: {
                syncCount += 1
                return true
            }, isForced: { _ in syncCount == 1 })
        }

        // This fixture build carries no App Sandbox entitlements. Both calls must stop
        // before touching CoreFoundation preferences for real vendor domains.
        try requireFailure(.missingConfinement) { try readManagedPreferencePresence(.codex) }
        try requireFailure(.missingConfinement) { try readManagedPreferencePresence(.claude) }
    }
}
