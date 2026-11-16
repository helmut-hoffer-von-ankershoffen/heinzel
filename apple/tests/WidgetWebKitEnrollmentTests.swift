import AppKit
import CryptoKit
import Foundation
import Security
import WebKit

/// Synthetic in-memory fixture; this is not signed Keychain acceptance.
private final class FixtureKeychain: WidgetCredentialKeychain {
    var active: Data?
    var pending: [String: Data] = [:]
    func preflight() -> Bool { true }
    func read() -> Data? { active }
    func readStatus() -> WidgetKeychainRead { active.map(WidgetKeychainRead.data) ?? .missing }
    func write(_ data: Data) -> Bool { active = data; return true }
    func delete() -> Bool { active = nil; return true }
    func readPending(_ attempt: String) -> Data? { pending[attempt] }
    func readPendingStatus(_ attempt: String) -> WidgetKeychainRead { pending[attempt].map(WidgetKeychainRead.data) ?? .missing }
    func createPending(_ data: Data, attempt: String) -> Bool {
        guard pending[attempt] == nil else { return false }; pending[attempt] = data; return true
    }
    func updatePending(_ data: Data, attempt: String) -> Bool {
        guard pending[attempt] != nil else { return false }; pending[attempt] = data; return true
    }
    func deletePending(_ attempt: String) -> Bool { pending.removeValue(forKey: attempt); return true }
}

private struct FixtureInput: Decodable {
    let origin: String
    let certificateSHA256: String
    let directory: String
}

@MainActor private final class Navigation: NSObject, WKNavigationDelegate {
    let certificateSHA256: String
    let port: Int
    weak var view: WKWebView?
    var loaded: CheckedContinuation<Void, Error>?
    private func diagnostic(_ message: String) { FileHandle.standardError.write(Data((message + "\n").utf8)) }
    init(certificateSHA256: String, port: Int) { self.certificateSHA256 = certificateSHA256; self.port = port }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { loaded?.resume(); loaded = nil }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let value = error as NSError
        let underlying = value.userInfo[NSUnderlyingErrorKey] as? NSError
        diagnostic("navigation-error domain=\(value.domain) code=\(value.code) underlying=\(underlying?.domain ?? "none")/\(underlying?.code ?? 0)")
        loaded?.resume(throwing: error); loaded = nil
    }
    func webView(_ webView: WKWebView, respondTo challenge: URLAuthenticationChallenge) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        diagnostic("challenge view=\(webView === view) method=\(challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust) host=\(challenge.protectionSpace.host == "127.0.0.1") port=\(challenge.protectionSpace.port == port)")
        guard webView === view, challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              challenge.protectionSpace.host == "127.0.0.1", challenge.protectionSpace.port == port,
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first else {
            return (.cancelAuthenticationChallenge, nil)
        }
        let digest = SHA256.hash(data: SecCertificateCopyData(leaf) as Data).map { String(format: "%02x", $0) }.joined()
        diagnostic("certificate-pin=\(digest == certificateSHA256)")
        var trustError: CFError?
        guard digest == certificateSHA256, SecTrustSetAnchorCertificates(trust, [leaf] as CFArray) == errSecSuccess,
              SecTrustSetAnchorCertificatesOnly(trust, true) == errSecSuccess,
              SecTrustEvaluateWithError(trust, &trustError) else {
            diagnostic("trust-error domain=\(trustError.map { CFErrorGetDomain($0) as String } ?? "none") code=\(trustError.map(CFErrorGetCode) ?? 0)")
            return (.cancelAuthenticationChallenge, nil)
        }
        return (.useCredential, URLCredential(trust: trust))
    }
}

@main struct WidgetWebKitEnrollmentTests {
    enum FixtureError: String, Error { case origin, port, navigation, fingerprint, catalog, first, cancel, recover }
    @MainActor static func main() async {
        do { try await run() }
        catch let error as FixtureError { FileHandle.standardError.write(Data("fixture failed: \(error.rawValue)\n".utf8)); exit(1) }
        catch { FileHandle.standardError.write(Data("fixture failed: other\n".utf8)); exit(1) }
    }

    @MainActor static func run() async throws {
        let input = try JSONDecoder().decode(FixtureInput.self, from: FileHandle.standardInput.readDataToEndOfFile())
        guard let endpoint = URL(string: input.origin), endpoint.scheme == "https", endpoint.host == "127.0.0.1" else { throw FixtureError.origin }
        _ = NSApplication.shared
        guard let port = endpoint.port else { throw FixtureError.port }
        let navigation = Navigation(certificateSHA256: input.certificateSHA256, port: port)
        let config = WKWebViewConfiguration(); config.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: config); navigation.view = view; view.navigationDelegate = navigation
        do {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                navigation.loaded = continuation
                view.load(URLRequest(url: endpoint))
            }
        } catch { throw FixtureError.navigation }
        let isCurrent = { view.url?.host == endpoint.host && view.url?.port == endpoint.port }
        guard let fingerprint = await WidgetEnrollmentTransport.pairedDeviceFingerprint(in: view, endpoint: endpoint, isCurrent: isCurrent) else { throw FixtureError.fingerprint }
        _ = try await view.callAsyncJavaScript("window.fixtureOriginal = localStorage.getItem('hz.device'); localStorage.setItem('hz.device', 'hzd_' + 'x'.repeat(43)); return true;",
            arguments: [:], in: nil, contentWorld: .defaultClient)
        let changedPair = await WidgetEnrollmentTransport.exchange(.negotiate, payload: nil, in: view, endpoint: endpoint,
                                                                    fingerprint: fingerprint, isCurrent: isCurrent)
        guard case .unavailable = changedPair else { throw FixtureError.fingerprint }
        _ = try await view.callAsyncJavaScript("localStorage.setItem('hz.device', window.fixtureOriginal); delete window.fixtureOriginal; return true;",
            arguments: [:], in: nil, contentWorld: .defaultClient)
        let staleView = await WidgetEnrollmentTransport.exchange(.negotiate, payload: nil, in: view, endpoint: endpoint,
                                                                  fingerprint: fingerprint, isCurrent: { false })
        guard case .unavailable = staleView else { throw FixtureError.fingerprint }
        let root = URL(fileURLWithPath: input.directory, isDirectory: true)
        let catalogURL = root.appendingPathComponent("catalog.sqlite")
        guard ActivityCatalog(url: catalogURL) != nil,
              let activation = ActivityCatalog.activate(url: catalogURL, endpoint: endpoint.absoluteString) else { throw FixtureError.catalog }
        let keychain = FixtureKeychain()
        let store = WidgetCredentialStore(catalogURL: catalogURL, keychain: keychain)
        let enrollment = WidgetFencedEnrollment(store: store)
        let coordinator = WidgetEnrollmentCoordinator(enrollment: enrollment)
        let first = await coordinator.begin(expected: activation, in: view, endpoint: endpoint, fingerprint: fingerprint, isCurrent: isCurrent)
        guard case .pending = first, let reservation = ActivityCatalog.pendingEnrollment(url: catalogURL) else { throw FixtureError.first }
        guard enrollment.cancel(reservation).complete else { throw FixtureError.cancel }
        let result = await coordinator.recover(reservation, in: view, endpoint: endpoint, fingerprint: fingerprint, isCurrent: isCurrent)
        guard case .identityMismatch(let clear) = result, clear.complete,
              ActivityCatalog.pendingEnrollment(url: catalogURL) == nil, keychain.active == nil, keychain.pending.isEmpty else { throw FixtureError.recover }
        print("PASS: real WKWebView lost-reply cancellation and revoke")
    }
}
