import Foundation

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
