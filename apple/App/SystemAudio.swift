import Foundation

/// Serializes teardown with an in-flight start and keeps failed teardown retryable.
@MainActor
final class AudioStopFence {
    private var cleanup: Task<Void, Never>?

    func stop(after start: Task<Bool, Never>?,
              captureStop: @escaping @MainActor () async -> Bool,
              finished: @escaping @MainActor (Bool) -> Void) {
        guard cleanup == nil else { return }
        cleanup = Task {
            let started = await start?.value ?? false
            let stopped = started ? await captureStop() : true
            cleanup = nil
            finished(stopped)
        }
    }
}

#if os(macOS)

import AppKit
import CoreAudio
import Foundation

final class TapSamples: @unchecked Sendable {
    private let lock = NSLock()
    private let capacity: Int
    private let storage: UnsafeMutableRawPointer
    private var offset = 0
    private var buffered = 0
    private var received = false

    var hasReceivedSamples: Bool { lock.withLock { received } }

    init(rate: Double) {
        capacity = Int(rate / 4) * MemoryLayout<Float>.size
        storage = .allocate(byteCount: capacity, alignment: MemoryLayout<Float>.alignment)
    }

    deinit { storage.deallocate() }

    func append(_ input: UnsafePointer<AudioBufferList>) {
        guard input.pointee.mNumberBuffers == 1 else { return }
        let buffer = input.pointee.mBuffers
        let size = Int(buffer.mDataByteSize)
        guard buffer.mNumberChannels == 1,
              size > 0, size.isMultiple(of: MemoryLayout<Float>.size),
              let data = buffer.mData, lock.try() else { return }
        defer { lock.unlock() }
        received = true
        // The Core Audio callback never waits for a reader or allocates a packet.
        let count = min(size, capacity)
        let start = data.advanced(by: size - count)
        let first = min(count, capacity - offset)
        storage.advanced(by: offset).copyMemory(from: start, byteCount: first)
        storage.copyMemory(from: start.advanced(by: first), byteCount: count - first)
        offset = (offset + count) % capacity
        buffered = min(buffered + count, capacity)
    }

    func drain() -> String {
        let packet = lock.withLock { () -> Data in
            guard buffered > 0 else { return Data() }
            let start = (offset - buffered + capacity) % capacity
            let first = min(buffered, capacity - start)
            var packet = Data(bytes: storage.advanced(by: start), count: first)
            packet.append(storage.assumingMemoryBound(to: UInt8.self), count: buffered - first)
            buffered = 0
            return packet
        }
        return packet.base64EncodedString()
    }
}

enum TapHealth {
    // Invalid objects and devices are terminal; other property errors leave health unknown.
    static func isStopped(propertyStatus: OSStatus, running: UInt32) -> Bool {
        propertyStatus == kAudioHardwareBadDeviceError || propertyStatus == kAudioHardwareBadObjectError
            || (propertyStatus == noErr && running == 0)
    }
}

@MainActor
final class SystemAudio {
    static let available = true
    private var tap = AudioObjectID(kAudioObjectUnknown)
    private var device = AudioObjectID(kAudioObjectUnknown)
    private var io: AudioDeviceIOProcID?
    private var samples: TapSamples?
    private weak var consentSheet: NSWindow?
    private(set) var sampleRate = 48_000.0

    func confirm(in window: NSWindow) async -> Bool {
        guard window.attachedSheet == nil else { return false }
        let alert = NSAlert()
        alert.messageText = "Share system audio?"
        alert.informativeText = "This includes sound from other apps. Heinzelmännchen uses it to animate the visualizer while sharing is on."
        alert.addButton(withTitle: "Cancel")
        alert.addButton(withTitle: "Share system audio")
        consentSheet = alert.window
        defer { consentSheet = nil }
        return await alert.beginSheetModal(for: window) == .alertSecondButtonReturn
    }

