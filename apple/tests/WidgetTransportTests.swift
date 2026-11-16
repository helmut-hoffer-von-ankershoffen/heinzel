import Foundation

// Fixed fictional identities for this isolated fixture; never production configuration.
private let fixtureEndpoint = "https://example.test"
private let fixtureRedirectEndpoint = "https://redirect.invalid/widget"

private final class TransportKeychain: WidgetCredentialKeychain, @unchecked Sendable {
    var data: Data?
    var pending: [String: Data] = [:]
    func preflight() -> Bool { true }
    func read() -> Data? { data }
    func readStatus() -> WidgetKeychainRead { data.map(WidgetKeychainRead.data) ?? .missing }
    func write(_ data: Data) -> Bool { self.data = data; return true }
    func delete() -> Bool { data = nil; return true }
    func readPending(_ attempt: String) -> Data? { pending[attempt] }
    func createPending(_ data: Data, attempt: String) -> Bool { guard pending[attempt] == nil else { return false }; pending[attempt] = data; return true }
    func updatePending(_ data: Data, attempt: String) -> Bool { guard pending[attempt] != nil else { return false }; pending[attempt] = data; return true }
    func deletePending(_ attempt: String) -> Bool { pending.removeValue(forKey: attempt); return true }
}

private final class WidgetStubProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handle: ((URLRequest) -> (Int, String, Data))!
    override class func canInit(with _: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let (status, mime, body) = Self.handle(request)
        if status == -1 {
            client?.urlProtocol(self, didFailWithError: URLError(.secureConnectionFailed))
            return
        }
        if status == 302 {
            let target = URL(string: fixtureRedirectEndpoint)!
            let response = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: nil,
                                           headerFields: ["Location": target.absoluteString])!
            client?.urlProtocol(self, wasRedirectedTo: URLRequest(url: target), redirectResponse: response)
            return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
                                       headerFields: ["Content-Type": mime])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {
        // Responses are delivered synchronously in startLoading; no work remains to cancel.
    }
}

@main struct WidgetTransportTests {
    @MainActor static func main() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("widget-transport-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("catalog.sqlite")
        _ = ActivityCatalog(url: url)!
        let keychain = TransportKeychain()
        let store = WidgetCredentialStore(catalogURL: url, keychain: keychain)
        let workspace = String(repeating: "a", count: 64)
        let pending = ActivityCatalog.activate(url: url, endpoint: fixtureEndpoint)!
        func seed(_ credential: WidgetCredential, expected: ActivityCatalog.Activation) -> ActivityCatalog.Activation? {
            guard let data = try? JSONEncoder().encode(credential) else {
                preconditionFailure("Could not encode fixture credential")
            }
            return ActivityCatalog.installCredential(url: url, expected: expected, workspaceID: credential.workspaceID,
                                                     generation: credential.generation, install: { keychain.write(data) })
        }
        let credential = WidgetCredential(endpoint: pending.endpoint, workspaceID: workspace,
            generation: UUID().uuidString, token: "hzw_" + String(repeating: "x", count: 40),
            expiresAt: .now.addingTimeInterval(3600))
        let bound = seed(credential, expected: pending)!
        let client = WidgetSnapshotClient(store: store, protocolClasses: [WidgetStubProtocol.self])
        let generated = ISO8601DateFormatter().string(from: .now)
        let payload = Data("""
        {"version":1,"workspace_id":"\(workspace)","generated_at":"\(generated)","fresh_for_seconds":300,"awake":0,"waiting":0,"roster":[]}
        """.utf8)
        WidgetStubProtocol.handle = { request in
            precondition(request.url?.host == "example.test")
            precondition(request.value(forHTTPHeaderField: "Authorization") == "Bearer \(credential.token)")
            precondition(request.value(forHTTPHeaderField: "X-Hz-Workspace-Id") == workspace)
            return (200, "application/json", payload)
        }
        let success = await client.fetch()
        precondition(success != nil)
        WidgetStubProtocol.handle = { _ in (200, "text/plain", payload) }
        let wrongMIME = await client.fetch()
        precondition(wrongMIME == nil && ActivityCatalog.activation(url: url) == bound)
        WidgetStubProtocol.handle = { _ in (200, "application/json", Data(repeating: 65, count: 131_073)) }
        let tooLarge = await client.fetch()
        precondition(tooLarge == nil && ActivityCatalog.activation(url: url) == bound)
        WidgetStubProtocol.handle = { _ in (503, "application/json", Data()) }
        let unavailable = await client.fetch()
        precondition(unavailable == nil && ActivityCatalog.activation(url: url) == bound)
        WidgetStubProtocol.handle = { _ in (-1, "", Data()) }
        let tlsFailure = await client.fetch()
        precondition(tlsFailure == nil && ActivityCatalog.activation(url: url) == bound)
        WidgetStubProtocol.handle = { request in
            precondition(request.url?.host == "example.test")
            return (302, "", Data())
        }
        let redirect = await client.fetch()
        precondition(redirect == nil && ActivityCatalog.activation(url: url) == bound)
        WidgetStubProtocol.handle = { _ in
            let replacement = WidgetCredential(endpoint: credential.endpoint, workspaceID: workspace,
                generation: UUID().uuidString, token: credential.token, expiresAt: credential.expiresAt)
            precondition(seed(replacement, expected: bound) != nil)
            return (401, "application/json", Data())
        }
        let stale401 = await client.fetch()
        precondition(stale401 == nil)
        precondition(ActivityCatalog.activation(url: url)?.id != bound.id && keychain.data != nil)
        WidgetStubProtocol.handle = { _ in (401, "application/json", Data()) }
        let current401 = await client.fetch()
        precondition(current401 == nil && ActivityCatalog.activation(url: url) == nil && keychain.data == nil)
        let newPending = ActivityCatalog.activate(url: url, endpoint: pending.endpoint)!
        let mismatchCredential = WidgetCredential(endpoint: pending.endpoint, workspaceID: workspace,
            generation: UUID().uuidString, token: credential.token, expiresAt: credential.expiresAt)
        precondition(seed(mismatchCredential, expected: newPending) != nil)
        let mismatch = Data(String(decoding: payload, as: UTF8.self)
            .replacingOccurrences(of: workspace, with: String(repeating: "b", count: 64)).utf8)
        WidgetStubProtocol.handle = { _ in (200, "application/json", mismatch) }
        let wrongIdentity = await client.fetch()
        precondition(wrongIdentity == nil && ActivityCatalog.activation(url: url) == nil && keychain.data == nil)
        print("PASS: widget transport bounds and scoped authorization failure")
    }
}
