import AppKit
import Security
import SwiftUI
import WebKit
@testable import Heinzel

/// Allow the native menu guard to run without ordering a fixture window onto the user's desktop.
private final class FixtureWindow: NSWindow {
    override var isKeyWindow: Bool { true }
}

/// Forward navigation to the app's delegate. Trust only the ephemeral loopback certificate.
@MainActor private final class FixtureTrust: NSObject, WKNavigationDelegate {
    let navigation: Navigation
    let port: Int
    let certificate: Data

    init(_ navigation: Navigation, port: Int, certificate: Data) {
        self.navigation = navigation
        self.port = port
        self.certificate = certificate
    }

    nonisolated override func responds(to selector: Selector!) -> Bool {
        super.responds(to: selector) || MainActor.assumeIsolated { navigation.responds(to: selector) }
    }

    nonisolated override func forwardingTarget(for selector: Selector!) -> Any? { navigation }

    func webView(_ view: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping @MainActor @Sendable (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              space.host == "127.0.0.1", space.port == port, let trust = space.serverTrust,
              let leaf = (SecTrustCopyCertificateChain(trust) as? [SecCertificate])?.first,
              SecCertificateCopyData(leaf) as Data == certificate,
              SecTrustSetAnchorCertificates(trust, [leaf] as CFArray) == errSecSuccess,
              SecTrustSetAnchorCertificatesOnly(trust, true) == errSecSuccess,
              SecTrustEvaluateWithError(trust, nil) else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}

@main private struct WorkspaceNavigationTests {
    @MainActor static func wait(_ message: String, until check: () async throws -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(10)
        while try await !check() {
            precondition(ContinuousClock.now < deadline, message)
            try await Task.sleep(for: .milliseconds(20))
        }
    }

    @MainActor static func main() async throws {
        let args = CommandLine.arguments
        let token = ProcessInfo.processInfo.environment["FIXTURE_SESSION"]!
        let endpoint = Endpoint(args[1] + "/#token=" + token, loopback: args[1].hasPrefix("http://"))!
        let marker = URL(fileURLWithPath: args[2])
        let certificate = try Data(contentsOf: URL(fileURLWithPath: args[3]))
        var view: WKWebView?
        var established = false
        var opened = 0
        var issues: [WorkspaceProblem] = []
        let host = NSHostingView(rootView: WorkspaceView(endpoint: endpoint, isObscured: false,
            registerPage: { view = $0 }, requiresVerification: { !established },
            isMenuWorkspaceCurrent: { true }, openWorkspaceChooser: { opened += 1; return true },
            selectedWorkspaceName: { "Studio" }, ready: { established = true }, reportError: { issues.append($0) }))
        let window = FixtureWindow(contentRect: NSRect(x: 0, y: 0, width: 580, height: 440),
                                   styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let tab = view!
        let trust = FixtureTrust(tab.navigationDelegate as! Navigation, port: endpoint.origin.port!, certificate: certificate)
        if endpoint.origin.scheme == "https" { tab.navigationDelegate = trust }
        defer { withExtendedLifetime((window, trust)) {} }
        try await wait("Workspace failed to authenticate") { established }
        _ = try await tab.evaluateJavaScript("document.querySelector('textarea').value = 'Unsent'; history.replaceState(null, '', '/draft');")
        for status in [503, 404, 410, 401, 403] {
            _ = try await tab.evaluateJavaScript("location.assign('/resource/\(status)')")
            try await wait("Resource \(status) did not prompt authenticated verification") {
                (try? String(contentsOf: marker, encoding: .utf8)) == String(status)
            }
            let draft = try await tab.evaluateJavaScript("location.pathname + ' ' + document.querySelector('textarea').value") as? String
            precondition(issues.isEmpty && draft == "/draft Unsent" && view === tab, "Healthy workspace lost on resource \(status)")
        }

        for modern in [false, true] {
            _ = try await tab.evaluateJavaScript("window.modern = \(modern); document.querySelector('#trigger').click()")
            let item = "document.querySelector('[role=menu] button')"
            try await wait("Incorrect Profile workspace item") {
                try await tab.evaluateJavaScript("\(item)?.textContent === '\(modern ? "Current menu" : "Workspaces · Studio")'") as? Bool == true
            }
            let count = try await tab.evaluateJavaScript("document.querySelectorAll('[role=menu] button').length") as? Int
            precondition(count == 1, "Duplicate workspace items")
            _ = try await tab.evaluateJavaScript("\(item).click()")
            try await wait("Chooser action did not reach native host") { opened == (modern ? 2 : 1) }
            try await wait("Menu stayed open") { try await tab.evaluateJavaScript("document.querySelector('[role=menu]') === null") as? Bool == true }
        }
        // A 503 resource now coincides with lifecycle rejecting the credential. Only the latter reaches recovery.
        _ = try await tab.evaluateJavaScript("location.assign('/denied')")
        try await wait("Confirmed loss was not reported") { !issues.isEmpty }
        precondition(issues == [.denied], "Reported resource status instead of authenticated session loss")
    }
}