    func start() async -> Bool {
        // A second start must not overwrite the resources owned by the first one.
        guard tap == kAudioObjectUnknown, device == kAudioObjectUnknown, io == nil else { return false }
        guard let description = tapDescription(), createTap(description), createDevice(description) else {
            stop()
            return false
        }
        let output = TapSamples(rate: sampleRate)
        let status = AudioDeviceCreateIOProcIDWithBlock(&io, device, nil) { @Sendable _, input, _, _, _ in
            output.append(input)
        }
        guard status == noErr, let io, AudioDeviceStart(device, io) == noErr else {
            stop()
            return false
        }
        samples = output
        return true
    }

    var hasReceivedSamples: Bool { samples?.hasReceivedSamples ?? false }

    func read() -> String? {
        guard let samples else { return nil }
        var address = property(kAudioDevicePropertyDeviceIsRunning)
        var running: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        let status = AudioObjectGetPropertyData(device, &address, 0, nil, &size, &running)
        if TapHealth.isStopped(propertyStatus: status, running: running) {
            stop()
            return nil
        }
        return samples.drain()
    }

    func stop() {
        if let sheet = consentSheet, let parent = sheet.sheetParent {
            parent.endSheet(sheet, returnCode: .alertFirstButtonReturn)
        }
        if let io {
            AudioDeviceStop(device, io)
            AudioDeviceDestroyIOProcID(device, io)
            self.io = nil
        }
        if device != kAudioObjectUnknown {
            AudioHardwareDestroyAggregateDevice(device)
            device = kAudioObjectUnknown
        }
        if tap != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tap)
            tap = kAudioObjectUnknown
        }
        samples = nil
    }

    private func tapDescription() -> CATapDescription? {
        #if DEBUG
        // A live test can confine capture to its own sound source. An invalid target fails closed.
        if let value = ProcessInfo.processInfo.environment["HZ_SPIKE_AUDIO_PID"] {
            guard var pid = pid_t(value), pid > 0 else { return nil }
            var address = property(kAudioHardwarePropertyTranslatePIDToProcessObject)
            var process = AudioObjectID(kAudioObjectUnknown)
            var size = UInt32(MemoryLayout<AudioObjectID>.size)
            guard AudioObjectGetPropertyData(
                AudioObjectID(kAudioObjectSystemObject), &address, UInt32(MemoryLayout<pid_t>.size), &pid, &size, &process
            ) == noErr, process != kAudioObjectUnknown else { return nil }
            return CATapDescription(monoMixdownOfProcesses: [process])
        }
        #endif
        return CATapDescription(monoGlobalTapButExcludeProcesses: [])
    }

    private func createTap(_ description: CATapDescription) -> Bool {
        description.name = "Heinzelmännchen System Audio"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        guard AudioHardwareCreateProcessTap(description, &tap) == noErr else { return false }
        var formatAddress = property(kAudioTapPropertyFormat)
        var format = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        guard AudioObjectGetPropertyData(tap, &formatAddress, 0, nil, &size, &format) == noErr,
              format.mFormatID == kAudioFormatLinearPCM,
              format.mFormatFlags & kAudioFormatFlagIsFloat != 0,
              format.mFormatFlags & kAudioFormatFlagIsBigEndian == 0,
              format.mBitsPerChannel == 32,
              format.mChannelsPerFrame == 1, format.mBytesPerFrame == MemoryLayout<Float>.size,
              (8_000...192_000).contains(format.mSampleRate) else { return false }
        sampleRate = format.mSampleRate
        return true
    }

    private func createDevice(_ description: CATapDescription) -> Bool {
        let settings: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Heinzelmännchen System Audio",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: description.uuid.uuidString]],
        ]
        return AudioHardwareCreateAggregateDevice(settings as CFDictionary, &device) == noErr
    }

    private func property(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    }
}

#elseif canImport(ScreenCaptureKit)

import AudioToolbox
import CoreMedia
import Foundation
@preconcurrency import ScreenCaptureKit

