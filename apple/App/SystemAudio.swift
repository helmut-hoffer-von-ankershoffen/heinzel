#if canImport(ScreenCaptureKit)

import AudioToolbox
import CoreMedia
import Foundation
@preconcurrency import ScreenCaptureKit

/// Holds at most a fraction of a second of mono PCM. The workspace reads it locally for analysis.
@available(iOS 27.0, *)
private final class AudioSamples: NSObject, SCStreamOutput, @unchecked Sendable {
    private let lock = NSLock()
    private var bytes = Data()

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, let format = sample.formatDescription,
              let description = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee,
              description.mFormatID == kAudioFormatLinearPCM,
              description.mFormatFlags & kAudioFormatFlagIsFloat != 0,
              description.mChannelsPerFrame == 1, description.mBitsPerChannel == 32,
              description.mSampleRate == 48_000 else { return }
        let frames = CMSampleBufferGetNumSamples(sample)
        guard frames > 0, frames <= 12_000 else { return }
        let count = frames * MemoryLayout<Float>.size
        var packet = Data(count: count)
        let result = packet.withUnsafeMutableBytes { storage in
            var audio = AudioBufferList(
                mNumberBuffers: 1,
                mBuffers: AudioBuffer(mNumberChannels: 1, mDataByteSize: UInt32(count), mData: storage.baseAddress)
            )
            return CMSampleBufferCopyPCMDataIntoAudioBufferList(sample, frameOffset: 0, numFrames: Int32(frames), bufferList: &audio)
        }
        guard result == noErr else { return }
        lock.withLock {
            bytes.append(packet)
            if bytes.count > 48_000 { bytes.removeFirst(bytes.count - 48_000) }
        }
    }

    func drain() -> String {
        lock.withLock {
            let packet = bytes.base64EncodedString()
            bytes.removeAll(keepingCapacity: true)
            return packet
        }
    }
}

@available(iOS 27.0, *)
@MainActor
final class SystemAudio: NSObject, SCContentSharingPickerObserver, SCStreamDelegate {
    static var available: Bool {
        if #available(macOS 27, *) { return SCContentSharingPicker.shared.isAvailable }
        return true
    }

    private static weak var owner: SystemAudio?
    private let picker = SCContentSharingPicker.shared
    private var pending: CheckedContinuation<Bool, Never>?
    private var stream: SCStream?
    private var output: AudioSamples?
    private var revision = 0
    private var pickerTimeout: Task<Void, Never>?

    func start() async -> Bool {
        guard Self.owner == nil else { return false }
        Self.owner = self
        return await withCheckedContinuation { continuation in
            pending = continuation
            picker.add(self)
            picker.isActive = true
            picker.present()
            pickerTimeout = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(60)) } catch { return }
                self?.stop()
            }
        }
    }

    func read() -> String? { output?.drain() }

    func stop() {
        revision += 1
        finishPicker(false)
        let current = stream
        stream = nil
        output = nil
        if Self.owner === self {
            picker.isActive = false
            Self.owner = nil
        }
        if let current { Task { try? await current.stopCapture() } }
    }

    private func finishPicker(_ result: Bool) {
        guard let continuation = pending else { return }
        pending = nil
        pickerTimeout?.cancel()
        pickerTimeout = nil
        picker.remove(self)
        if !result, Self.owner === self {
            picker.isActive = false
            Self.owner = nil
        }
        continuation.resume(returning: result)
    }

    nonisolated func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        Task { @MainActor in self.finishPicker(false) }
    }

    nonisolated func contentSharingPickerStartDidFailWithError(_ error: any Error) {
        Task { @MainActor in self.finishPicker(false) }
    }

    nonisolated func contentSharingPicker(
        _ picker: SCContentSharingPicker, didUpdateWith filter: SCContentFilter, for stream: SCStream?
    ) {
        Task { @MainActor in self.startCapture(filter) }
    }

    private func startCapture(_ filter: SCContentFilter) {
        guard pending != nil, self.stream == nil else { return }
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.sampleRate = 48_000
        configuration.channelCount = 1
        let samples = AudioSamples()
        let capture = SCStream(filter: filter, configuration: configuration, delegate: self)
        self.stream = capture
        let attempt = revision
        Task {
            do {
                try capture.addStreamOutput(samples, type: .audio, sampleHandlerQueue: DispatchQueue(label: "team.heinzel.audio"))
                try await capture.startCapture()
                guard attempt == revision else { try? await capture.stopCapture(); return }
                output = samples
                finishPicker(true)
            } catch {
                if attempt == revision { stop() }
            }
        }
    }

    nonisolated func stream(_ stream: SCStream, didStopWithError error: any Error) {
        let stopped = ObjectIdentifier(stream)
        Task { @MainActor in
            if self.stream.map(ObjectIdentifier.init) == stopped { stop() }
        }
    }
}

#endif
