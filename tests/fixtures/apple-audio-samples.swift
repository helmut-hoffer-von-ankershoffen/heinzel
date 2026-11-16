import CoreAudio
import Foundation

@main
struct SampleProbe {
    static func main() {
        let samples = TapSamples(rate: 8_000)
        func append(_ values: [Float]) {
            values.withUnsafeBytes { bytes in
                var list = AudioBufferList(mNumberBuffers: 1, mBuffers: AudioBuffer(
                    mNumberChannels: 1, mDataByteSize: UInt32(bytes.count),
                    mData: UnsafeMutableRawPointer(mutating: bytes.baseAddress)
                ))
                withUnsafePointer(to: &list) { samples.append($0) }
            }
        }
        func drain() -> [Float] {
            let data = Data(base64Encoded: samples.drain())!
            return data.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
        }
        precondition(!samples.hasReceivedSamples)
        precondition(!TapHealth.isStopped(propertyStatus: -1, running: 0))
        precondition(!TapHealth.isStopped(propertyStatus: kAudioHardwareUnknownPropertyError, running: 0))
        precondition(TapHealth.isStopped(propertyStatus: kAudioHardwareBadDeviceError, running: 1))
        precondition(TapHealth.isStopped(propertyStatus: kAudioHardwareBadObjectError, running: 1))
        precondition(!TapHealth.isStopped(propertyStatus: noErr, running: 1))
        precondition(TapHealth.isStopped(propertyStatus: noErr, running: 0))
        append([0, 0, 0])
        precondition(samples.hasReceivedSamples)
        precondition(drain() == [0, 0, 0])
        precondition(samples.hasReceivedSamples)
        append([0.25, -0.5, 1])
        precondition(drain() == [0.25, -0.5, 1])
        precondition(drain().isEmpty)
        append(Array(repeating: 1, count: 1_500))
        append(Array(repeating: 2, count: 1_000))
        precondition(drain() == Array(repeating: 1, count: 1_000) + Array(repeating: 2, count: 1_000))
        append(Array(repeating: 3, count: 2_500))
        precondition(drain() == Array(repeating: 3, count: 2_000))
    }
}
