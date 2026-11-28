import AVFoundation
import Foundation

/// Run with an isolated, verified fixture library; never uses the product singleton.
@MainActor
struct SoundscapeVolumeTests {
    static func run(library: AlbumLibrary, defaults: UserDefaults) throws {
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
