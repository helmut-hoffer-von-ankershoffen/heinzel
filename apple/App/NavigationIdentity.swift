import Foundation

/// Tracks the current WebKit navigation without mistaking a delayed callback for a replaced page.
struct NavigationIdentity<Identifier: Hashable> {
    private(set) var generation = 0
    private(set) var current: Identifier?
    private(set) var expectedURL: URL?
    private(set) var awaitingStart = false
    private(set) var inFlight = false
    private var superseded: Set<Identifier> = []

    mutating func begin(target: URL?) -> Int {
        if let current { superseded.insert(current) }
        generation += 1
        current = nil
        expectedURL = target
        awaitingStart = true
        inFlight = true
        return generation
    }

    mutating func attach(_ identifier: Identifier?, to attempt: Int) {
        guard attempt == generation else { return }
        current = identifier
    }

    mutating func cancel(_ attempt: Int) {
        guard attempt == generation else { return }
        if let current { superseded.insert(current) }
        generation += 1
        current = nil
        expectedURL = nil
        awaitingStart = false
        inFlight = false
    }

    func expects(_ url: URL?) -> Bool { awaitingStart && expectedURL == url }

    mutating func start(_ identifier: Identifier?, url: URL?) -> Bool {
        if let identifier, superseded.contains(identifier) { return false }
        if identifier == nil, !expects(url) { return false }
        if let current, current != identifier { superseded.insert(current) }
        current = identifier
        expectedURL = url ?? expectedURL
        awaitingStart = false
        inFlight = true
        return true
    }

    func acceptsCompletion(_ identifier: Identifier?, url: URL?) -> Bool {
        if let identifier { return current == identifier && !superseded.contains(identifier) && inFlight }
        return current == nil && expectedURL == url && !awaitingStart && inFlight
    }

    mutating func finish(_ identifier: Identifier?, url: URL?) -> Bool {
        guard acceptsCompletion(identifier, url: url) else { return false }
        inFlight = false
        return true
    }
}
