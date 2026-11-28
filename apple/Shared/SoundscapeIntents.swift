import AppIntents
import Foundation
import WidgetKit

/// Static widget configurations share a face within each family. Each family has
/// its own file so neither snapshot publication nor another family's write wins.
enum SoundscapeWidgetFace {
    private static func url(medium: Bool) -> URL? {
        ActivityWidgetData.sharedCatalogURL()?.deletingLastPathComponent()
            .appendingPathComponent(medium ? "soundscape-face-medium.json" : "soundscape-face-small.json")
    }

    static func isShowingInfo(medium: Bool) -> Bool {
        guard let url = url(medium: medium), let data = try? Data(contentsOf: url),
              let value = try? JSONDecoder().decode(Bool.self, from: data) else { return false }
        return value
    }

    static func set(medium: Bool, showInfo: Bool) throws {
        guard let url = url(medium: medium) else { throw CocoaError(.fileNoSuchFile) }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try JSONEncoder().encode(showInfo)
        var coordinationError: NSError?
        var writeError: Error?
        NSFileCoordinator().coordinate(writingItemAt: url, options: .forReplacing, error: &coordinationError) { destination in
            if (try? Data(contentsOf: destination)) == data { return }
            do { try data.write(to: destination, options: .atomic) }
            catch { writeError = error }
        }
        if let coordinationError { throw coordinationError }
        if let writeError { throw writeError }
    }
}

struct SetSoundscapeFaceIntent: AppIntent {
    static let title: LocalizedStringResource = "Set Soundscape widget face"
    static let openAppWhenRun = false
    @Parameter(title: "Medium widget") var medium: Bool
    @Parameter(title: "Show information") var showInfo: Bool

    init() {}
    init(medium: Bool, showInfo: Bool) {
        self.medium = medium
        self.showInfo = showInfo
    }

    func perform() async throws -> some IntentResult {
        try SoundscapeWidgetFace.set(medium: medium, showInfo: showInfo)
        WidgetCenter.shared.reloadTimelines(ofKind: "HeinzelSoundscape")
        return .result()
    }
}

enum SoundscapeTransferAction: String, AppEnum {
    case pause, resume, cancel
    static let typeDisplayRepresentation = TypeDisplayRepresentation(name: "Soundscape transfer action")
    static let caseDisplayRepresentations: [Self: DisplayRepresentation] = [
        .pause: "Pause", .resume: "Resume", .cancel: "Cancel"
    ]

    @MainActor func apply(to library: AlbumLibrary) throws {
        // Read authoritative app state, never the widget's possibly stale snapshot.
        let state = library.snapshot()
        let phase = state["phase"] as? String
        let total = state["total"] as? Int ?? 0
        let verified = state["verified"] as? Int ?? 0
        if total > 0 && verified < total {
            switch self {
            case .pause where phase == "downloading": try library.pauseDownloads()
            case .resume where ["paused", "cancelled", "failed"].contains(phase ?? ""):
                try library.resumeDownloads()
            case .cancel where ["downloading", "paused", "failed"].contains(phase ?? ""):
                try library.cancelDownloads()
            default: break
            }
        }
    }
}

struct SoundscapeTransferIntent: AppIntent {
    static let title: LocalizedStringResource = "Control Soundscape download"
    static let openAppWhenRun = false
    #if compiler(>=6.2)
    @available(iOS 26.0, macOS 26.0, *)
    static var supportedModes: IntentModes { .foreground(.dynamic) }
    #endif
    #if compiler(>=6.4)
    @available(iOS 27.0, macOS 27.0, *)
    static var allowedExecutionTargets: IntentExecutionTargets { .main }
    #endif

    @Parameter(title: "Action") var action: SoundscapeTransferAction

    init() {}
    init(action: SoundscapeTransferAction) { self.action = action }

    @MainActor func perform() async throws -> some IntentResult {
        #if HEINZEL_APP_INTENTS
        let library = AlbumLibrary.shared
        try action.apply(to: library)
        SoundscapePlayback.shared.publishWidgetState()
        #else
        // Never create an extension-private library if execution routing is wrong.
        throw CocoaError(.featureUnsupported)
        #endif
        return .result()
    }
}

// APPLICATION_EXTENSION_API_ONLY is an Xcode setting, not a Swift condition.
// Define HEINZEL_APP_INTENTS only in the containing app's Debug/Release settings.
#if HEINZEL_APP_INTENTS
extension SoundscapeTransferIntent: ForegroundContinuableIntent {}
#endif

