import CryptoKit
import Foundation

/// Fictional test-only keychain persisted across harness processes.
private struct FixtureKeychain: WidgetCredentialKeychain {
    let directory: URL
    private func path(_ name: String) -> URL { directory.appendingPathComponent(name) }
    func preflight() -> Bool { true }
    func read() -> Data? { try? Data(contentsOf: path("active")) }
    func readStatus() -> WidgetKeychainRead { read().map(WidgetKeychainRead.data) ?? .missing }
    func write(_ data: Data) -> Bool { (try? data.write(to: path("active"), options: .atomic)) != nil }
    func delete() -> Bool { (try? FileManager.default.removeItem(at: path("active"))) != nil || read() == nil }
    func readPending(_ attempt: String) -> Data? { try? Data(contentsOf: path("pending-" + attempt)) }
    func readPendingStatus(_ attempt: String) -> WidgetKeychainRead { readPending(attempt).map(WidgetKeychainRead.data) ?? .missing }
    func createPending(_ data: Data, attempt: String) -> Bool {
        guard readPending(attempt) == nil else { return false }
        return (try? data.write(to: path("pending-" + attempt), options: .atomic)) != nil
    }
    func updatePending(_ data: Data, attempt: String) -> Bool {
        guard readPending(attempt) != nil else { return false }
        return (try? data.write(to: path("pending-" + attempt), options: .atomic)) != nil
    }
    func deletePending(_ attempt: String) -> Bool {
        (try? FileManager.default.removeItem(at: path("pending-" + attempt))) != nil || readPending(attempt) == nil
    }
}

@main struct WidgetFencedProtocolHarness {
    static func main() throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let operation = CommandLine.arguments[2]
        let url = root.appendingPathComponent("catalog.sqlite")
        let keychain = FixtureKeychain(directory: root)
        let store = WidgetCredentialStore(catalogURL: url, keychain: keychain)
        let enrollment = WidgetFencedEnrollment(store: store)
        let reservationURL = root.appendingPathComponent("reservation.json")
        if operation == "prepare" || operation == "prepare-paired" {
            _ = ActivityCatalog(url: url)!
            let activation = ActivityCatalog.activate(url: url, endpoint: "https://example.test")!
            let fingerprint = operation == "prepare-paired" ? CommandLine.arguments[3] : nil
            let reservation = enrollment.prepare(expected: activation, pairingFingerprint: fingerprint)!
            try JSONEncoder().encode(reservation).write(to: reservationURL, options: .atomic)
            print("prepared")
            return
        }
        let reservation = try JSONDecoder().decode(ActivityCatalog.Enrollment.self, from: Data(contentsOf: reservationURL))
        if operation == "cancel" {
            precondition(enrollment.cancel(reservation).complete)
            print("cancelled")
        } else if operation == "recovery-plan" || operation == "recovery-conflict" {
            let status = try Data(contentsOf: root.appendingPathComponent("status.json"))
            let fingerprint = CommandLine.arguments[3]
            let decision: ActivityCatalog.EnrollmentRecoveryPlan?
            if operation == "recovery-plan" {
                decision = ActivityCatalog.planCancelledRecovery(url: url, expected: reservation, fingerprint: fingerprint, observedStatus: status)
            } else {
                let request = try Data(contentsOf: root.appendingPathComponent("recovery-request.json"))
                decision = ActivityCatalog.resolveRecoveryConflict(url: url, expected: reservation, fingerprint: fingerprint,
                                                                    failedRequest: request, observedStatus: status)
            }
            switch decision {
            case .revoke(let request): print(request.base64EncodedString())
            case .superseded: print("superseded")
            case nil: fatalError("recovery plan")
            }
        } else if operation == "recovery-receipt" {
            let request = try Data(contentsOf: root.appendingPathComponent("recovery-request.json"))
            let receipt = try Data(contentsOf: root.appendingPathComponent("receipt.json"))
            precondition(ActivityCatalog.recordRecoveryReceipt(url: url, expected: reservation, fingerprint: CommandLine.arguments[3],
                                                               request: request, receipt: receipt))
            print("recorded")
        } else if operation == "recovery-finish" {
            let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
            let result = ActivityCatalog.finishCancelledRecovery(url: url, expected: reservation, fingerprint: CommandLine.arguments[3],
                verify: { enrollment.recoveryEvidenceMatches($0) }, cleanup: { keychain.deletePending(attempt) && store.purgeCredential() })
            precondition(result.complete)
            print("recovered")
        } else if operation == "bind" {
            let status = try Data(contentsOf: root.appendingPathComponent("status.json"))
            guard case .ready(let request) = enrollment.bind(status, reservation: reservation) else { fatalError("bind") }
            print(request.base64EncodedString())
        } else if operation == "record" {
            let markerURL = url.appendingPathExtension("enrollment-pending")
            let journal = try JSONDecoder().decode(ActivityCatalog.EnrollmentJournal.self, from: Data(contentsOf: markerURL))
            let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
            let pending = try JSONSerialization.jsonObject(with: keychain.readPending(attempt)!) as! [String: Any]
            let receipt = try Data(contentsOf: root.appendingPathComponent("receipt.json"))
            guard ActivityCatalog.recordEnrollmentReceipt(url: url, expected: reservation, workspaceID: journal.workspaceID!,
                request: journal.request!, receipt: receipt, generation: pending["generation"] as! String) != nil else { fatalError("record") }
            print("recorded")
        } else if operation == "complete" || operation == "complete-saved" {
            let receipt = operation == "complete-saved" ? ActivityCatalog.savedEnrollmentReceipt(url: url, expected: reservation)!
                : try Data(contentsOf: root.appendingPathComponent("receipt.json"))
            guard case .installed = enrollment.complete(receipt, reservation: reservation) else { fatalError("complete") }
            print("installed")
        } else if operation == "verify" {
            let expected = CommandLine.arguments[3]
            guard let credential = store.capture()?.credential else { fatalError("capture") }
            let commitment = SHA256.hash(data: Data(credential.token.utf8)).map { String(format: "%02x", $0) }.joined()
            precondition(commitment == expected)
            print("verified")
        } else { fatalError("unknown operation") }
    }
}
