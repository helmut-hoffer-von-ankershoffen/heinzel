import Foundation

@main
struct LocalServiceProbe {
    @MainActor static func main() async throws {
        let service = LocalService()
        guard LocalService.attachArguments == ["ui", "attach"] else { throw ProbeFailure.spawnsAServer }
        if CommandLine.arguments.last == "continuity" {
            let first = try await service.start()
            let instance = try await first.readLifecycle().instance
            let pid = try String(contentsOfFile: "service.pid", encoding: .utf8)
            let remote = Endpoint("https://wren.example")!
            for destination: Endpoint? in [nil, remote] {
                service.selectWorkspace(destination)
                guard await service.runningBackend()?.instance == instance else { throw ProbeFailure.serviceStopped }
                let next = try await service.start(monitoring: false)
                guard next == first, try await next.readLifecycle().instance == instance,
                      try String(contentsOfFile: "service.pid", encoding: .utf8) == pid,
                      let process = Int32(pid), kill(process, 0) == 0 else { throw ProbeFailure.serviceStopped }
                service.selectWorkspace(next)
            }
            service.selectWorkspace(nil)
            return
        }
        if CommandLine.arguments.last == "page-authorization" {
            let endpoint = try await service.start()
            var losses = 0
            service.onLost = { losses += 1 }
            service.reportPageIssue(.serviceUnavailable, for: endpoint)
            guard losses == 0, service.recovery == nil else { throw ProbeFailure.falseLoss }
            for (issue, state) in [(WorkspaceProblem.signIn, LocalService.Recovery.signIn), (.denied, .denied)] {
                service.selectWorkspace(endpoint)
                let before = losses
                service.reportPageIssue(issue, for: endpoint)
                service.reportPageIssue(issue, for: endpoint)
                guard losses == before + 1, service.recovery == state,
                      service.publishingSession == nil else { throw ProbeFailure.lossUnnoticed }
            }
            service.selectWorkspace(nil)
            service.reportPageIssue(.signIn, for: endpoint)
            guard losses == 2, service.recovery == nil else { throw ProbeFailure.falseLoss }
            try await expectFixture(endpoint)
            return
        }
        if CommandLine.arguments.last == "lifecycle-errors" {
            let endpoint = try await service.start()
            let expected: [ConnectionError] = [.authentication, .accessDenied, .unverifiedWorkspace, .unverifiedWorkspace]
            for refusal in expected {
                do {
                    try await endpoint.verify()
                    throw ProbeFailure.unexpectedLifecycle
                } catch let error as ConnectionError where error == refusal {
                    continue
                }
            }
            try await expectFixture(endpoint)
            service.selectWorkspace(nil)
            return
        }
        if CommandLine.arguments.last == "prepare" {
            service.probeInterval = .milliseconds(25)
            let endpoint = try await service.start(monitoring: false)
            let count = { try String(contentsOfFile: "probes", encoding: .utf8).split(separator: "\n").count }
            try await Task.sleep(for: .milliseconds(160))
            guard try count() == 1 else { throw ProbeFailure.unexpectedLifecycle }
            service.selectWorkspace(endpoint)
            let deadline = ContinuousClock.now + .seconds(2)
            while try count() == 1 {
                guard ContinuousClock.now < deadline else { throw ProbeFailure.unexpectedLifecycle }
                try await Task.sleep(for: .milliseconds(10))
            }
            service.selectWorkspace(nil)
            try await expectFixture(endpoint)
            return
        }
        if CommandLine.arguments.last == "outdated" {
            do {
                _ = try await service.start()
            } catch ConnectionError.outdatedCLI {
                return
            }
            throw ProbeFailure.unexpectedStartup
        }
        if CommandLine.arguments.last == "flaky" {
            // The service fails two probes, then answers again: below the threshold of three, so no loss.
            service.probeInterval = .milliseconds(100)
            var lost = false
            service.onLost = { lost = true }
            let endpoint = try await service.start()
            try await Task.sleep(for: .milliseconds(1500))
            guard !lost else { throw ProbeFailure.falseLoss }
            try await expectFixture(endpoint)
            service.selectWorkspace(nil)
            return
        }
        if CommandLine.arguments.last == "overlap" {
            // A re-attach and the person's click at once share one attach.
            async let first = service.start()
            async let second = service.start()
            let (a, b) = try await (first, second)
            guard a.origin == b.origin else { throw ProbeFailure.unexpectedLifecycle }
            service.selectWorkspace(nil)
            return
        }
        if CommandLine.arguments.last == "backoff" {
            try await exerciseRecovery(service)
            return
        }
        if CommandLine.arguments.last == "lost" {
            try await reattachAfterLoss(service)
            return
        }
        if CommandLine.arguments.last == "refuse" {
            do {
                _ = try await service.start()
            } catch ConnectionError.startup {
                return
            }
            throw ProbeFailure.unexpectedStartup
        }
        let endpoint: Endpoint
        do {
            endpoint = try await service.start()
            try await expectFixture(endpoint)
        } catch {
            throw ProbeFailure.unexpectedLifecycle
        }
        // Leaving the workspace never ends the owned service: it still answers afterwards.
        service.selectWorkspace(nil)
        do {
            try await expectFixture(endpoint)
        } catch {
            throw ProbeFailure.serviceStopped
        }
    }

