#if os(iOS)
import AppIntents
import Observation

@MainActor @Observable
final class AppShortcutRouter {
    static let shared = AppShortcutRouter()
    var pending: NativeRoute?

    private init() {}
}

struct OpenWorkspaceIntent: AppIntent {
    static let title: LocalizedStringResource = "Open Workspace"
    static let openAppWhenRun = true
    @available(iOS 26.0, *)
    static var supportedModes: IntentModes { .foreground(.immediate) }

    @MainActor
    func perform() async throws -> some IntentResult {
        AppShortcutRouter.shared.pending = .workspace
        return .result()
    }
}

struct OpenConnectionHelpIntent: AppIntent {
    static let title: LocalizedStringResource = "Open Connection Help"
    static let openAppWhenRun = true
    @available(iOS 26.0, *)
    static var supportedModes: IntentModes { .foreground(.immediate) }

    @MainActor
    func perform() async throws -> some IntentResult {
        AppShortcutRouter.shared.pending = .connectionHelp
        return .result()
    }
}

struct AskHeinzelIntent: AppIntent {
    static let title: LocalizedStringResource = "Ask Heinzel"
    static let description = IntentDescription("Open the prompt composer to choose a Heinzel and send a question.")
    static let openAppWhenRun = true
    @available(iOS 26.0, *)
    static var supportedModes: IntentModes { .foreground(.immediate) }

    @MainActor
    func perform() async throws -> some IntentResult {
        AppShortcutRouter.shared.pending = .writePrompt
        return .result()
    }
}

struct HeinzelShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppIntents.AppShortcut(
            intent: OpenWorkspaceIntent(),
            phrases: ["Open my workspace in \(.applicationName)"],
            shortTitle: "Open Workspace",
            systemImageName: "point.3.connected.trianglepath.dotted"
        )
        AppIntents.AppShortcut(
            intent: OpenConnectionHelpIntent(),
            phrases: ["Get connection help in \(.applicationName)"],
            shortTitle: "Connection Help",
            systemImageName: "questionmark.circle"
        )
        AppIntents.AppShortcut(
            intent: AskHeinzelIntent(),
            phrases: ["Ask \(.applicationName)", "Write a prompt in \(.applicationName)"],
            shortTitle: "Ask Heinzel",
            systemImageName: "square.and.pencil"
        )
    }
}
#endif
