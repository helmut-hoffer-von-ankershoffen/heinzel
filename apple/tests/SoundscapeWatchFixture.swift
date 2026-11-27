import AVFoundation
import Foundation

/// Native, isolated player proof. Supply the same two-track catalog/store as the album fixture.
@main struct SoundscapeWatchFixture {
    @MainActor static func main() async {
        do { try await run() }
        catch { print("FAIL: \(error)"); exit(1) }
    }

    @MainActor static func run() async throws {
        let args = CommandLine.arguments
        try SoundscapeAlbumTests.require(args.count == 5, "offline|stream ROOT CATALOG EVIDENCE required")
        let offline = args[1] == "offline"
        try SoundscapeAlbumTests.require(offline || args[1] == "stream", "invalid origin")
        let session = URLSession(configuration: .ephemeral)
        defer { session.invalidateAndCancel() }
        let library = AlbumLibrary(root: URL(fileURLWithPath: args[2]),
                                   catalogURL: URL(string: args[3])!, session: session)
        if offline {
            try await SoundscapeAlbumTests.wait("store not verified") { try library.savedTracks().count == 2 }
        } else { try library.pauseDownloads() }
        let tracks = try await library.playableTracks()
        try SoundscapeAlbumTests.require(tracks.count == 2, "two tracks required")
        try SoundscapeAlbumTests.require(tracks.allSatisfy { $0.url.isFileURL == offline }, "wrong origin")
        let suite = "example.watch.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        var state = SoundscapeWidgetState()
        let playback = SoundscapePlayback(library: library, defaults: defaults, publish: { state = $0 })
        playback.player.isMuted = true
        defer { playback.pause() }
        var evidence: [[String: Any]] = []
        func record(_ event: String) throws {
            playback.publishWidgetState()
            evidence.append(["event": event, "selectedID": state.selectedID ?? "none",
                             "title": state.title, "position": state.position ?? -1,
                             "origin": state.origin, "video": state.video,
                             "rate": playback.player.rate, "repeat": state.repeatMode,
                             "url": playback.videoURL?.absoluteString ?? "none",
                             "item": playback.player.currentItem.map { String(describing: ObjectIdentifier($0)) } ?? "none"])
            try JSONSerialization.data(withJSONObject: evidence, options: [.prettyPrinted, .sortedKeys])
                .write(to: URL(fileURLWithPath: args[4]), options: .atomic)
        }
        func advancing(_ index: Int) async throws {
            try await SoundscapeAlbumTests.wait("Watch did not play exact video") {
                playback.publishWidgetState()
                if let error = playback.player.currentItem?.error { throw error }
                return playback.errorMessage == nil && state.selectedID == tracks[index].id &&
                    state.title == tracks[index].title && state.video && playback.showingDock &&
                    !playback.showingPicker && playback.videoURL == tracks[index].url &&
                    playback.player.rate > 0 && playback.player.currentTime().seconds > 0.2
            }
        }
        playback.openVideo(trackID: tracks[0].id)
        try await advancing(0)
        try record("cold-exact-A")
        let item = playback.player.currentItem
        let before = playback.player.currentTime().seconds
        playback.openVideo(trackID: tracks[0].id)
        try SoundscapeAlbumTests.require(playback.player.currentItem === item, "warm Watch replaced item")
        try SoundscapeAlbumTests.require(playback.player.currentTime().seconds >= before - 0.1, "warm Watch reset position")
        try record("warm-same-item-position")
        try await playback.selectApp(tracks[1].id)
        playback.openVideo(trackID: tracks[0].id)
        try await advancing(0)
        try record("stale-A-over-app-B")
        playback.openVideo(trackID: "unknown-fixture-id")
        try await SoundscapeAlbumTests.wait("unknown did not fail closed") { playback.errorMessage != nil }
        try SoundscapeAlbumTests.require(playback.player.rate == 0 && !playback.isPlaying, "unknown played another title")
        try record("unknown-stopped")
        playback.openVideo(trackID: tracks[0].id)
        playback.openVideo(trackID: tracks[1].id)
        try await advancing(1)
        try record("concurrent-last-B")
        playback.pause()
        try SoundscapeAlbumTests.require(playback.repeatMode == "Off", "Watch changed repeat mode")
        try record("PASS")
    }
}