/// Holds at most a fraction of a second of mono PCM. The workspace reads it locally for analysis.
@available(iOS 27.0, *)
private final class AudioSamples: NSObject, SCStreamOutput, @unchecked Sendable {
    private let lock = NSLock()
    private var bytes = Data()
    private var received = false

    var hasReceivedSamples: Bool { lock.withLock { received } }

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
        let result = packet.withUnsafeMutableBytes { (storage: UnsafeMutableRawBufferPointer) -> OSStatus in
            var audio = AudioBufferList(
                mNumberBuffers: 1,
                mBuffers: AudioBuffer(mNumberChannels: 1, mDataByteSize: UInt32(count), mData: storage.baseAddress)
            )
            return CMSampleBufferCopyPCMDataIntoAudioBufferList(sample, at: 0, frameCount: Int32(frames), into: &audio)
        }
        guard result == noErr else { return }
        lock.withLock {
            received = true
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
    let sampleRate = 48_000.0
    static var available: Bool { SCContentSharingPicker.shared.isAvailable }

    private static var owner: SystemAudio?
    private let picker = SCContentSharingPicker.shared
    private var pending: CheckedContinuation<Bool, Never>?
    private var stream: SCStream?
    private var output: AudioSamples?
    private var revision = 0
    private var pickerTimeout: Task<Void, Never>?
    private var captureStart: Task<Bool, Never>?
    private let stopFence = AudioStopFence()
    private var didStop = false
    private var tearingDown = false
    private var used = false

    func start() async -> Bool {
        guard !used else { return false }
        guard Self.owner == nil else {
            if Self.owner?.tearingDown == true { Self.owner?.stop() }
            return false
        }
        used = true
        Self.owner = self
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                pending = continuation
                picker.add(self)
                picker.isActive = true
                pickerTimeout = Task { [weak self] in
                    do { try await Task.sleep(for: .seconds(60)) } catch { return }
                    self?.stop()
                }
                picker.present()
            }
        } onCancel: {
            Task { @MainActor in self.stop() }
        }
    }

    func read() -> String? { output?.drain() }
    var hasReceivedSamples: Bool { output?.hasReceivedSamples == true }

    func stop() {
        tearingDown = true
        revision += 1
        finishPicker(false)
        output = nil
        guard let current = stream else {
            releaseOwner()
            return
        }
        stopFence.stop(after: captureStart, captureStop: {
            do { try await current.stopCapture(); return true }
            catch { return self.didStop }
        }, finished: { stopped in
            guard stopped else { return }
            self.stream = nil
            self.captureStart = nil
            self.releaseOwner()
        })
    }

    private func releaseOwner() {
        if Self.owner === self { picker.isActive = false; Self.owner = nil }
    }

    private func finishPicker(_ result: Bool) {
        guard let continuation = pending else { return }
        pending = nil
        pickerTimeout?.cancel()
        pickerTimeout = nil
        picker.remove(self)
        if !result, stream == nil, Self.owner === self {
            picker.isActive = false
            Self.owner = nil
        }
        continuation.resume(returning: result)
    }

    nonisolated func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        Task { @MainActor in
            if self.stream == nil { self.finishPicker(false) }
        }
    }

    nonisolated func contentSharingPickerStartDidFailWithError(_ error: any Error) {
        Task { @MainActor in
            if self.stream == nil { self.finishPicker(false) }
        }
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
        let starting = Task { @MainActor in
            do {
                try capture.addStreamOutput(samples, type: .audio, sampleHandlerQueue: DispatchQueue(label: "team.heinzel.audio"))
                try await capture.startCapture()
                return true
            } catch { return false }
        }
        captureStart = starting
        Task {
            let started = await starting.value
            guard attempt == revision else { return }
            if started {
                output = samples
                finishPicker(true)
            } else { stop() }
        }
    }

    nonisolated func stream(_ stream: SCStream, didStopWithError error: any Error) {
        let stopped = ObjectIdentifier(stream)
        Task { @MainActor in
            if self.stream.map(ObjectIdentifier.init) == stopped { self.didStop = true; stop() }
        }
    }
}

#endif
