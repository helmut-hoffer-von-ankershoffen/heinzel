import CoreFoundation
import SwiftUI
import WebKit
#if DEBUG
import OSLog
#endif

struct HeinzelChatRequest: Equatable {
    let id: String
    let workspaceScope: String
    let request = UUID()
}

struct NativePromptRequest: Codable, Equatable {
    let workspaceScope: String
    let recipientID: String
    let recipientName: String
    let text: String
    let requestID: String

    var isValid: Bool {
        workspaceScope.wholeMatch(of: /[a-f0-9]{64}/) != nil && NativeRoute.isSessionID(recipientID) &&
        UUID(uuidString: requestID) != nil && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        text.unicodeScalars.count <= 32_000
    }
}

enum NativePromptOutcome: Equatable {
    case accepted
    case unavailable
    case signIn
    case unconfirmed
    case rejected
}

/// Draft text stays in the app's private container, never in widget storage or a URL.
@MainActor @Observable
final class NativePromptComposer {
    var recipientID = ""
    var text = ""
    var roster: [WidgetHeinzel] = []
    var search = ""
    var page = 0
    private(set) var pageCount = 0
    private(set) var scope: String?
    private(set) var catalogAvailable = false
    private(set) var pending: NativePromptRequest?
    private(set) var sending = false
    private(set) var accepted = false
    private(set) var message: String?
    @ObservationIgnored weak var navigation: Navigation?
    @ObservationIgnored private var sendTask: Task<Void, Never>?

    @ObservationIgnored private let storageRoot: URL?
    @ObservationIgnored private let deliver: (@MainActor (NativePromptRequest) async -> NativePromptOutcome)?

    init(storageRoot: URL? = nil,
         deliver: (@MainActor (NativePromptRequest) async -> NativePromptOutcome)? = nil) {
        self.storageRoot = storageRoot
        self.deliver = deliver
    }

    var canSend: Bool {
        scope != nil && catalogAvailable && !sending && !accepted &&
        (pending != nil || roster.contains { $0.id == recipientID }) &&
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && text.unicodeScalars.count <= 32_000
    }

    func prepare(scope newScope: String?, roster: [WidgetHeinzel]) {
        guard !sending else { return }
        self.roster = roster
        guard scope != newScope else { return }
        scope = newScope
        pending = nil
        recipientID = ""
        text = ""
        message = nil
        accepted = false
        guard let newScope else { return }
        do {
            let directory = try draftDirectory(scope: newScope)
            let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            guard let url = files.filter({ $0.pathExtension == "json" })
                .sorted(by: { $0.lastPathComponent < $1.lastPathComponent }).first else { return }
            let saved = try JSONDecoder().decode(NativePromptRequest.self, from: Data(contentsOf: url))
            guard saved.isValid, saved.workspaceScope == newScope,
                  url.deletingPathExtension().lastPathComponent == saved.requestID else { throw CocoaError(.fileReadCorruptFile) }
            pending = saved
            recipientID = saved.recipientID
            text = saved.text
            message = "Delivery wasn’t confirmed. Retry this saved prompt safely, or check the chat."
        } catch {
            message = "The saved prompt could not be read. Open the chat to check its delivery before writing another."
            // Keep a corrupt pending file from being silently replaced by a new request.
            scope = nil
        }
    }

    func prepareCatalog(scope newScope: String?, snapshot: ActivityWidgetData?) {
        guard !sending else { return }
        if scope != newScope {
            catalogAvailable = false
            prepare(scope: newScope, roster: [])
        }
        guard let newScope, scope == newScope, let snapshot, snapshot.workspaceScope == newScope,
              let generation = snapshot.generation else {
            catalogAvailable = false
            return
        }
        var entries = ActivityWidgetData.search(scope: newScope, generation: generation, query: search, offset: page * 50)
        pageCount = entries.count
        if !recipientID.isEmpty, !entries.contains(where: { $0.id == recipientID }),
           let selected = ActivityWidgetData.find(scope: newScope, generation: generation, id: recipientID) { entries.append(selected) }
        let items = entries.map { entry in
            snapshot.roster.first(where: { $0.id == entry.id }) ?? entry.widgetHeinzel
        }
        prepare(scope: newScope, roster: items)
        catalogAvailable = true
    }

