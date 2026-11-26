import Foundation

/// Tracks the current WebKit navigation without mistaking a delayed callback for a replaced page.
private final class WeakNavigation<Navigation: AnyObject> {
    weak var value: Navigation?
    init(_ value: Navigation) { self.value = value }
}

struct NavigationIdentity<Navigation: AnyObject> {
    private(set) var generation = 0
    private(set) var current: Navigation?
    private(set) var expectedURL: URL?
    private(set) var awaitingStart = false
    private(set) var inFlight = false
    private var superseded: [WeakNavigation<Navigation>] = []

    private mutating func retire(_ navigation: Navigation?) {
        guard let navigation else { return }
        superseded.removeAll { $0.value == nil }
        if !superseded.contains(where: { $0.value === navigation }) {
            superseded.append(WeakNavigation(navigation))
        }
    }

    private func wasSuperseded(_ navigation: Navigation) -> Bool {
        superseded.contains { $0.value === navigation }
    }

    mutating func begin(target: URL?) -> Int {
        retire(current)
        generation += 1
        current = nil
        expectedURL = target
        awaitingStart = true
        inFlight = true
        return generation
    }

    mutating func attach(_ navigation: Navigation?, to attempt: Int) {
        guard attempt == generation else { return }
        current = navigation
    }

    mutating func cancel(_ attempt: Int) {
        guard attempt == generation else { return }
        retire(current)
        generation += 1
        current = nil
        expectedURL = nil
        awaitingStart = false
        inFlight = false
    }

    func expects(_ url: URL?) -> Bool { awaitingStart && expectedURL == url }

    mutating func start(_ navigation: Navigation?, url: URL?) -> Bool {
        superseded.removeAll { $0.value == nil }
        if let navigation, wasSuperseded(navigation) { return false }
        if navigation == nil {
            guard inFlight, expectedURL == url else { return false }
            if current != nil {
                awaitingStart = false
                return true
            }
            guard expects(url) else { return false }
        }
        if awaitingStart, let expectedURL, let url, expectedURL != url { return false }
        if let current {
            // An action-returned WKNavigation is already authoritative. An unrelated
            // provisional start cannot replace it while WebKit callbacks are pending.
            guard navigation === current else { return false }
        }
        current = navigation
        expectedURL = url ?? expectedURL
        awaitingStart = false
        inFlight = true
        return true
    }

    func acceptsCompletion(_ navigation: Navigation?, url: URL?) -> Bool {
        if let navigation { return current === navigation && !wasSuperseded(navigation) && inFlight }
        return expectedURL == url && !awaitingStart && inFlight
    }

    mutating func finish(_ navigation: Navigation?, url: URL?) -> Bool {
        guard acceptsCompletion(navigation, url: url) else { return false }
        inFlight = false
        return true
    }

    mutating func finishSameDocument(url: URL?) -> Bool {
        guard inFlight, expectedURL == url else { return false }
        awaitingStart = false
        inFlight = false
        return true
    }
}
