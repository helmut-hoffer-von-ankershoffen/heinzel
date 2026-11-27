import Foundation

/// Distinguishes an explicit menu request made during an asynchronous connection.
struct MacConnectionIntent {
    private(set) var revision = 0

    mutating func requestSetup() { revision += 1 }

    func keepSetup(afterStartedAt startRevision: Int) -> Bool {
        revision != startRevision
    }
}

/// Menu availability and routing are independent of the embedded workspace's lifetime.
struct MacMenuState {
    let hasEndpoint: Bool
    let choosingWorkspace: Bool
    let starting: Bool
    let loading: Bool
    let hasProblem: Bool
    let connectionPresented: Bool
    let otherSheetPresented: Bool

    var workspaceAvailable: Bool {
        hasEndpoint && !choosingWorkspace && !starting && !loading && !hasProblem &&
            !connectionPresented && !otherSheetPresented
    }

    enum ConnectionRoute: Equatable { case setup, connection, alreadyVisible, unavailable }

    var connectionRoute: ConnectionRoute {
        if otherSheetPresented { return .unavailable }
        if connectionPresented || choosingWorkspace || (!hasEndpoint && !starting) { return .alreadyVisible }
        return hasEndpoint ? .connection : .setup
    }
}

enum MacMenuDelivery {
    static func allowed(available: Bool, visible: Bool, keyWindow: Bool,
                        ownsOrigin: Bool, committed: Bool, loading: Bool) -> Bool {
        available && visible && keyWindow && ownsOrigin && committed && !loading
    }
}

/// Routes within the native app. Shared workspace URLs are handled separately.
enum NativeRoute: Equatable {
    case workspace
    case connectionHelp
    case chooseHeinzel
    case writePrompt
    case chat(workspace: String, id: String)

    var url: URL {
        let path: String
        switch self {
        case .workspace: path = "workspace"
        case .connectionHelp: path = "connection-help"
        case .chooseHeinzel: path = "choose-heinzel"
        case .writePrompt: path = "write-prompt"
        case .chat(let workspace, let id): path = "chat/\(workspace)/\(id)"
        }
        return URL(string: "heinzel://app/\(path)")!
    }

    init?(url: URL) {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "heinzel", parts.host == "app", parts.user == nil, parts.password == nil,
              parts.port == nil, parts.query == nil, parts.fragment == nil else { return nil }
        switch parts.percentEncodedPath {
        case "/workspace": self = .workspace
        case "/connection-help": self = .connectionHelp
        case "/choose-heinzel": self = .chooseHeinzel
        case "/write-prompt": self = .writePrompt
        default:
            guard parts.percentEncodedPath.hasPrefix("/chat/") else { return nil }
            let path = parts.percentEncodedPath.dropFirst(6).split(separator: "/", omittingEmptySubsequences: false)
            guard path.count == 2, path[0].wholeMatch(of: /[a-f0-9]{64}/) != nil,
                  Self.isSessionID(String(path[1])) else { return nil }
            self = .chat(workspace: String(path[0]), id: String(path[1]))
        }
    }

    static func isSessionID(_ value: String) -> Bool {
        value.wholeMatch(of: /hz-[0-9A-HJKMNP-TV-Z]{26}/) != nil
    }
}
