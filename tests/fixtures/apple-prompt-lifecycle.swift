import Foundation
import Observation

struct WidgetHeinzel { let id: String; let name: String }
struct CatalogEntry {
    let id: String
    var widgetHeinzel: WidgetHeinzel { WidgetHeinzel(id: id, name: id) }
}
struct ActivityWidgetData {
    let workspaceScope: String?
    let generation: String?
    let roster: [WidgetHeinzel]
    static func search(scope: String, generation: String, query: String, offset: Int) -> [CatalogEntry] {
        [CatalogEntry(id: "heinzel")]
    }
    static func find(scope: String, generation: String, id: String) -> CatalogEntry? { CatalogEntry(id: id) }
}
enum NativeRoute {
    static func isSessionID(_ value: String) -> Bool { !value.isEmpty }
}
@MainActor final class Navigation {
    var calls: [NativePromptRequest] = []
    var continuation: CheckedContinuation<NativePromptOutcome, Never>?
    func sendPrompt(_ request: NativePromptRequest) async -> NativePromptOutcome {
        calls.append(request)
        return await withCheckedContinuation { continuation = $0 }
    }
}

// PROMPT_COMPOSER

@main struct Probe {
    @MainActor static func main() async throws {
        let scope = String(repeating: "a", count: 64)
        let roster = [WidgetHeinzel(id: "heinzel", name: "Heinzel")]
        let navigation = Navigation()
        let composer = NativePromptComposer()
        composer.navigation = navigation
        let snapshot = ActivityWidgetData(workspaceScope: scope, generation: "g1", roster: roster)
        composer.prepareCatalog(scope: scope, snapshot: snapshot)
        precondition(composer.roster.first?.id == "heinzel" && composer.pageCount == 1)
        composer.recipientID = "heinzel"
        composer.text = "Hello"
        precondition(composer.canSend)
        composer.prepareCatalog(scope: scope, snapshot: nil)
        precondition(composer.scope == scope && composer.text == "Hello" && composer.recipientID == "heinzel" && !composer.canSend)
        composer.prepareCatalog(scope: scope, snapshot: snapshot)
        precondition(composer.text == "Hello" && composer.recipientID == "heinzel" && composer.canSend)
        let otherScope = String(repeating: "b", count: 64)
        composer.prepareCatalog(scope: scope, snapshot: ActivityWidgetData(workspaceScope: otherScope, generation: "g2", roster: []))
        precondition(composer.text == "Hello" && composer.recipientID == "heinzel" && !composer.canSend)
        composer.prepareCatalog(scope: otherScope, snapshot: nil)
        precondition(composer.scope == otherScope && composer.text.isEmpty && composer.recipientID.isEmpty && !composer.canSend)
        composer.prepareCatalog(scope: scope, snapshot: snapshot)
        composer.recipientID = "heinzel"
        composer.text = "Hello"
        composer.startSend()
        while navigation.continuation == nil { await Task.yield() }
        precondition(composer.sending && !composer.canSend && composer.pending != nil)
        let request = composer.pending!
        composer.startSend()
        composer.discardPending()
        precondition(navigation.calls == [request] && composer.pending == request)
        // Closing and reopening a sheet does not own the task; a fresh composer also finds the saved identity.
        let reopened = NativePromptComposer()
        reopened.prepare(scope: scope, roster: roster)
        precondition(reopened.pending == request && reopened.text == "Hello")
        composer.prepare(scope: String(repeating: "b", count: 64), roster: roster)
        precondition(composer.pending == request && composer.scope == scope)
        navigation.continuation!.resume(returning: .unconfirmed)
        while composer.sending { await Task.yield() }
        precondition(composer.pending == request && composer.canSend)
        composer.prepareCatalog(scope: scope, snapshot: nil)
        precondition(composer.pending == request && !composer.canSend)
        composer.prepareCatalog(scope: scope, snapshot: snapshot)
        precondition(composer.pending == request && composer.canSend)
        composer.startSend()
        while navigation.calls.count < 2 { await Task.yield() }
        precondition(navigation.calls == [request, request])
        navigation.continuation!.resume(returning: .accepted)
        while composer.sending { await Task.yield() }
        precondition(composer.accepted && composer.pending == request)
        let recovered = NativePromptComposer()
        recovered.prepare(scope: scope, roster: roster)
        precondition(recovered.pending == nil)
    }
}
