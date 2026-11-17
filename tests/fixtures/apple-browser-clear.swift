import Foundation
import WebKit

@MainActor
final class RejectWorkspaceNavigation: NSObject, WKNavigationDelegate {
    func webView(_ _: WKWebView, decidePolicyFor _: WKNavigationAction) async -> WKNavigationActionPolicy {
        .cancel
    }

    func webView(_ _: WKWebView, decidePolicyFor _: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        .cancel
    }
}

@main
struct BrowserClearProbe {
    @MainActor static func main() async {
        let endpoint = Endpoint("https://workspace.example.com")!
        func target(_ value: String, revision: Int = 1, ready: Bool = true) -> IntegratedBrowser.ExternalTarget? {
            IntegratedBrowser.externalTarget(url: URL(string: value), revision: revision, ready: ready, endpoint: endpoint)
        }
        let current = target("https://example.com/page")!
        precondition(target("https://example.com/page") == current)
        precondition(target("https://example.com/page", revision: 2) != current)
        precondition(target("https://example.com/other") != current)
        precondition(target("https://example.com/page", ready: false) == nil)
        for value in ["https://workspace.example.com/a", "https://localhost/a", "https://10.0.0.1/a",
                      "http://example.com/a", "https://user:pass@example.com/a", "https://example.com:8443/a"] {
            precondition(target(value) == nil)
        }
        let otherPort = Endpoint("https://workspace.example.com:8443")!
        precondition(!IntegratedBrowser.permits(URL(string: "https://workspace.example.com/")!, excluding: otherPort))
        await tabCommands(endpoint)
        let dns = BoundedGuestDNS()
        let unblock = DispatchSemaphore(value: 0)
        let delayed = await dns.resolve("example.com", timeout: .milliseconds(30)) { _ in
            unblock.wait()
            return true
        }
        precondition(!delayed && dns.busy)
        let concurrent = await dns.resolve("example.com") { _ in fatalError("lookup overlapped") }
        precondition(!concurrent)
        unblock.signal()
        for _ in 0..<100 where dns.busy { try? await Task.sleep(for: .milliseconds(2)) }
        precondition(!dns.busy)
        let fresh = await dns.resolve("example.com") { _ in true }
        precondition(fresh)
        let clear = GuestDataClear<String>()
        var guest = "guest"
        let workspace = "workspace-token"
        var started = false
        var resume: CheckedContinuation<Bool, Never>?
        let first = Task { @MainActor in
            await clear.run {
                let previous = guest
                guest = "detached"
                return previous
            } erase: { store in
                precondition(store == "guest" && guest == "detached")
                return await withCheckedContinuation { resume = $0; started = true }
            }
        }
        while !started { await Task.yield() }
        precondition(clear.clearing)
        let duplicate = await clear.run { "wrong" } erase: { _ in fatalError("concurrent wipe") }
        precondition(!duplicate && guest == "detached" && workspace == "workspace-token")
        resume?.resume(returning: false)
        let failed = await first.value
        precondition(!failed && !clear.clearing)
        let retried = await clear.run { nil } erase: { $0 == "guest" }
        precondition(retried && !clear.clearing && workspace == "workspace-token")
        let empty = await clear.run { nil } erase: { _ in fatalError("empty store") }
        precondition(empty && !clear.clearing)
    }

    @MainActor
    static func tabCommands(_ endpoint: Endpoint) async {
        let browser = IntegratedBrowser()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let workspace = WKWebView(frame: .zero, configuration: configuration)
        let rejectNavigation = RejectWorkspaceNavigation()
        workspace.navigationDelegate = rejectNavigation
        let store = workspace.configuration.websiteDataStore
        let workspaceSubviews = workspace.subviews.map(ObjectIdentifier.init)
        func command(_ action: String, _ tab: String? = nil) async -> [String: Any] {
            var request: [String: Any] = ["action": action]
            if let tab { request["tab"] = tab }
            return await browser.handle(request, in: workspace, excluding: endpoint)
        }
        func ids(_ state: [String: Any]) -> [String] {
            let tabs = state["tabs"] as! [[String: Any]]
            return tabs.map { $0["id"] as! String }
        }
        let empty = await command("tabs")
        precondition(ids(empty).isEmpty)
        let first = await command("new-tab")
        let firstID = ids(first)[0]
        let second = await command("new-tab")
        let secondID = ids(second)[1]
        precondition(firstID != secondID && second["selected"] as? String == secondID)
        let missing = await command("select-tab", "unknown")
        precondition(missing["selected"] as? String == secondID)
        let selected = await command("select-tab", firstID)
        precondition(selected["selected"] as? String == firstID)
        let closed = await command("close-tab", firstID)
        precondition(ids(closed) == [secondID] && closed["selected"] as? String == secondID)
        for _ in 0..<8 { _ = await command("new-tab") }
        let bounded = await command("tabs")
        precondition(ids(bounded).count == 8)
        let cleared = await command("clear")
        precondition(ids(cleared).isEmpty && cleared["selected"] == nil && cleared["notice"] == nil)
        precondition(workspace.navigationDelegate === rejectNavigation)
        precondition(workspace.configuration.websiteDataStore === store && workspace.subviews.map(ObjectIdentifier.init) == workspaceSubviews)
    }
}
