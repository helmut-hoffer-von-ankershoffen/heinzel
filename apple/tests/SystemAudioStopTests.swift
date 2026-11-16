import Foundation

@main
struct SystemAudioStopTests {
    @MainActor static func waitUntil(_ condition: @MainActor () -> Bool) async {
        for _ in 0..<200 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
        preconditionFailure("Timed out waiting for audio cleanup")
    }

    @MainActor static func main() async {
        let fence = AudioStopFence()
        var startContinuation: CheckedContinuation<Bool, Never>?
        let start = Task { @MainActor in
            await withCheckedContinuation { startContinuation = $0 }
        }
        await waitUntil { startContinuation != nil }
        var stops = 0
        var results: [Bool] = []
        var stopContinuation: CheckedContinuation<Bool, Never>?
        let stop: @MainActor () async -> Bool = {
            stops += 1
            return await withCheckedContinuation { stopContinuation = $0 }
        }
        fence.stop(after: start, captureStop: stop) { results.append($0) }
        fence.stop(after: start, captureStop: stop) { results.append($0) }
        await Task.yield()
        precondition(stops == 0 && results.isEmpty, "stop must await start")
        startContinuation?.resume(returning: true)
        await waitUntil { stopContinuation != nil }
        precondition(stops == 1, "overlapping stops must coalesce")
        stopContinuation?.resume(returning: false)
        await waitUntil { !results.isEmpty }
        precondition(results == [false], "stop failure must remain visible")
        stopContinuation = nil
        fence.stop(after: start, captureStop: stop) { results.append($0) }
        await waitUntil { stopContinuation != nil }
        precondition(stops == 2, "failed cleanup must be retryable")
        stopContinuation?.resume(returning: true)
        await waitUntil { results.count >= 2 }
        precondition(results == [false, true])
        let failedStart = Task { false }
        fence.stop(after: failedStart, captureStop: stop) { results.append($0) }
        await waitUntil { results.count >= 3 }
        precondition(stops == 2 && results == [false, true, true], "failed start needs no stop")
        print("System audio stop lifecycle passed")
    }
}
