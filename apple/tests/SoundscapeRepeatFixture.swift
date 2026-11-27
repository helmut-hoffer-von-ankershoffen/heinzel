import Foundation
import AVFoundation

/// Real player boundaries only. This observer never posts a transport notification.
@MainActor
final class NaturalEnds {
    var items: [AVPlayerItem] = []
    var token: NSObjectProtocol?
    init() {
        token = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime, object: nil, queue: .main
        ) { [weak self] note in
            guard let item = note.object as? AVPlayerItem else { return }
            Task { @MainActor [weak self] in self?.items.append(item) }
        }
    }
    func close() { if let token { NotificationCenter.default.removeObserver(token) } }
}

@main struct SoundscapeRepeatFixture {
    @MainActor static func main() async {
        do { try await run() }
        catch {
            print("FAIL: \(error)")
            exit(1)
        }
    }

    @MainActor static func run() async throws {
        let args = CommandLine.arguments
        try SoundscapeAlbumTests.require(args.count == 7, "MODE ROOT CATALOG REPEAT INDEX EVIDENCE required")
        let origin = args[1], mode = args[4]
        guard let index = Int(args[5]), (0...1).contains(index) else {
            throw SoundscapeAlbumTests.Failure(description: "invalid track index")
        }
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil
        config.httpCookieStorage = nil
        config.urlCredentialStorage = nil
        config.connectionProxyDictionary = [:]
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 8
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let root = URL(fileURLWithPath: args[2])
        let defaults = UserDefaults(suiteName: "example.repeat.\(UUID().uuidString)")!
        let library = AlbumLibrary(root: root, catalogURL: URL(string: args[3])!, session: session)
        var state = SoundscapeWidgetState()
        let playback = SoundscapePlayback(library: library, defaults: defaults, publish: { state = $0 })
        playback.player.isMuted = true
        defer { playback.pause() }
        var events: [[String: Any]] = []
        var boundaryItemID = "none"
        let evidence = URL(fileURLWithPath: args[6])
        func record(_ event: String) throws {
            playback.publishWidgetState()
            let position = playback.player.currentTime().seconds
            let duration = playback.player.currentItem?.duration.seconds ?? .nan
            events.append([
                "event": event, "uptime": ProcessInfo.processInfo.systemUptime,
                "pid": ProcessInfo.processInfo.processIdentifier, "mode": playback.repeatMode,
                "selectedID": state.selectedID ?? "none", "title": playback.currentTitle,
                "itemIdentity": playback.player.currentItem.map { String(describing: ObjectIdentifier($0)) } ?? "none",
                "boundaryItemIdentity": boundaryItemID,
                "url": (playback.player.currentItem?.asset as? AVURLAsset)?.url.absoluteString ?? "none",
                "position": position.isFinite ? position : -1,
                "duration": duration.isFinite ? duration : -1,
                "playing": playback.isPlaying, "rate": playback.player.rate,
                "timeControl": playback.player.timeControlStatus.rawValue,
                "library": library.snapshot()
            ])
            try JSONSerialization.data(withJSONObject: events, options: [.prettyPrinted, .sortedKeys])
                .write(to: evidence, options: .atomic)
        }
        try record("start")
        do {
            if origin == "seed" {
                _ = try await library.playableTracks()
                try await SoundscapeAlbumTests.wait("seed did not verify both files") {
                    try library.savedTracks().count == 2 && library.snapshot()["phase"] as? String == "ready"
                }
                try record("seed-verified")
                return
            }
            try SoundscapeAlbumTests.require(["remote", "offline"].contains(origin), "invalid origin")
            if origin == "remote" { try library.pauseDownloads() }
            else {
                try await SoundscapeAlbumTests.wait("cold verification failed") {
                    try library.savedTracks().count == 2 && library.snapshot()["phase"] as? String == "ready"
                }
            }
            let tracks = try await library.playableTracks()
            try SoundscapeAlbumTests.require(tracks.count == 2, "requires exact two-track fixture")
            try SoundscapeAlbumTests.require(tracks.allSatisfy { $0.url.isFileURL == (origin == "offline") }, "wrong queue URL origin")
            // Exercise the owner-visible cycle, including returning to Off, before every case.
            try SoundscapeAlbumTests.require(playback.repeatMode == "Off", "initial mode")
            for expected in ["Album", "Track", "Off"] {
                playback.cycleRepeat()
                try SoundscapeAlbumTests.require(playback.repeatMode == expected, "repeat cycle mismatch")
                try record("cycle-\(expected)")
            }
            try SoundscapeAlbumTests.require(["Off", "Album", "Track"].contains(mode), "invalid repeat")
            while playback.repeatMode != mode { playback.cycleRepeat() }
            let ends = NaturalEnds()
            defer { ends.close() }
            try await playback.selectApp(tracks[index].id)
            guard let initial = playback.player.currentItem else {
                throw SoundscapeAlbumTests.Failure(description: "missing initial item")
            }
            boundaryItemID = String(describing: ObjectIdentifier(initial))
            func healthy() throws {
                try SoundscapeAlbumTests.require(playback.errorMessage == nil && playback.player.error == nil,
                                                "player failure")
                if let error = playback.player.currentItem?.error { throw error }
                let url = (playback.player.currentItem?.asset as? AVURLAsset)?.url
                try SoundscapeAlbumTests.require(url != nil && url?.isFileURL == (origin == "offline"), "selected URL origin changed")
                if origin == "remote" {
                    try SoundscapeAlbumTests.require(try library.savedTracks().isEmpty &&
                        library.snapshot()["phase"] as? String == "paused", "stream became saved")
                }
            }
            try await SoundscapeAlbumTests.wait("initial item did not naturally progress near end") {
                try healthy()
                let seconds = playback.player.currentTime().seconds
                let duration = initial.duration.seconds
                return playback.player.currentItem === initial && playback.isPlaying &&
                    playback.player.rate > 0 && duration.isFinite && duration > 0 &&
                    seconds > duration * 0.6 && seconds < duration
            }
            try record("before-boundary")
            try await SoundscapeAlbumTests.wait("no natural end for original item") {
                try healthy()
                return ends.items.contains { $0 === initial }
            }
            try record("natural-end-observed")
            let stopped = mode == "Off" && index == 1
            let expectedIndex = mode == "Track" || stopped ? index : (index + 1) % 2
            var resetPosition: Double?
            var stoppedAt: Double?
            try await SoundscapeAlbumTests.wait("boundary outcome did not stabilize") {
                try healthy()
                playback.publishWidgetState()
                let seconds = playback.player.currentTime().seconds
                guard state.selectedID == tracks[expectedIndex].id,
                      playback.currentTitle == tracks[expectedIndex].title,
                      (playback.player.currentItem?.asset as? AVURLAsset)?.url == tracks[expectedIndex].url,
                      seconds.isFinite else { return false }
                if stopped {
                    guard !playback.isPlaying, playback.player.rate == 0,
                          seconds >= initial.duration.seconds - 0.15 else {
                        stoppedAt = nil
                        return false
                    }
                    if stoppedAt == nil { stoppedAt = ProcessInfo.processInfo.systemUptime }
                    return ProcessInfo.processInfo.systemUptime - stoppedAt! > 0.5
                }
                guard playback.isPlaying, playback.player.rate > 0,
                      playback.player.timeControlStatus == .playing else { return false }
                if resetPosition == nil && seconds < 0.5 {
                    resetPosition = seconds
                    try record("reset-observed")
                }
                return resetPosition.map { seconds > $0 + 0.15 && seconds < 1.2 } ?? false
            }
            try record("PASS")
        } catch {
            try? record("FAIL: \(error)")
            throw error
        }
    }
}
