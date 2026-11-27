import Foundation
import AVFoundation

/// Reusable CLI assertions for the injected product player; no singleton or widget publisher.
@MainActor
struct SoundscapeAlbumTests {
    struct Failure: Error, CustomStringConvertible { let description: String }

    static func require(_ value: Bool, _ message: String) throws {
        if !value { throw Failure(description: message) }
    }

    static func wait(_ message: String, until predicate: () throws -> Bool) async throws {
        for _ in 0..<300 {
            if try predicate() { return }
            try await Task.sleep(for: .milliseconds(100))
        }
        throw Failure(description: message)
    }

    static func offline(library: AlbumLibrary, defaults: UserDefaults) async throws {
        try await wait("copied store did not finish re-verification") { try library.savedTracks().count == 2 }
        try require(library.snapshot()["phase"] as? String == "ready", "cold complete album lost its ready status")
        let tracks = try library.savedTracks()
        var states: [SoundscapeWidgetState] = []
        let playback = SoundscapePlayback(library: library, defaults: defaults, publish: { states.append($0) })
        playback.player.isMuted = true
        defer { playback.pause() }
        try require(tracks.allSatisfy { $0.url.isFileURL }, "offline queue contains remote media")
        try playback.playSavedAlbum()
        try require(playback.currentTitle == tracks[0].title, "cold Play selected wrong title")
        try await wait("local decode did not progress") {
            try require(playback.errorMessage == nil, "local decode failed")
            return playback.player.currentTime().seconds > 0.1
        }
        try await wait("Off did not naturally advance by title") { playback.currentTitle == tracks[1].title }
        try await wait("Off did not stop at album end") { !playback.isPlaying }
        try require(playback.currentTitle == tracks[1].title, "Off wrapped")
        playback.cycleRepeat()
        try require(playback.repeatMode == "Album", "Off → Album failed")
        try playback.select(tracks[1].id)
        try await wait("Album did not naturally wrap") { playback.currentTitle == tracks[0].title }
        try playback.previous()
        try require(playback.currentTitle == tracks[1].title, "Album previous failed to wrap")
        try playback.next()
        try require(playback.currentTitle == tracks[0].title, "Album next failed to wrap")
        playback.cycleRepeat()
        try require(playback.repeatMode == "Track", "Album → Track failed")
        try playback.select(tracks[0].id)
        var reachedEnd = false
        try await wait("Track did not repeat the selected title") {
            try require(playback.currentTitle == tracks[0].title, "Track advanced to another title")
            let seconds = playback.player.currentTime().seconds
            if seconds > 0.8 { reachedEnd = true }
            return reachedEnd && seconds < 0.4 && playback.isPlaying
        }
        playback.cycleRepeat()
        try require(playback.repeatMode == "Off", "Track → Off failed")
        playback.pause()
        try playback.toggle()
        try require(playback.isPlaying, "toggle did not resume")
        try playback.toggle()
        try require(!playback.isPlaying, "toggle did not pause")
        try playback.select(tracks[1].id)
        playback.openPicker()
        try require(playback.showingPicker && playback.showingDock, "picker route missing")
        playback.openVideo()
        try require(!playback.showingPicker && playback.showingDock, "Watch route missing")
        try require(playback.videoURL == tracks[1].url, "Watch did not use selected cached file")
        try await wait("selected cached video did not advance") { playback.player.currentTime().seconds > 0.1 }
        playback.pause()
        try require(states.last?.selectedID == tracks[1].id && states.last?.video == true &&
                    (states.last?.position ?? 0) > 0.1, "paused video position missing from widget snapshot")
        let snapshot = library.snapshot()
        try require(states.last?.verified == (snapshot["verified"] as? Int) &&
                    states.last?.total == (snapshot["total"] as? Int) &&
                    states.last?.phase == (snapshot["phase"] as? String), "published download snapshot differs")
    }
}
