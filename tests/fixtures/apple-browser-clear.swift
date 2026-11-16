import Foundation

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
}
