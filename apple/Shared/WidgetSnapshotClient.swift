import Foundation

/// The Core endpoint returns a bounded projection, not the full catalog.
struct WidgetSnapshotEnvelope: Decodable {
    let version: Int
    let workspaceID: String
    var generatedAt: String
    let freshForSeconds: Int
    let awake: Int
    let waiting: Int
    let roster: [WidgetHeinzel]
    let rosterCount: Int?
    let openAsks: Int?
    let workspaceDisplayName: String?

    enum CodingKeys: String, CodingKey {
        case version, workspaceID = "workspace_id", generatedAt = "generated_at"
        case freshForSeconds = "fresh_for_seconds", awake, waiting, roster
        case rosterCount = "roster_count", openAsks = "open_asks", workspaceDisplayName = "workspace_display_name"
    }

    var timestamp: Date? {
        guard generatedAt.hasSuffix("Z") || generatedAt.hasSuffix("+00:00") else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: generatedAt) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: generatedAt)
    }

    func valid(now: Date) -> Bool {
        switch version {
        case 1: break
        case 2:
            guard let rosterCount, rosterCount >= roster.count, rosterCount <= 9999,
                  let openAsks, (0...9999).contains(openAsks), openAsks >= waiting,
                  (workspaceDisplayName?.count ?? 0) <= 128,
                  workspaceDisplayName?.rangeOfCharacter(from: .controlCharacters) == nil else { return false }
        default: return false
        }
        guard freshForSeconds == 300, let timestamp,
              timestamp <= now.addingTimeInterval(30), timestamp.addingTimeInterval(300) > now,
              workspaceID.wholeMatch(of: /[a-f0-9]{64}/) != nil,
              (0...9999).contains(awake), (0...9999).contains(waiting), roster.count <= 40,
              Set(roster.map(\.id)).count == roster.count else { return false }
        return roster.allSatisfy {
            NativeRoute.isSessionID($0.id) && !$0.name.isEmpty && $0.name.count <= 128 &&
            $0.name.rangeOfCharacter(from: .controlCharacters) == nil && !$0.state.isEmpty && $0.state.count <= 64 &&
            $0.state.rangeOfCharacter(from: .controlCharacters) == nil &&
            ($0.activity?.count ?? 0) <= 64 && $0.activity?.rangeOfCharacter(from: .controlCharacters) == nil &&
            ($0.asks.map { (0...9999).contains($0) } ?? true) &&
            $0.portrait == nil && $0.nextWake == nil && $0.lastApproved == nil
        }
    }
}

private final class WidgetRejectRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_: URLSession, task _: URLSessionTask,
                    willPerformHTTPRedirection _: HTTPURLResponse, newRequest _: URLRequest,
                    completionHandler: @escaping @Sendable (URLRequest?) -> Void) { completionHandler(nil) }
}

enum WidgetSnapshotOutcome {
    case unavailable
    case received(WidgetSnapshotEnvelope, WidgetCredentialCapture)
    case rejected(WidgetCredentialCapture, ActivityCatalog.ClearResult)
}

@MainActor struct WidgetSnapshotClient {
    private struct Verified {
        let activation: ActivityCatalog.Activation
        let tag: String
        let snapshot: WidgetSnapshotEnvelope
    }

    // One credential generation in the extension process. A cold start fetches the complete projection.
    private static var previous: Verified?
    let store: WidgetCredentialStore
    // Tests may inject an in-process protocol; production uses the system transport.
    var protocolClasses: [AnyClass]? = nil

    /// Network and decoding failures keep the existing honestly aged snapshot.
    /// The returned capture must be passed unchanged to the fenced publisher.
    func fetch(now: Date = .now) async -> (WidgetSnapshotEnvelope, WidgetCredentialCapture)? {
        guard let capture = store.capture(now: now) else { return nil }
        if case .received(let snapshot, let original) = await fetch(captured: capture) {
            return (snapshot, original)
        }
        return nil
    }

