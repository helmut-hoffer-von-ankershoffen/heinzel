// Standalone collaborators: compile this file with NativePrompt.swift only.
import Foundation

struct WidgetHeinzel {
    let id: String
    let name: String
    var widgetHeinzel: Self { self }
}
struct ActivityWidgetData {
    let workspaceScope: String
    let generation: String? = "fixture"
    let roster = [WidgetHeinzel(id: "example", name: "Example")]
    static func search(scope: String, generation: String, query: String, offset: Int) -> [WidgetHeinzel] {
        [WidgetHeinzel(id: "example", name: "Example")]
    }
    static func find(scope: String, generation: String, id: String) -> WidgetHeinzel? { nil }
}
enum NativeRoute {
    static func isSessionID(_ id: String) -> Bool { id == "example" }
}
@MainActor final class Navigation {
    func sendPrompt(_ request: NativePromptRequest) async -> NativePromptOutcome {
        preconditionFailure("The probe must use its injected transport")
    }
}

@MainActor final class Transport {
    var requests: [NativePromptRequest] = []
    var replies: [CheckedContinuation<NativePromptOutcome, Never>] = []
    func send(_ request: NativePromptRequest) async -> NativePromptOutcome {
        requests.append(request)
        return await withCheckedContinuation { replies.append($0) }
    }
    func finish(_ index: Int, _ outcome: NativePromptOutcome) { replies[index].resume(returning: outcome) }
}

@main struct NativePromptCoordinationTests {
    @MainActor static func until(_ condition: () -> Bool) async {
        for _ in 0..<10000 {
            if condition() { return }
            await Task.yield()
        }
        preconditionFailure("Expected composer transition did not occur")
    }
    @MainActor static func main() async throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        precondition(!FileManager.default.fileExists(atPath: root.path), "Use a fresh private directory")
        let transport = Transport()
        func store() -> NativePromptDrafts {
            NativePromptDrafts { NativePromptComposer(storageRoot: root, deliver: transport.send) }
        }
        let drafts = store()
        let a = String(repeating: "a", count: 64), b = String(repeating: "b", count: 64)
        let window = drafts.composer(for: a), inline = drafts.composer(for: a)
        precondition(window === inline, "Window and inline Quick Chat must share state")
        window.prepareCatalog(scope: a, snapshot: ActivityWidgetData(workspaceScope: a))
        window.recipientID = "example"
        window.text = "Workspace A draft"
        precondition(inline.text == window.text)
        window.startSend()
        inline.startSend()
        await until { transport.requests.count == 1 }
        let identity = transport.requests[0].requestID
        precondition(inline.sending && !inline.canSend)
        inline.discardPending()
        precondition(inline.pending?.requestID == identity, "In-flight request cannot be discarded")
        transport.finish(0, .unconfirmed)
        await until { !inline.sending }
        precondition(inline.pending?.requestID == identity && !window.accepted)
        inline.startSend()
        await until { transport.requests.count == 2 }
        precondition(transport.requests[1].requestID == identity, "Retry must retain idempotency key")
        // Another workspace remains usable while A's retry is suspended.
        let independent = drafts.composer(for: b)
        independent.prepareCatalog(scope: b, snapshot: ActivityWidgetData(workspaceScope: b))
        independent.recipientID = "example"
        independent.text = "Workspace B draft"
        independent.startSend()
        await until { transport.requests.count == 3 }
        transport.finish(2, .accepted)
        await until { independent.accepted }
        independent.newPrompt()
        independent.text = "New B draft"
        transport.finish(1, .accepted)
        await until { window.accepted }
        precondition(independent.text == "New B draft" && !independent.accepted && independent.pending == nil,
                     "Late A completion must not mutate the current B draft")
        precondition(inline.accepted && inline.text.isEmpty)
        window.newPrompt()
        inline.recipientID = "example"
        inline.text = "Persist me"
        inline.startSend()
        await until { transport.requests.count == 4 }
        transport.finish(3, .unconfirmed)
        await until { !inline.sending }
        let restored = store().composer(for: a)
        restored.prepareCatalog(scope: a, snapshot: ActivityWidgetData(workspaceScope: a))
        precondition(restored.pending == inline.pending && restored.text == "Persist me", "Restart restores same request")
        // Use the restored store as a new process after the original transport has finished.
        restored.startSend()
        await until { transport.requests.count == 5 }
        precondition(transport.requests[4] == transport.requests[3])
        transport.finish(4, .rejected)
        await until { !restored.sending }
        precondition(restored.pending == nil)
        restored.recipientID = "example"
        restored.text = "Discard me"
        restored.startSend()
        await until { transport.requests.count == 6 }
        transport.finish(5, .unconfirmed)
        await until { !restored.sending }
        restored.discardPending()
        precondition(restored.pending == nil && restored.text.isEmpty)
        let clean = store().composer(for: a)
        clean.prepareCatalog(scope: a, snapshot: ActivityWidgetData(workspaceScope: a))
        precondition(clean.pending == nil, "Discard must survive restart")
        // Recover multiple pending files left by older independent composers.
        let directory = root.appendingPathComponent("HeinzelPromptDrafts").appendingPathComponent(a)
        for id in ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"] {
            let request = NativePromptRequest(workspaceScope: a, recipientID: "example", recipientName: "Example",
                                              text: id, requestID: id)
            try JSONEncoder().encode(request).write(to: directory.appendingPathComponent(id + ".json"))
        }
        let legacy = store().composer(for: a)
        legacy.prepareCatalog(scope: a, snapshot: ActivityWidgetData(workspaceScope: a))
        precondition(legacy.pending?.requestID.hasSuffix("001") == true)
        legacy.discardPending()
        precondition(legacy.pending?.requestID.hasSuffix("002") == true, "Next unresolved prompt must become visible")
        legacy.discardPending()
        precondition(legacy.pending == nil)
        print("PASS: shared surfaces, independent workspaces, retry, restart, discard, rejection, stale completion")
    }
}
