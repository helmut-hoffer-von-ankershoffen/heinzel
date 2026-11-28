import Foundation
import AVFoundation

struct ProofFailure: Error, CustomStringConvertible { let description: String }
func require(_ ok: Bool, _ message: String) throws {
    if !ok { throw ProofFailure(description: message) }
}
@MainActor
func emit(_ event: String, _ library: AlbumLibrary) throws {
    let data = try JSONSerialization.data(withJSONObject: ["event": event, "pid": ProcessInfo.processInfo.processIdentifier, "snapshot": library.snapshot()], options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
}
@main struct SoundscapeAlbumFixture {
    @MainActor static func main() async {
        do { try await run() }
        catch { fputs("FAIL: \(error)\n", stderr); exit(1) }
    }
    @MainActor static func run() async throws {
        let args = CommandLine.arguments
        try require(args.count == 4, "usage: fixture MODE ROOT CATALOG_URL")
        try testApprovedAlbumFilmRanges()
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil
        config.httpCookieStorage = nil
        config.urlCredentialStorage = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.connectionProxyDictionary = [:]
        config.timeoutIntervalForRequest = 8
        config.timeoutIntervalForResource = 20
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let root = URL(fileURLWithPath: args[2])
        let suiteFile = root.deletingLastPathComponent().appendingPathComponent("defaults-suite.txt")
        let suite = try String(contentsOf: suiteFile, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
        guard let defaults = UserDefaults(suiteName: suite) else { throw ProofFailure(description: "suite unavailable") }
        var states: [SoundscapeWidgetState] = []
        let library = AlbumLibrary(root: URL(fileURLWithPath: args[2]), catalogURL: URL(string: args[3])!, session: session)
        func saved() throws -> [AlbumPlayableTrack] { try library.savedTracks() }
        func waitCount(_ count: Int) async throws {
            for _ in 0..<200 {
                if try saved().count == count { return }
                try await Task.sleep(for: .milliseconds(100))
            }
            try emit("timeout", library)
            throw ProofFailure(description: "saved count did not reach \(count)")
        }
        switch args[1] {
        case "volume":
            try await waitCount(2)
            try await SoundscapeVolumeTests.run(library: library, defaults: defaults)
            try emit("verified-queue-volume-and-seek", library)
        case "download":
            let playback = SoundscapePlayback(library: library, defaults: defaults, publish: { states.append($0) })
            playback.player.isMuted = true
            defer { playback.pause() }
            try await playback.playAlbum()
            let tracks = try await library.playableTracks()
            try require((playback.player.currentItem?.asset as? AVURLAsset)?.url.isFileURL == false, "cold first Play did not use remote stream")
            try require(playback.currentTitle == tracks[0].title, "cold app Play title wrong")
            try await SoundscapeAlbumTests.wait("cold stream did not decode") {
                if let failure = playback.player.currentItem?.error { throw failure }
                return playback.player.currentTime().seconds > 0.1
            }
            // Widget toggle must pause the app stream before requiring saved media.
            try playback.toggle()
            try require(!playback.isPlaying && playback.player.rate == 0, "widget pause left app stream playing")
            try await Task.sleep(for: .milliseconds(200))
            try require(!playback.isPlaying && playback.player.rate == 0, "queued observer undid widget pause")
            try await playback.toggleApp()
            try await SoundscapeAlbumTests.wait("app stream did not resume") {
                playback.isPlaying && playback.player.rate > 0
            }
            playback.pause()
            try require(tracks.count == 2, "initial playable queue must contain two tracks")
            try await waitCount(2)
            try await SoundscapeAlbumTests.wait("entire album never ready") { library.snapshot()["phase"] as? String == "ready" }
            try emit("download-complete", library)
        case "copied-controls", "copied-cancel":
            try await waitCount(2)
            let completed = try saved().map(\.url)
            let bytes = try completed.map { try Data(contentsOf: $0) }
            if args[1] == "copied-controls" { try library.pauseDownloads() }
            else { try library.cancelDownloads() }
            try require(try saved().map(\.url) == completed, "copied stop lost verified files")
            try require(try completed.map { try Data(contentsOf: $0) } == bytes, "copied stop changed bytes")
            try emit(args[1], library)
        case "rapid-resume":
            _ = try await library.playableTracks()
            try await waitCount(1)
            try library.pauseDownloads()
            try library.resumeDownloads()
            try await waitCount(2)
            try await SoundscapeAlbumTests.wait("rapid resume never restored ready") {
                library.snapshot()["phase"] as? String == "ready"
            }
            try emit("rapid-resume-ready", library)
        case "controls":
            _ = try await library.playableTracks()
            try await waitCount(1)
            try library.pauseDownloads()
            let completed = try saved().map(\.url)
            try require(completed.count == 1, "pause must occur with one completed file")
            try await Task.sleep(for: .seconds(4))
            try require(try saved().map(\.url) == completed, "pause allowed completion")
            try emit("paused-retained", library)
            try library.resumeDownloads()
            try await waitCount(2)
            try emit("resumed-complete", library)
        case "cancel":
            _ = try await library.playableTracks()
            try await waitCount(1)
            let completed = try saved().map(\.url)
            let bytes = try completed.map { try Data(contentsOf: $0) }
            try library.cancelDownloads()
            try await Task.sleep(for: .seconds(4))
            try require(try saved().map(\.url) == completed, "cancel did not retain only completed files")
            try require(try completed.map { try Data(contentsOf: $0) } == bytes, "cancel modified completed bytes")
            try emit("cancel-retained", library)
        case "cancel-read":
            try await waitCount(1)
            try require(library.snapshot()["phase"] as? String == "cancelled", "cancel intent lost after restart")
            _ = try await library.playableTracks()
            try await Task.sleep(for: .seconds(4))
            try require(try saved().count == 1, "restart resumed cancelled downloads")
            try emit("cancel-durable", library)
        case "offline":
            defaults.removeObject(forKey: "soundscapePlayback")
            try await SoundscapeAlbumTests.offline(library: library, defaults: defaults)
            try emit("offline-expanded-player", library)
        case "selection-write", "selection-read":
            try await waitCount(2)
            let tracks = try saved()
            let playback = SoundscapePlayback(library: library, defaults: defaults, publish: { states.append($0) })
            playback.player.isMuted = true
            defer { playback.pause() }
            if args[1] == "selection-write" {
                try playback.select(tracks[1].id)
                playback.pause()
                defaults.synchronize()
            } else {
                try require(playback.currentTitle == tracks[1].title, "restart did not restore selected title")
                try playback.playSavedAlbum()
                playback.openVideo()
                try require(playback.videoURL == tracks[1].url, "restart did not restore selected cached file")
            }
            try emit(args[1], library)
        case "pre-pause", "pre-cancel", "pre-read":
            if args[1] != "pre-read" {
                let request = Task { try await library.playableTracks() }
                try await Task.sleep(for: .milliseconds(200))
                if args[1] == "pre-pause" { try library.pauseDownloads() }
                else { try library.cancelDownloads() }
                let playable = try await request.value
                try require(try saved().isEmpty && playable.count == 2, "transfer stop interrupted catalog playback or downloaded assets")
            } else {
                let phase = library.snapshot()["phase"] as? String
                try require(phase == "paused" || phase == "cancelled", "pre-manifest intent lost on restart")
                _ = try await library.playableTracks()
                try await Task.sleep(for: .seconds(4))
                try require(try saved().isEmpty, "pre-manifest stop started downloads after restart")
                try require(library.snapshot()["phase"] as? String == phase, "manifest overwrote durable intent")
            }
            try emit(args[1], library)
        default: throw ProofFailure(description: "unknown mode")
        }
    }
}