    /// Authentication rejection is distinct from an offline or malformed response.
    /// The caller must suppress the captured activation even if cleanup fails.
    func fetch(captured capture: WidgetCredentialCapture) async -> WidgetSnapshotOutcome {
        guard let parts = URLComponents(string: capture.credential.endpoint), parts.scheme == "https",
              parts.host?.isEmpty == false, parts.user == nil, parts.password == nil,
              parts.path.isEmpty, parts.query == nil, parts.fragment == nil,
              let origin = parts.url else { return .unavailable }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = protocolClasses
        configuration.urlCache = nil
        configuration.httpShouldSetCookies = false
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 8
        let session = URLSession(configuration: configuration, delegate: WidgetRejectRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let url = origin.appending(path: "api/_web/widget-snapshot").appending(queryItems: [URLQueryItem(name: "version", value: "2")])
        var request = URLRequest(url: url,
                                 cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5)
        request.setValue("Bearer \(capture.credential.token)", forHTTPHeaderField: "Authorization")
        request.setValue(capture.credential.workspaceID, forHTTPHeaderField: "X-Hz-Workspace-Id")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if Self.previous?.activation != capture.activation { Self.previous = nil }
        let prior = Self.previous
        if let prior { request.setValue(prior.tag, forHTTPHeaderField: "If-None-Match") }
        do {
            let (bytes, response) = try await session.bytes(for: request)
            guard let response = response as? HTTPURLResponse else { return .unavailable }
            if response.statusCode == 401 || response.statusCode == 403 {
                if Self.previous?.activation == capture.activation { Self.previous = nil }
                return .rejected(capture, store.invalidate(capture.activation))
            }
            if response.statusCode == 304 {
                guard let prior, response.value(forHTTPHeaderField: "ETag") == prior.tag,
                      let observed = response.value(forHTTPHeaderField: "X-Hz-Widget-Observed-At") else { return .unavailable }
                var snapshot = prior.snapshot
                snapshot.generatedAt = observed
                guard snapshot.valid(now: .now), let newTime = snapshot.timestamp,
                      let previousTime = prior.snapshot.timestamp, newTime >= previousTime else { return .unavailable }
                return .received(snapshot, capture)
            }
            guard response.statusCode == 200, response.mimeType == "application/json" else { return .unavailable }
            var data = Data()
            for try await byte in bytes {
                guard data.count < 131_072 else { return .unavailable }
                data.append(byte)
            }
            guard let snapshot = try? JSONDecoder().decode(WidgetSnapshotEnvelope.self, from: data) else { return .unavailable }
            guard snapshot.workspaceID == capture.credential.workspaceID else {
                if Self.previous?.activation == capture.activation { Self.previous = nil }
                return .rejected(capture, store.invalidate(capture.activation))
            }
            guard snapshot.valid(now: .now) else { return .unavailable }
            if snapshot.version == 2, let tag = response.value(forHTTPHeaderField: "ETag"),
               tag.wholeMatch(of: /W\/"[a-f0-9]{64}"/) != nil,
               ActivityCatalog.activation(url: store.catalogURL) == capture.activation {
                Self.previous = Verified(activation: capture.activation, tag: tag, snapshot: snapshot)
            }
            return .received(snapshot, capture)
        } catch { return .unavailable }
    }
}

extension ActivityWidgetData {
    /// A background projection never replaces the full catalog.
    static func savePartial(_ source: WidgetSnapshotEnvelope, capture: WidgetCredentialCapture,
                            now: Date = .now, catalogOverride: ActivityCatalog? = nil,
                            clock: () -> Date = { .now }) -> Bool {
        guard source.valid(now: now), source.workspaceID == capture.credential.workspaceID,
              let timestamp = source.timestamp, let scope = capture.activation.scope,
              let catalog = catalogOverride ?? backgroundCatalog(), let previousData = catalog.snapshot(),
              let old = try? JSONDecoder().decode(Self.self, from: previousData),
              old.workspaceScope == scope, old.updatedAt < timestamp else { return false }
        let generation = UUID().uuidString
        let snapshot = Self(awake: source.awake, waiting: source.waiting, updatedAt: timestamp,
                            heinzels: source.roster, workspaceScope: scope, generation: generation,
                            catalogUpdatedAt: old.catalogUpdatedAt ?? old.updatedAt, rosterCount: source.rosterCount)
        guard let data = try? JSONEncoder().encode(snapshot) else { return false }
        let entries = source.roster.map { CatalogHeinzel(id: $0.id, name: $0.name, state: $0.state,
                                                          activity: $0.activity, asks: $0.asks) }
        return catalog.publishPartial(scope: scope, generation: generation, snapshot: data, entries: entries,
                                      expectedToken: capture.writerToken) { previous in
            let checkedAt = clock()
            return previous == previousData && capture.credential.valid(at: checkedAt) && source.valid(now: checkedAt)
        }
    }
}

enum WidgetTimelineAccess: Sendable {
    case localOnly, hidden, permitted(ActivityCatalog.Activation, Date)

    func mayDisplay(at date: Date) -> Bool {
        switch self {
        case .localOnly: return true
        case .hidden: return false
        case .permitted(_, let expiry): return date < expiry
        }
    }

    var permitExpiry: Date? {
        if case .permitted(_, let expiry) = self { return expiry }
        return nil
    }