    private enum Event: Sendable {
        case wait(Duration, CheckedContinuation<Void, Never>)
        case probe(CheckedContinuation<Endpoint.Lifecycle, any Error>)
    }

    /// Drive the production monitor one suspension at a time, including deliberately late completions.
    @MainActor private static func exerciseRecovery(_ service: LocalService) async throws {
        let endpoint = try await service.start(monitoring: false)
        let healthy = try await endpoint.readLifecycle()
        let events = AsyncStream<Event>.makeStream()
        var iterator = events.stream.makeAsyncIterator()
        service.wait = { duration in
            await withCheckedContinuation { events.continuation.yield(.wait(duration, $0)) }
        }
        service.probe = { target in
            guard target == endpoint else { throw ProbeFailure.unexpectedLifecycle }
            return try await withCheckedThrowingContinuation { events.continuation.yield(.probe($0)) }
        }
        service.retryDelay = { .seconds([5, 10, 20, 40][min($0, 3)]) }
        var pageChecks = 0
        service.verifyRecoveredPage = { target in
            precondition(target == endpoint)
            pageChecks += 1
        }
        var losses = 0
        var recoveries = 0
        service.onLost = { losses += 1 }
        service.onRecovered = { target in
            precondition(target == endpoint)
            recoveries += 1
        }
        func clock(_ expected: Duration) async throws -> CheckedContinuation<Void, Never> {
            guard case .wait(let time, let completion) = await iterator.next(isolation: MainActor.shared),
                  time == expected else { throw ProbeFailure.backoff }
            return completion
        }
        func nextProbe() async throws -> CheckedContinuation<Endpoint.Lifecycle, any Error> {
            guard case .probe(let completion) = await iterator.next(isolation: MainActor.shared) else {
                throw ProbeFailure.unexpectedLifecycle
            }
            return completion
        }
        func miss() async throws {
            try await nextProbe().resume(throwing: URLError(.cannotConnectToHost))
        }

        service.selectWorkspace(endpoint)
        for _ in 0..<3 {
            let tick = try await clock(.seconds(5))
            guard losses == 0 else { throw ProbeFailure.falseLoss }
            tick.resume()
            try await miss()
        }
        let stale = try await nextProbe() // No second grace interval after confirmed loss.
        guard losses == 1, service.recovery == .reconnecting, service.publishingSession == nil,
              !service.shouldAttachOnSelection() else { throw ProbeFailure.lossUnnoticed }
        service.pauseRecovery()
        service.setSleeping(true)
        service.setForeground(false)
        stale.resume(returning: healthy)
        service.setSleeping(false)
        service.setForeground(true)
        await Task.yield()
        guard service.recovery == .paused, service.publishingSession == nil, recoveries == 0 else { throw ProbeFailure.backoff }

        guard !service.shouldAttachOnSelection() else { throw ProbeFailure.backoff }
        try await miss()
        for seconds in [5, 10, 20] {
            try await clock(.seconds(seconds)).resume()
            try await miss()
        }
        let delayed = try await clock(.seconds(40))
        service.setSleeping(true)
        delayed.resume()
        service.setSleeping(false)
        try await nextProbe().resume(returning: healthy)
        let normal = try await clock(.seconds(5))
        guard service.recovery == nil, service.publishingSession == endpoint, recoveries == 1, losses == 1, pageChecks == 1 else { throw ProbeFailure.backoff }

        // Cancellation must also win after the HTTP read succeeds, while WebKit is answering.
        service.lostAfter = 1
        normal.resume()
        try await miss()
        let pageReply = AsyncStream<CheckedContinuation<Void, Never>>.makeStream()
        var pages = pageReply.stream.makeAsyncIterator()
        service.verifyRecoveredPage = { _ in
            await withCheckedContinuation { pageReply.continuation.yield($0) }
        }
        try await nextProbe().resume(returning: healthy)
        guard let reply = await pages.next(isolation: MainActor.shared) else { throw ProbeFailure.backoff }
        service.selectWorkspace(Endpoint("https://wren.example"))
        reply.resume()
        await Task.yield()
        guard recoveries == 1 else { throw ProbeFailure.backoff }

        // A missing retained view is retryable, and a late page result cannot restore a deselected workspace.
        service.verifyRecoveredPage = nil
        service.selectWorkspace(endpoint)
        try await clock(.seconds(5)).resume()
        try await nextProbe().resume(throwing: ConnectionError.authentication)
        let retry = try await nextProbe()
        guard service.recovery == .unavailable else { throw ProbeFailure.lossUnnoticed }
        service.verifyRecoveredPage = { _ in
            await withCheckedContinuation { pageReply.continuation.yield($0) }
        }
        retry.resume(throwing: ConnectionError.authentication)
        guard let latePairing = await pages.next(isolation: MainActor.shared) else { throw ProbeFailure.backoff }
        service.selectWorkspace(nil)
        latePairing.resume()
        await Task.yield()
        guard recoveries == 1, service.publishingSession == endpoint else { throw ProbeFailure.backoff }

        service.verifyRecoveredPage = { _ in throw ConnectionError.authentication }
        for (error, phase) in [(ConnectionError.authentication, LocalService.Recovery.signIn), (.accessDenied, .denied)] {
            service.selectWorkspace(endpoint)
            try await clock(.seconds(5)).resume()
            try await nextProbe().resume(throwing: error)
            while service.recovery == nil { await Task.yield() }
            guard service.recovery == phase else { throw ProbeFailure.backoff }
        }
        service.selectWorkspace(endpoint)
        try await clock(.seconds(5)).resume()
        try await miss()
        let signedOut = try await nextProbe()
        service.selectWorkspace(nil)
        signedOut.resume(returning: healthy)
        await Task.yield()
        guard recoveries == 1, service.recovery == nil else { throw ProbeFailure.backoff }

        // A fast rotation needs no chooser. The retained tab then owns regular checks
        // and its authority never becomes a native publishing credential.
        let previousLosses = losses
        var pairedReads = 0
        service.verifyRecoveredPage = { target in
            precondition(target == endpoint)
            pairedReads += 1
        }
        service.selectWorkspace(endpoint)
        try await clock(.seconds(5)).resume()
        try await nextProbe().resume(throwing: ConnectionError.authentication)
        try await clock(.seconds(5)).resume()
        let next = try await clock(.seconds(5))
        guard losses == previousLosses, recoveries == 2, pairedReads == 2,
              service.publishingSession == nil, service.recovery == nil else { throw ProbeFailure.backoff }
        service.selectWorkspace(nil)
        next.resume()
        let renewed = try await service.start(monitoring: false)
        guard renewed.origin == endpoint.origin, renewed != endpoint, service.publishingSession == renewed
            else { throw ProbeFailure.unexpectedLifecycle }
        try await expectFixture(renewed)
    }

