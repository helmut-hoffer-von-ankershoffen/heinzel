import AVFoundation
import ImageIO
import Observation
import WidgetKit

/// One atomic document contains both playback selection and observed download state.
struct SoundscapeWidgetState: Codable {
    var title = "Soundscape"
    var playing = false
    var repeatMode = "Off"
    var verified = 0
    var total = 0
    var phase = "unavailable"
    var selectedID: String?
    var video = false
    var posterID: String?
    var position: Double?
    var sampledAt: Date?
    var duration: Double?
    var origin = "none"
    var advancing = false
    var canPlaySaved = false
    var canPrevious = false
    var canNext = false
    var canWatch = false

    var playbackOrigin: Date? {
        guard let sampledAt, let position, position.isFinite, position >= 0 else { return nil }
        return sampledAt.addingTimeInterval(-position)
    }

    var artworkURL: URL? {
        guard selectedID != nil, let id = posterID, Self.validPosterID(id),
              let folder = Self.url?.deletingLastPathComponent() else { return nil }
        return folder.appending(path: "soundscape-\(id).jpg")
    }

    private static func validPosterID(_ id: String) -> Bool {
        id.utf8.count == 64 && id.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }

    private static var url: URL? {
        ActivityWidgetData.sharedCatalogURL()?.deletingLastPathComponent()
            .appendingPathComponent("soundscape-state.json")
    }

    static func read() -> Self? {
        guard let url, let data = try? Data(contentsOf: url),
              let value = try? JSONDecoder().decode(Self.self, from: data),
              ["Off", "Album", "Track"].contains(value.repeatMode),
              value.verified >= 0, value.total >= value.verified,
              ["idle", "downloading", "paused", "cancelled", "ready", "failed"].contains(value.phase),
              ["none", "saved", "stream"].contains(value.origin),
              let sampledAt = value.sampledAt, sampledAt.timeIntervalSince1970.isFinite,
              let position = value.position, position.isFinite, (0...2_678_400).contains(position),
              value.duration.map({ $0.isFinite && $0 > 0 && $0 <= 2_678_400 }) ?? true,
              value.posterID.map(Self.validPosterID) ?? true,
              value.selectedID.map({ !$0.isEmpty }) ?? true,
              value.posterID == nil || value.selectedID != nil,
              !value.advancing || value.playing,
              !value.playing || (value.selectedID != nil && value.origin != "none"),
              value.canPlaySaved || !(value.canPrevious || value.canNext),
              !value.canWatch || value.selectedID != nil,
              !value.canPlaySaved || value.verified > 0
        else { return nil }
        return value
    }

    func write(poster: URL?) {
        guard let url = Self.url else { return }
        var state = self
        state.posterID = nil
        if selectedID != nil, let poster, Self.validPosterID(poster.deletingPathExtension().lastPathComponent) {
            let id = poster.deletingPathExtension().lastPathComponent
            let destination = url.deletingLastPathComponent().appending(path: "soundscape-\(id).jpg")
            if !FileManager.default.fileExists(atPath: destination.path),
               let source = CGImageSourceCreateWithURL(poster as CFURL, nil) {
                let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                               kCGImageSourceThumbnailMaxPixelSize: 160,
                                               kCGImageSourceCreateThumbnailWithTransform: true]
                let bytes = NSMutableData()
                if let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary),
                   let writer = CGImageDestinationCreateWithData(bytes, "public.jpeg" as CFString, 1, nil) {
                    CGImageDestinationAddImage(writer, thumbnail, nil)
                    if CGImageDestinationFinalize(writer) { try? (bytes as Data).write(to: destination, options: .atomic) }
                }
            }
            if FileManager.default.fileExists(atPath: destination.path) { state.posterID = id }
        }
        do { try JSONEncoder().encode(state).write(to: url, options: .atomic) }
        catch { return }
        // Old entries may lose their thumbnail, but must never borrow a new selection's art.
        let keep = state.posterID.map { "soundscape-\($0).jpg" }
        let folder = url.deletingLastPathComponent()
        for file in (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? [] {
            let name = file.lastPathComponent
            if name.hasPrefix("soundscape-"), name.hasSuffix(".jpg"), name != keep,
               Self.validPosterID(String(name.dropFirst(11).dropLast(4))) {
                try? FileManager.default.removeItem(at: file)
            }
        }
    }
}