    var refreshEnabled: Bool {
        if case .localOnly = self { return false }
        return true
    }
}

/// WidgetKit can request several timelines together. Share an in-process refresh
/// while keeping every publish bound to the captured workspace and writer token.
@MainActor
final class WidgetTimelineRefresh {
    static let shared = WidgetTimelineRefresh()
    nonisolated static let retryInterval: TimeInterval = 15 * 60
    private enum Result { case observed(Date), unavailable, rejected(ActivityCatalog.Activation, ActivityCatalog.ClearResult) }
    private struct Flight { let id: UUID; let task: Task<Result, Never> }
    private var flights: [String: Flight] = [:]
    private var permits: [String: Date] = [:]
    private var rejected: [String: ActivityCatalog.Activation] = [:]
    private var cleanupPending: [String: ActivityCatalog.Activation] = [:]

    private func key(for activation: ActivityCatalog.Activation) -> String {
        activation.id + "/" + (activation.credentialGeneration ?? "")
    }

    /// This read is actor-owned so a returned permit cannot outlive an
    /// in-process rejection. Recheck both authority and time after storage IO.
    func snapshot(access: WidgetTimelineAccess, catalogURL: URL? = ActivityWidgetData.sharedCatalogURL(),
                  read: () -> ActivityWidgetData? = ActivityWidgetData.read,
                  clock: () -> Date = { .now }) -> ActivityWidgetData? {
        switch access {
        case .localOnly: return read()
        case .hidden: return nil
        case .permitted(let activation, let expiry):
            let key = key(for: activation)
            guard expiry > clock(), permits[key] == expiry, rejected[key] == nil,
                  let url = catalogURL, ActivityCatalog.activation(url: url) == activation,
                  let snapshot = read(), snapshot.workspaceScope == activation.scope,
                  ActivityCatalog.activation(url: url) == activation, permits[key] == expiry,
                  rejected[key] == nil, expiry > clock() else { return nil }
            return snapshot
        }
    }

    /// Permits are deliberately process-local: after a restart, offline or
    /// inaccessible storage cannot reveal a snapshot whose revocation failed.
    func refreshIfConfigured() async -> WidgetTimelineAccess {
        guard WidgetCredentialStore.enrollmentEnabled else { return .localOnly }
        guard let store = WidgetCredentialStore.configured() else { return .hidden }
        return await refresh(store: store, fetch: { capture in
            await WidgetSnapshotClient(store: store).fetch(captured: capture)
        }, publish: { snapshot, capture in
            ActivityWidgetData.savePartial(snapshot, capture: capture)
        })
    }

    /// Injected operations allow the same scheduling and fencing path to be tested
    /// without a signed shared Keychain or a real workspace.
    func refresh(store: WidgetCredentialStore,
                 fetch: @escaping @MainActor (WidgetCredentialCapture) async -> WidgetSnapshotOutcome,
                 publish: @escaping @MainActor (WidgetSnapshotEnvelope, WidgetCredentialCapture) -> Bool) async -> WidgetTimelineAccess {
        // Retry the captured activation even if the Keychain is now unavailable
        // or the credential expired; neither condition permits a new request.
        for (key, activation) in cleanupPending {
            let cleanup = store.invalidate(activation)
            if cleanup.complete || cleanup.stale {
                cleanupPending[key] = nil
            } else {
                return .hidden
            }
        }
        if let pending = ActivityCatalog.pendingCleanup(url: store.catalogURL) {
            let cleanup = store.invalidate(pending)
            if !cleanup.complete && !cleanup.stale { return .hidden }
        }
        guard let capture = store.capture() else { return .hidden }
        let key = key(for: capture.activation)
        if rejected[key] != nil { return .hidden }
        let flight: Flight
        if let existing = flights[key] {
            flight = existing
        } else {
            let task = Task { @MainActor in
                switch await fetch(capture) {
                case .received(let snapshot, let original):
                    guard original.activation == capture.activation,
                          original.credential == capture.credential, original.writerToken == capture.writerToken,
                          publish(snapshot, original),
                          let timestamp = snapshot.timestamp else { return Result.unavailable }
                    return .observed(min(timestamp.addingTimeInterval(300), original.credential.expiresAt))
                case .unavailable: return .unavailable
                case .rejected(let original, let cleanup): return .rejected(original.activation, cleanup)
                }
            }
            flight = Flight(id: UUID(), task: task)
            flights[key] = flight
        }
        let result = await flight.task.value
        // A replaced workspace never inherits an old request's permit or revocation.
        let isCurrent = ActivityCatalog.activation(url: store.catalogURL) == capture.activation
        if flights[key]?.id == flight.id {
            flights[key] = nil
            switch result {
            case .observed(let expiry): if isCurrent { permits[key] = expiry }
            case .rejected(let activation, let cleanup):
                if activation == capture.activation {
                    permits[key] = nil
                    rejected[key] = activation
                    if !cleanup.complete && !cleanup.stale { cleanupPending[key] = activation }
                }
            case .unavailable: break
            }
        }
        guard isCurrent, rejected[key] == nil else { return .hidden }
        guard let expiry = permits[key], expiry > .now else { permits[key] = nil; return .hidden }
        return .permitted(capture.activation, expiry)
    }
}
