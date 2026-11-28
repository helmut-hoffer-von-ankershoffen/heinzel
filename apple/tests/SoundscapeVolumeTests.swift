import AVFoundation
import Foundation

/// Run with an isolated, verified fixture library; never uses the product singleton.
@MainActor
struct SoundscapeVolumeTests {
    static func run(library: AlbumLibrary, defaults: UserDefaults) async throws {
        let queue = AVQueuePlayer()
        let playback = SoundscapePlayback(library: library, defaults: defaults,
                                          player: queue, publish: { _ in })
        defer { playback.pause() }
        func require(_ condition: Bool) throws {
            if !condition { throw SoundscapeAlbumTests.Failure(description: "Volume and transport diverged") }
        }
        try playback.setVolume(0.65)
        try playback.playSavedAlbum()
        try require(abs(queue.volume - 0.65) < 0.0001 && playback.isPlaying == (queue.rate != 0))
        let rate = queue.rate
        try playback.setVolume(0)
        try require(queue.volume == 0 && queue.isMuted && queue.rate == rate && playback.effectiveGain == 0)
        try playback.setVolume(0.4)
        try require(abs(queue.volume - 0.4) < 0.0001 && !queue.isMuted && queue.rate == rate)
        playback.pause()
        try playback.setVolume(0.65)
        try require(queue.rate == 0 && !playback.isPlaying && playback.effectiveGain == 0)
        try playback.playSavedAlbum()
        try require(playback.isPlaying == (queue.rate != 0) && abs(queue.volume - 0.65) < 0.0001)
        playback.pause()
        try await SoundscapeAlbumTests.wait("local track duration unavailable") {
            playback.albumDuration != nil && queue.currentItem?.status == .readyToPlay
        }
        guard let selected = playback.selectedID else {
            throw SoundscapeAlbumTests.Failure(description: "No selected track to seek")
        }
        let stale = await playback.seekSelectedTrack("stale-track", to: 0.75)
        let sought = await playback.seekSelectedTrack(selected, to: 0.75)
        if stale || !sought || abs(queue.currentTime().seconds - 0.75) > 0.05 || queue.rate != 0 {
            throw SoundscapeAlbumTests.Failure(description: "Seek did not move the paused queue to the selected track position")
        }
        let other = try library.savedTracks().first { $0.id != selected }
        guard let other else { throw SoundscapeAlbumTests.Failure(description: "Missing widget fixture track") }
        await playback.openWidgetTrack(selected)
        try require(playback.selectedID == selected && abs(queue.currentTime().seconds - 0.75) <= 0.05 && queue.rate == 0)
        let coldQueue = AVQueuePlayer()
        let cold = SoundscapePlayback(library: library, defaults: defaults, player: coldQueue, publish: { _ in })
        await cold.openWidgetTrack(other.id)
        try require(cold.selectedID == other.id && cold.showingDock && !cold.showingPicker && coldQueue.rate == 0)
        await cold.openWidgetTrack("missing-fixture-track")
        try require(cold.selectedID == other.id && cold.showingPicker && coldQueue.rate == 0)

        for invalid in [Double.nan, .infinity, -0.1, 1.1] {
            do {
                try playback.setVolume(invalid)
                throw SoundscapeAlbumTests.Failure(description: "Invalid volume accepted")
            } catch SoundscapePlayback.PlaybackError.invalidGain { }
        }
        let restored = SoundscapePlayback(library: library, defaults: defaults,
                                         player: AVQueuePlayer(), publish: { _ in })
        try require(abs(restored.volume - 0.65) < 0.0001 && !restored.isPlaying)
    }
}