struct PlaySoundscapeIntent: AudioPlaybackIntent {
    static let title: LocalizedStringResource = "Play saved Soundscape"
    static let openAppWhenRun = false
    @MainActor func perform() async throws -> some IntentResult {
        #if HEINZEL_APP_INTENTS
        try SoundscapePlayback.shared.playSavedAlbum()
        #else
        throw CocoaError(.featureUnsupported)
        #endif
        return .result()
    }
}

struct PauseSoundscapeIntent: AudioPlaybackIntent {
    static let title: LocalizedStringResource = "Pause Soundscape"
    static let openAppWhenRun = false
    @MainActor func perform() async throws -> some IntentResult {
        #if HEINZEL_APP_INTENTS
        SoundscapePlayback.shared.pause()
        #else
        throw CocoaError(.featureUnsupported)
        #endif
        return .result()
    }
}

enum SoundscapeLink {
    static let artist = URL(string: "https://latentheinzels.heinzel.team/")!
    static let video = URL(string: "heinzel://app/sound/video")!
    static let picker = URL(string: "heinzel://app/sound/picker")!
    private static let trackPath = "/sound/track"

    static func track(_ id: String) -> URL {
        var url = URLComponents()
        url.scheme = "heinzel"
        url.host = "app"
        url.path = trackPath
        url.queryItems = [URLQueryItem(name: "id", value: id)]
        return url.url ?? picker
    }

    static func trackID(_ url: URL) -> String? {
        guard let value = URLComponents(url: url, resolvingAgainstBaseURL: false),
              value.scheme == "heinzel", value.host == "app", value.path == trackPath,
              value.user == nil, value.password == nil, value.port == nil, value.fragment == nil,
              let items = value.queryItems, items.count == 1, items[0].name == "id",
              let id = items[0].value, (1...128).contains(id.utf8.count),
              id.utf8.allSatisfy({ (97...122).contains($0) || (48...57).contains($0) || $0 == 45 }) else { return nil }
        return id
    }
}

struct PreviousSoundscapeIntent: AudioPlaybackIntent {
    static let title: LocalizedStringResource = "Previous track"
    static let openAppWhenRun = false
    @MainActor func perform() async throws -> some IntentResult {
        #if HEINZEL_APP_INTENTS
        try SoundscapePlayback.shared.previous()
        #else
        throw CocoaError(.featureUnsupported)
        #endif
        return .result()
    }
}

struct NextSoundscapeIntent: AudioPlaybackIntent {
    static let title: LocalizedStringResource = "Next track"
    static let openAppWhenRun = false
    @MainActor func perform() async throws -> some IntentResult {
        #if HEINZEL_APP_INTENTS
        try SoundscapePlayback.shared.next()
        #else
        throw CocoaError(.featureUnsupported)
        #endif
        return .result()
    }
}

struct ToggleSoundscapeIntent: AudioPlaybackIntent {
    static let title: LocalizedStringResource = "Play or pause Soundscape"
    static let openAppWhenRun = false
    @MainActor func perform() async throws -> some IntentResult {
        #if HEINZEL_APP_INTENTS
        try SoundscapePlayback.shared.toggle()
        #else
        throw CocoaError(.featureUnsupported)
        #endif
        return .result()
    }
}

struct RepeatSoundscapeIntent: AudioPlaybackIntent {
    static let title: LocalizedStringResource = "Change repeat mode"
    static let openAppWhenRun = false
    @MainActor func perform() async throws -> some IntentResult {
        #if HEINZEL_APP_INTENTS
        SoundscapePlayback.shared.cycleRepeat()
        #else
        throw CocoaError(.featureUnsupported)
        #endif
        return .result()
    }
}

// Opening the app routes each widget action to its app-process playback instance.
struct PickSoundscapeIntent: AppIntent {
    static let title: LocalizedStringResource = "Choose Soundscape track"
    static let openAppWhenRun = true
    @MainActor func perform() async throws -> some IntentResult {
        #if HEINZEL_APP_INTENTS
        SoundscapePlayback.shared.openPicker()
        #else
        throw CocoaError(.featureUnsupported)
        #endif
        return .result()
    }
}

struct WatchSoundscapeIntent: AppIntent {
    static let title: LocalizedStringResource = "Watch Soundscape video"
    static let openAppWhenRun = true
    @Parameter(title: "Track ID") var trackID: String

    init() { trackID = "" }
    init(trackID: String) { self.trackID = trackID }

    @MainActor func perform() async throws -> some IntentResult {
        #if HEINZEL_APP_INTENTS
        SoundscapePlayback.shared.dockWidth = nil
        SoundscapePlayback.shared.openVideo(trackID: trackID)
        #else
        throw CocoaError(.featureUnsupported)
        #endif
        return .result()
    }
}