    func startSend() {
        guard sendTask == nil, canSend else { return }
        // The composer outlives the sheet, so closing it does not cancel an accepted turn.
        sendTask = Task { [self] in
            await send()
            sendTask = nil
        }
    }

    private func send() async {
        guard !sending, !accepted else { return }
        guard canSend, let scope, navigation != nil || deliver != nil else {
            message = "Open the workspace and sign in before sending. Your prompt is still here."
            return
        }
        let request = pending ?? NativePromptRequest(
            workspaceScope: scope, recipientID: recipientID,
            recipientName: roster.first { $0.id == recipientID }?.name ?? "Heinzel",
            text: text, requestID: UUID().uuidString
        )
        guard request.isValid else { return }
        // Retain the identity even if an atomic write succeeds but a later file operation fails.
        pending = request
        do {
            let url = try draftDirectory(scope: scope).appendingPathComponent(request.requestID + ".json")
            let data = try JSONEncoder().encode(request)
            #if os(iOS)
            try data.write(to: url, options: [.atomic, .completeFileProtection])
            #else
            try data.write(to: url, options: .atomic)
            #endif
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch {
            message = "Your prompt couldn’t be saved safely. Nothing was sent. Try again."
            return
        }
        sending = true
        message = nil
        let outcome: NativePromptOutcome
        if let deliver { outcome = await deliver(request) }
        else if let navigation { outcome = await navigation.sendPrompt(request) }
        else { outcome = .unavailable }
        // A delayed transport result must only finish the request it started.
        guard self.scope == request.workspaceScope, pending == request else { return }
        sending = false
        switch outcome {
        case .accepted:
            accepted = true
            text = ""
            message = "Response received. Open the chat to read it."
            if let directory = try? draftDirectory(scope: scope) {
                try? FileManager.default.removeItem(at: directory.appendingPathComponent(request.requestID + ".json"))
            }
        case .unavailable:
            message = "The workspace isn’t ready. Open it, then retry this saved prompt."
        case .signIn:
            message = "Sign in to the workspace, then retry this saved prompt."
        case .unconfirmed:
            message = "The response wasn’t confirmed. Retry this saved prompt or open the chat to check its status."
        case .rejected:
            if removePendingFile() {
                reloadPending()
                message = "The host rejected this prompt. Check the recipient and text, then send again."
            }
        }
    }

    func discardPending() {
        guard !sending, removePendingFile() else { return }
        reloadPending()
    }

    private func reloadPending() {
        let previousScope = scope
        scope = nil
        prepare(scope: previousScope, roster: roster)
    }

    @discardableResult
    private func removePendingFile() -> Bool {
        guard let pending else { return true }
        do {
            let url = try draftDirectory(scope: pending.workspaceScope).appendingPathComponent(pending.requestID + ".json")
            if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
            return true
        } catch {
            message = "The saved prompt couldn’t be removed. It is still available to retry."
            return false
        }
    }

    func newPrompt() {
        guard accepted, !sending else { return }
        let previousScope = scope
        scope = nil
        accepted = false
        pending = nil
        text = ""
        message = nil
        prepare(scope: previousScope, roster: roster)
    }

    private func draftDirectory(scope: String) throws -> URL {
        guard scope.wholeMatch(of: /[a-f0-9]{64}/) != nil else { throw CocoaError(.fileWriteInvalidFileName) }
        var directory = try (storageRoot ?? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                    appropriateFor: nil, create: true))
            .appendingPathComponent("HeinzelPromptDrafts", isDirectory: true)
            .appendingPathComponent(scope, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try directory.setResourceValues(values)
        return directory
    }
}

/// Both surfaces share one composer per workspace for the app lifetime.
@MainActor
final class NativePromptDrafts {
    static let shared = NativePromptDrafts()
    private let makeComposer: () -> NativePromptComposer

    init(makeComposer: @escaping () -> NativePromptComposer = { NativePromptComposer() }) {
        self.makeComposer = makeComposer
    }
    private var drafts: [String: NativePromptComposer] = [:]

    func composer(for scope: String) -> NativePromptComposer {
        if let draft = drafts[scope] { return draft }
        let draft = makeComposer()
        drafts[scope] = draft
        return draft
    }
}