    /// The service crashes (the probe kills the fixture's service); the app notices and attaches again.
    @MainActor static func reattachAfterLoss(_ service: LocalService) async throws {
        service.probeInterval = .milliseconds(100)
        var lost = false
        service.onLost = { lost = true }
        let first = try await service.start()
        try await expectFixture(first)
        guard let text = try? String(contentsOfFile: "service.pid", encoding: .utf8), let pid = Int32(text) else {
            throw ProbeFailure.unexpectedLifecycle
        }
        kill(pid, SIGKILL)
        for _ in 0..<50 where !lost { try await Task.sleep(for: .milliseconds(100)) }
        guard lost else { throw ProbeFailure.lossUnnoticed }
        let second = try await service.start()
        try await expectFixture(second)
        guard second.origin != first.origin else { throw ProbeFailure.unexpectedLifecycle }
        service.selectWorkspace(nil)
    }

    @MainActor static func expectFixture(_ endpoint: Endpoint) async throws {
        let state = try await endpoint.readLifecycle()
        guard state.instance == "fixture", state.version == "1.0", state.restartable == true else {
            throw ProbeFailure.unexpectedLifecycle
        }
    }

    enum ProbeFailure: Error {
        case spawnsAServer, unexpectedStartup, unexpectedLifecycle, serviceStopped, lossUnnoticed, falseLoss, backoff
    }
}