/// All transport commands run in the app process and share this queue.
@MainActor @Observable
final class SoundscapePlayback {
    static let shared = SoundscapePlayback()
    static let changed = Notification.Name("SoundscapePlayback.changed")
    typealias ResumeSeek = @MainActor (AVQueuePlayer, CMTime, @escaping @Sendable (Bool) -> Void) -> Void

    private let library: AlbumLibrary
    private let resumeSeek: ResumeSeek
    private let defaults: UserDefaults
    private let publish: @MainActor (SoundscapeWidgetState) -> Void
    private var downloadObserver: NSObjectProtocol?
    private var positionObserver: Any?
    private var restoredPosition: Double = 0
    private var pendingResumePosition: Double?
    var isResuming: Bool { pendingResumePosition != nil }
    private var loadedURL: URL?
    private var albumEnded = false
    let player = AVQueuePlayer()
    private(set) var errorMessage: String?
    private(set) var isPlaying = false
    private(set) var repeatMode: String
    private(set) var currentTitle: String
    private var watchGeneration = UUID()
    var dockWidth: CGFloat?
    var showingDock = false { didSet { if !showingDock { watchGeneration = UUID() } } }
    var showingPicker = false { didSet { if showingPicker { watchGeneration = UUID() } } }
    private(set) var videoURL: URL?
    private var tracks: [AlbumPlayableTrack] = []
    private var selectedID: String?
    private var videoSelected: Bool
    private var rateObservation: NSKeyValueObservation?
    private var itemObservation: NSKeyValueObservation?
    private var durationObservation: NSKeyValueObservation?
    private var timeControlObservation: NSKeyValueObservation?
    private var seekObserver: NSObjectProtocol?
    private var publishedDuration: Double?
    private var endObserver: NSObjectProtocol?
    private var failureObserver: NSObjectProtocol?

    // Restore only selection, never a previous process's transport availability.
    private struct Selection: Decodable {
        var title: String?
        var selectedID: String?
        var repeatMode: String?
        var video: Bool?
        var position: Double?
    }

