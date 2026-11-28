import AVKit
import Combine
import SwiftUI

struct SoundDock: View {
    @Bindable var playback: SoundscapePlayback
    var availableSize = CGSize(width: 600, height: 800)
    @Environment(\.dismiss) private var dismiss
    @State private var error: String?
    @State private var tracks: [AlbumPlayableTrack] = []
    @State private var phase = "idle"
    @State private var verified = 0
    @State private var total = 0
    @State private var verifiedBytes = 0
    @State private var totalBytes = 0
    @State private var videoAspect: CGFloat = 16 / 9

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    if playback.videoURL != nil {
                        let videoSize = SoundDockGeometry.videoSize(
                            width: SoundDockGeometry.width(requested: playback.dockWidth, available: availableSize.width) - 32,
                            height: availableSize.height, aspect: videoAspect
                        )
                        ZStack {
                            Color.black
                            if playback.isResuming {
                                ProgressView().tint(.white).accessibilityLabel("Resuming video")
                            } else {
                                SoundscapeNativeVideo(player: playback.player)
                            }
                        }
                        .frame(width: videoSize.width, height: videoSize.height)
                        .frame(maxWidth: .infinity)
                    }
                    Link("Latent Heinzels", destination: SoundscapeLink.artist)
                    Button { playback.showingPicker.toggle() } label: {
                        HStack {
                            Text(playback.currentTitle).font(.headline)
                            Image(systemName: "chevron.down")
                        }
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .accessibilityHint("Choose a saved track")
                    Text("Dreaming of Electric Snow").font(.headline)
                    if let message = error ?? playback.errorMessage {
                        Text(message).foregroundStyle(.red).accessibilityAddTraits(.updatesFrequently)
                    }
                    if total > 0 && verified == total && phase == "ready" {
                        Label("Offline", systemImage: "checkmark.circle")
                    } else if total > 0 {
                        Text("\(verified) of \(total) files saved · \(phase)")
                        if totalBytes > 0 {
                            ProgressView(value: Double(verifiedBytes), total: Double(totalBytes))
                                .accessibilityLabel("Saved album download")
                        }
                        if verified < total {
                            HStack {
                                if phase == "downloading" {
                                    Button("Pause download") { transfer(.pause) }
                                }
                                if ["paused", "cancelled", "failed"].contains(phase) {
                                    Button("Resume download") { transfer(.resume) }
                                }
                                if ["downloading", "paused", "failed"].contains(phase) {
                                    Button("Cancel download", role: .destructive) { transfer(.cancel) }
                                }
                            }
                        }
                    }
                    HStack {
                        Button("Previous", systemImage: "backward.end.fill") {
                            performAsync { try await playback.previousApp() }
                        }
                        Button(playback.isPlaying ? "Pause" : "Play",
                               systemImage: playback.isPlaying ? "pause.fill" : "play.fill") {
                            performAsync { try await playback.toggleApp() }
                        }
                        Button("Next", systemImage: "forward.end.fill") {
                            performAsync { try await playback.nextApp() }
                        }
                    }
                    Button("Repeat: \(playback.repeatMode)", systemImage: "repeat") { playback.cycleRepeat() }
                    Button("Watch", systemImage: "video") { playback.openVideo() }
                    if playback.showingPicker {
                        if tracks.isEmpty { Text("No tracks available.") }
                        ForEach(tracks, id: \.id) { track in
                            Button(track.title) { performAsync { try await playback.selectApp(track.id) } }
                                .frame(minHeight: 44)
                        }
                    }
                }
                .buttonStyle(SoundDockButtonStyle())
                .padding()
            }
            .navigationTitle("Sound")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .frame(width: SoundDockGeometry.width(requested: playback.dockWidth, available: availableSize.width))
        .frame(minHeight: min(350, max(1, availableSize.height)), maxHeight: max(1, availableSize.height))
        .task { refresh() }
        .onReceive(playback.player.publisher(for: \.currentItem).map { item -> AnyPublisher<CGSize, Never> in
            guard let item else { return Just(.zero).eraseToAnyPublisher() }
            return item.publisher(for: \.presentationSize).prepend(.zero).eraseToAnyPublisher()
        }.switchToLatest()) { size in
            // Reset on item replacement; presentation size includes the selected movie's orientation.
            videoAspect = SoundDockGeometry.aspect(size)
        }
        .task(id: playback.showingPicker) {
            guard playback.showingPicker else { return }
            do { tracks = try await AlbumLibrary.shared.catalogTracks() }
            catch { self.error = error.localizedDescription }
        }
        .onReceive(NotificationCenter.default.publisher(for: AlbumLibrary.progress)) { _ in refresh() }
        .onReceive(NotificationCenter.default.publisher(for: AlbumLibrary.changed)) { _ in refresh() }
    }

    private func performAsync(_ action: @escaping () async throws -> Void) {
        Task { @MainActor in
            do { try await action(); error = nil }
            catch { self.error = error.localizedDescription }
            refresh()
        }
    }

    private func transfer(_ action: SoundscapeTransferAction) {
        perform { try action.apply(to: AlbumLibrary.shared) }
    }

    private func perform(_ action: () throws -> Void) {
        do { try action(); error = nil }
        catch { self.error = error.localizedDescription }
        refresh()
    }

    private func refresh() {
        let state = AlbumLibrary.shared.snapshot()
        phase = state["phase"] as? String ?? "unavailable"
        verified = state["verified"] as? Int ?? 0
        total = state["total"] as? Int ?? 0
        verifiedBytes = state["receivedBytes"] as? Int ?? 0
        totalBytes = state["totalBytes"] as? Int ?? 0
        if !playback.showingPicker { tracks = (try? playback.savedTracks()) ?? [] }
        if let message = state["error"] as? String { error = message }
    }
}

private struct SoundDockButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(minWidth: 44, minHeight: 44)
            .contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.6 : 1)
    }
}

// Native controls supply seeking; aspect fit preserves portrait and widescreen movies.
#if os(macOS)
private struct SoundscapeNativeVideo: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.controlsStyle = .inline
        view.videoGravity = .resizeAspect
        view.player = player
        return view
    }
    func updateNSView(_ view: AVPlayerView, context: Context) { view.player = player }
}
#else
private struct SoundscapeNativeVideo: UIViewControllerRepresentable {
    let player: AVPlayer
    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let view = AVPlayerViewController()
        view.showsPlaybackControls = true
        view.videoGravity = .resizeAspect
        view.player = player
        return view
    }
    func updateUIViewController(_ view: AVPlayerViewController, context: Context) { view.player = player }
}
#endif
