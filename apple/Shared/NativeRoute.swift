import Foundation

/// Fixed native routes. Shared workspace URLs are handled separately by the app.
enum NativeRoute: String {
    case workspace
    case connectionHelp = "connection-help"

    var url: URL { URL(string: "heinzel://app/\(rawValue)")! }

    init?(url: URL) {
        switch url.absoluteString {
        case Self.workspace.url.absoluteString: self = .workspace
        case Self.connectionHelp.url.absoluteString: self = .connectionHelp
        default: return nil
        }
    }
}