    /// Fixture dependencies isolate playback, publishing and the outcome of a resume seek.
    init(library: AlbumLibrary = .shared, defaults: UserDefaults = .standard,
         resumeSeek: @escaping ResumeSeek = { player, time, completion in
             player.seek(to: time, completionHandler: completion)
         },
         publish: @escaping @MainActor (SoundscapeWidgetState) -> Void = { state in
             state.write(poster: AlbumLibrary.shared.savedPoster(for: state.selectedID))
             WidgetCenter.shared.reloadTimelines(ofKind: "HeinzelSoundscape")
         }) {
        self.library = library
        self.resumeSeek = resumeSeek
        self.defaults = defaults
        self.publish = publish
        let saved = defaults.data(forKey: "soundscapePlayback")
            .flatMap { try? JSONDecoder().decode(Selection.self, from: $0) }
        selectedID = saved?.selectedID
        let mode = saved?.repeatMode ?? "Off"
        repeatMode = ["Off", "Album", "Track"].contains(mode) ? mode : "Off"
        currentTitle = saved?.title ?? "Soundscape"
        videoSelected = saved?.video ?? false
        let position = saved?.position ?? 0
        restoredPosition = position.isFinite ? min(max(0, position), 2_678_400) : 0
        downloadObserver = NotificationCenter.default.addObserver(
            forName: AlbumLibrary.changed, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in self?.publishWidgetState() }
        }
        positionObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 5, preferredTimescale: 1), queue: .main) {
            [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, isPlaying, let data = self.defaults.data(forKey: "soundscapePlayback"),
                      var checkpoint = try? JSONDecoder().decode(SoundscapeWidgetState.self, from: data),
                      checkpoint.selectedID == selectedID else { return }
                checkpoint.position = playbackPosition
                checkpoint.sampledAt = .now
                if let encoded = try? JSONEncoder().encode(checkpoint) { self.defaults.set(encoded, forKey: "soundscapePlayback") }
            }
        }
        rateObservation = player.observe(\.rate, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                let playing = self.loadedURL != nil && self.player.rate != 0
                if self.isPlaying != playing { self.setPlaying(playing) }
            }
        }
        timeControlObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in self?.publishWidgetState() }
        }
        seekObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemTimeJumped, object: nil, queue: .main
        ) { [weak self] note in
            guard let item = note.object as? AVPlayerItem else { return }
            Task { @MainActor [weak self] in
                guard let self, self.player.currentItem === item else { return }
                self.publishWidgetState()
            }
        }
        // A previous process's playing flag is not evidence of live playback.
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime, object: nil, queue: .main
        ) { [weak self] note in
            guard let item = note.object as? AVPlayerItem else { return }
            Task { @MainActor [weak self] in
                guard let self, self.player.currentItem === item else { return }
                self.watchGeneration = UUID()
                do {
                    if self.repeatMode == "Track" { self.albumEnded = true; try self.startSelected() }
                    else { try self.move(1) }
                } catch { self.fail(error.localizedDescription) }
            }
        }
        failureObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime, object: nil, queue: .main
        ) { [weak self] note in
            guard let item = note.object as? AVPlayerItem else { return }
            Task { @MainActor [weak self] in
                guard let self, self.player.currentItem === item else { return }
                self.failCurrentItem("Playback stopped. Open Sound and try again.")
            }
        }
    }

    func savedTracks() throws -> [AlbumPlayableTrack] {
        try library.savedTracks()
    }

    private func loadSaved() throws {
        if loadedURL?.isFileURL == false {
            restoredPosition = playbackPosition
            clearItem()
            isPlaying = false
        }
        tracks = try savedTracks()
        guard !tracks.isEmpty else { clearItem(); throw PlaybackError.noSavedTracks }
        if !tracks.contains(where: { $0.id == selectedID }) {
            selectedID = tracks[0].id
            restoredPosition = 0
            clearItem()
        }
    }

    /// App entry: starts automatic saving and permits streaming while files arrive.
    func playAlbum() async throws {
        watchGeneration = UUID()
        let generation = watchGeneration
        do {
            let available = try await library.playableTracks()
            guard watchGeneration == generation else { return }
            tracks = available
            guard !tracks.isEmpty else { throw PlaybackError.noSavedTracks }
            if !tracks.contains(where: { $0.id == selectedID }) {
                selectedID = tracks[0].id
                restoredPosition = 0
                clearItem()
            }
            try startSelected()
        } catch {
            guard watchGeneration == generation else { return }
            fail(error.localizedDescription)
            throw error
        }
    }

    /// Widget entry points always re-verify the local library, even with a loaded app stream.
    func playSavedAlbum() throws {
        watchGeneration = UUID()
        do { try loadSaved(); try startSelected() }
        catch { fail(error.localizedDescription); throw error }
    }

    func toggle() throws {
        watchGeneration = UUID()
        // Decide before local verification can replace an app's streaming item.
        if isPlaying || player.rate != 0 { pause(); return }
        do {
            try loadSaved()
            try startSelected()
        } catch { fail(error.localizedDescription); throw error }
    }

    func previous() throws { try savedMove(-1) }
    func next() throws { try savedMove(1) }

    private func savedMove(_ delta: Int) throws {
        watchGeneration = UUID()
        do {
            try loadSaved()
            try move(delta)
        } catch { fail(error.localizedDescription); throw error }
    }

    func toggleApp() async throws {
        if isPlaying { pause() } else { try await playAlbum() }
    }

    func previousApp() async throws { try await moveApp(-1) }
    func nextApp() async throws { try await moveApp(1) }

    private func moveApp(_ delta: Int) async throws {
        watchGeneration = UUID()
        let generation = watchGeneration
        do {
            let available = try await library.playableTracks()
            guard watchGeneration == generation else { return }
            tracks = available
            guard !tracks.isEmpty else { throw PlaybackError.noSavedTracks }
            try move(delta)
        } catch {
            guard watchGeneration == generation else { return }
            fail(error.localizedDescription)
            throw error
        }
    }

    private func move(_ delta: Int) throws {
        if tracks.isEmpty { try loadSaved() }
        let index = tracks.firstIndex(where: { $0.id == selectedID }) ?? 0
        var next = index + delta
        if !tracks.indices.contains(next) {
            guard repeatMode == "Album" else {
                if delta > 0 { albumEnded = true; pause() }
                return
            }
            next = (next + tracks.count) % tracks.count
        }
        selectedID = tracks[next].id
        restoredPosition = 0
        clearItem()
        try startSelected()
    }

    func selectApp(_ id: String) async throws {
        watchGeneration = UUID()
        let generation = watchGeneration
        let available = try await library.playableTracks()
        guard watchGeneration == generation else { return }
        tracks = available
        guard tracks.contains(where: { $0.id == id }) else { throw PlaybackError.noSavedTracks }
        if selectedID != id { restoredPosition = 0; clearItem() }
        selectedID = id
        try startSelected()
    }

    func select(_ id: String, video: Bool = false) throws {
        watchGeneration = UUID()
        do {
            try loadSaved()
            guard tracks.contains(where: { $0.id == id }) else { throw PlaybackError.noSavedTracks }
            if selectedID != id { restoredPosition = 0; clearItem() }
            selectedID = id
            videoSelected = video
            try startSelected()
        } catch { fail(error.localizedDescription); throw error }
    }

    func cycleRepeat() {
        repeatMode = repeatMode == "Off" ? "Album" : repeatMode == "Album" ? "Track" : "Off"
        publishWidgetState()
    }

    private func activateAudio() throws {
        #if os(iOS)
        try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)
        try AVAudioSession.sharedInstance().setActive(true)
        #endif
    }

    private func startSelected() throws {
        guard let track = tracks.first(where: { $0.id == selectedID })
        else { throw PlaybackError.noSavedTracks }
        currentTitle = track.title
        try activateAudio()
        if loadedURL == track.url, player.currentItem != nil, player.currentItem?.status != .failed {
            if albumEnded {
                pendingResumePosition = nil
                player.seek(to: .zero)
                albumEnded = false
            }
            currentTitle = track.title
            videoURL = videoSelected ? track.url : nil
            errorMessage = nil
            player.play()
            setPlaying(true)
            return
        }
        let position = albumEnded ? 0 : playbackPosition
        clearItem()
        albumEnded = false
        // Keep the completed item until our explicit queue/repeat transition runs.
        player.actionAtItemEnd = .pause
        let item = AVPlayerItem(url: track.url)
        itemObservation = item.observe(\.status, options: [.new]) { [weak self] item, _ in
            Task { @MainActor [weak self] in
                guard let self, self.player.currentItem === item, item.status == .failed else { return }
                self.failCurrentItem("This track could not play. Open Sound and try again.")
            }
        }
        durationObservation = item.observe(\.duration, options: [.new]) { [weak self] item, _ in
            Task { @MainActor [weak self] in
                guard let self, self.player.currentItem === item,
                      item.duration.seconds.isFinite, item.duration.seconds > 0,
                      item.duration.seconds != self.publishedDuration else { return }
                self.publishWidgetState()
            }
        }
        player.insert(item, after: nil)
        loadedURL = track.url
        if position > 0 {
            pendingResumePosition = position
            resumeSeek(player, CMTime(seconds: position, preferredTimescale: 600)) { [weak self] finished in
                Task { @MainActor [weak self] in
                    guard let self, self.player.currentItem === item, self.pendingResumePosition != nil else { return }
                    guard finished else {
                        self.failCurrentItem("Playback could not resume. Open Sound and try again.")
                        return
                    }
                    self.pendingResumePosition = nil
                    self.publishWidgetState()
                }
            }
        }
        restoredPosition = 0
        currentTitle = track.title
        videoURL = videoSelected ? track.url : nil
        errorMessage = nil
        player.play()
        setPlaying(true)
    }

    func pause() { watchGeneration = UUID(); player.pause(); setPlaying(false) }

    func openPicker() { dockWidth = nil; showingPicker = true; showingDock = true }

    /// The app's legacy unparameterized route may choose the normal current/first track.
    func openVideo() { openVideo(trackID: selectedID) }

    /// A supplied ID is exact: only verified files or catalog entries may resolve it.
    /// Each foreground action owns a generation so late catalog results cannot reopen video.
    func openVideo(trackID requested: String?) {
        showingPicker = false
        showingDock = true
        watchGeneration = UUID()
        let generation = watchGeneration
        let selection = selectedID
        if requested != selectedID || requested == "" {
            player.pause()
            isPlaying = false
            videoSelected = false
            videoURL = nil
        }
        func apply(_ available: [AlbumPlayableTrack]) throws {
            guard let track = available.first(where: { requested == nil || $0.id == requested }) else {
                throw PlaybackError.watchUnavailable
            }
            let sameSelection = selectedID == track.id
            let position = sameSelection ? playbackPosition : 0
            if !sameSelection {
                clearItem()
                restoredPosition = 0
            } else if loadedURL != track.url {
                clearItem()
                restoredPosition = position
            }
            tracks = available
            selectedID = track.id
            videoSelected = true
            try startSelected()
        }
        do {
            let saved = try savedTracks()
            if saved.contains(where: { requested == nil || $0.id == requested }) {
                try apply(saved)
                return
            }
            // A running catalog stream is already resolved. Never replace its player item.
            if let requested, selectedID == requested, player.currentItem != nil,
               loadedURL?.isFileURL == false,
               tracks.contains(where: { $0.id == requested && $0.url == loadedURL }) {
                videoSelected = true
                try startSelected()
                return
            }
        } catch { fail(error.localizedDescription); return }
        Task {
            do {
                let available = try await library.playableTracks()
                guard watchGeneration == generation, showingDock, !showingPicker,
                      selectedID == selection else { return }
                // Downloads may have completed while the catalog was loading.
                let saved = try savedTracks()
                let resolved = available.map { track in saved.first { $0.id == track.id } ?? track }
                do { try apply(resolved) }
                catch { fail(error.localizedDescription) }
            } catch {
                if watchGeneration == generation, showingDock, !showingPicker, selectedID == selection {
                    fail(error.localizedDescription)
                }
            }
        }
    }

    var albumDuration: Double? {
        guard let seconds = player.currentItem?.duration.seconds, seconds.isFinite, seconds > 0 else { return nil }
        return seconds
    }

    func seekSelectedTrack(_ trackID: String, to seconds: Double) async -> Bool {
        guard selectedID == trackID, seconds.isFinite, seconds >= 0,
              pendingResumePosition == nil, let item = player.currentItem, let duration = albumDuration else { return false }
        let target = CMTime(seconds: min(seconds, duration), preferredTimescale: 600)
        item.cancelPendingSeeks()
        let finished = await withCheckedContinuation { continuation in
            player.seek(to: target, toleranceBefore: .zero, toleranceAfter: .zero) {
                continuation.resume(returning: $0)
            }
        }
        return finished && player.currentItem === item && selectedID == trackID
    }

    var playbackPosition: Double {
        if let pendingResumePosition { return pendingResumePosition }
        let seconds = player.currentTime().seconds
        return loadedURL != nil && player.currentItem != nil && seconds.isFinite ? max(0, seconds) : restoredPosition
    }

    private func clearItem() {
        itemObservation = nil
        durationObservation = nil
        pendingResumePosition = nil
        player.pause()
        player.removeAllItems()
        loadedURL = nil
        videoURL = nil
        publishedDuration = nil
    }

    func publishWidgetState() {
        let download = library.snapshot()
        var state = SoundscapeWidgetState()
        state.title = currentTitle
        state.playing = isPlaying
        state.repeatMode = repeatMode
        state.selectedID = selectedID
        state.video = videoSelected
        state.position = playbackPosition
        state.sampledAt = .now
        state.origin = loadedURL.map { $0.isFileURL ? "saved" : "stream" } ?? "none"
        let duration = player.currentItem?.duration.seconds
        state.duration = duration.flatMap { $0.isFinite && $0 > 0 ? $0 : nil }
        publishedDuration = state.duration
        // Waiting/buffering must freeze the widget clock until media actually advances.
        state.advancing = isPlaying && !isResuming && player.timeControlStatus == .playing
        let saved = (try? savedTracks()) ?? []
        let index = saved.firstIndex(where: { $0.id == selectedID }) ?? 0
        state.canPlaySaved = !saved.isEmpty
        state.canPrevious = !saved.isEmpty && (index > 0 || repeatMode == "Album")
        state.canNext = !saved.isEmpty && (index + 1 < saved.count || repeatMode == "Album")
        // Watch opens the foreground app, whose selected-video path may stream.
        state.canWatch = selectedID != nil
        state.verified = download["verified"] as? Int ?? 0
        state.total = download["total"] as? Int ?? 0
        state.phase = download["phase"] as? String ?? "unavailable"
        if let data = try? JSONEncoder().encode(state) {
            defaults.set(data, forKey: "soundscapePlayback")
        }
        publish(state)
        NotificationCenter.default.post(name: Self.changed, object: self)
    }

    private func failCurrentItem(_ message: String) {
        restoredPosition = playbackPosition
        clearItem()
        videoSelected = false
        fail(message)
    }

    private func fail(_ message: String) { errorMessage = message; pause() }
    private func setPlaying(_ value: Bool) { isPlaying = value; publishWidgetState() }

    enum PlaybackError: LocalizedError {
        case noSavedTracks
        case watchUnavailable
        var errorDescription: String? {
            switch self {
            case .watchUnavailable: "This video is unavailable. Open Sound and choose a track."
            case .noSavedTracks: "No saved tracks are available. Save the album in Sound first."
            }
        }
    }
}
