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
        if operation == "prepare" {
            _ = ActivityCatalog(url: url)!
            let activation = ActivityCatalog.activate(url: url, endpoint: "https://example.test")!
            let reservation = enrollment.prepare(expected: activation)!
            try JSONEncoder().encode(reservation).write(to: reservationURL, options: .atomic)
            print("prepared")
            return
        }
        let reservation = try JSONDecoder().decode(ActivityCatalog.Enrollment.self, from: Data(contentsOf: reservationURL))
        if operation == "bind" {
            let status = try Data(contentsOf: root.appendingPathComponent("status.json"))
            guard case .ready(let request) = enrollment.bind(status, reservation: reservation) else { fatalError("bind") }
            print(request.base64EncodedString())
        } else if operation == "complete" {
            let receipt = try Data(contentsOf: root.appendingPathComponent("receipt.json"))
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
