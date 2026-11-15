import Foundation

/// The Core endpoint returns a bounded projection, not the full catalog.
struct WidgetSnapshotEnvelope: Decodable {
    let version: Int
    let workspaceID: String
    let generatedAt: String
    let freshForSeconds: Int
    let awake: Int
    let waiting: Int
    let roster: [WidgetHeinzel]

    enum CodingKeys: String, CodingKey {
        case version, workspaceID = "workspace_id", generatedAt = "generated_at"
        case freshForSeconds = "fresh_for_seconds", awake, waiting, roster
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
        guard version == 1, freshForSeconds == 300, let timestamp,
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
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping @Sendable (URLRequest?) -> Void) { completionHandler(nil) }
}

struct WidgetSnapshotClient {
    let store: WidgetCredentialStore

    /// Network and decoding failures keep the existing honestly aged snapshot.
    /// The returned capture must be passed unchanged to the fenced publisher.
    func fetch(now: Date = .now) async -> (WidgetSnapshotEnvelope, WidgetCredentialCapture)? {
        guard let capture = store.capture(now: now),
              let parts = URLComponents(string: capture.credential.endpoint), parts.scheme == "https",
              parts.host?.isEmpty == false, parts.user == nil, parts.password == nil,
              parts.path.isEmpty, parts.query == nil, parts.fragment == nil,
              let origin = parts.url else { return nil }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.httpShouldSetCookies = false
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 8
        let session = URLSession(configuration: configuration, delegate: WidgetRejectRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: origin.appending(path: "api/_web/widget-snapshot"),
                                 cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5)
        request.setValue("Bearer \(capture.credential.token)", forHTTPHeaderField: "Authorization")
        request.setValue(capture.credential.workspaceID, forHTTPHeaderField: "X-Hz-Workspace-Id")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        do {
            let (bytes, response) = try await session.bytes(for: request)
            guard let response = response as? HTTPURLResponse else { return nil }
            if response.statusCode == 401 || response.statusCode == 403 {
                _ = store.invalidate(capture.activation)
                return nil
            }
            guard response.statusCode == 200, response.mimeType == "application/json" else { return nil }
            var data = Data()
            for try await byte in bytes {
                guard data.count < 131_072 else { return nil }
                data.append(byte)
            }
            guard let snapshot = try? JSONDecoder().decode(WidgetSnapshotEnvelope.self, from: data) else { return nil }
            guard snapshot.workspaceID == capture.credential.workspaceID else {
                _ = store.invalidate(capture.activation)
                return nil
            }
            guard snapshot.valid(now: .now) else { return nil }
            return (snapshot, capture)
        } catch { return nil }
    }
}

extension ActivityWidgetData {
    /// A background projection never replaces the full catalog.
    static func savePartial(_ source: WidgetSnapshotEnvelope, capture: WidgetCredentialCapture,
                            now: Date = .now, catalogOverride: ActivityCatalog? = nil) -> Bool {
        guard source.valid(now: now), source.workspaceID == capture.credential.workspaceID,
              let timestamp = source.timestamp, let scope = capture.activation.scope,
              let catalog = catalogOverride ?? backgroundCatalog() else { return false }
        let generation = UUID().uuidString
        let snapshot = Self(awake: source.awake, waiting: source.waiting, updatedAt: timestamp,
                            heinzels: source.roster, workspaceScope: scope, generation: generation)
        guard let data = try? JSONEncoder().encode(snapshot) else { return false }
        return catalog.publishPartial(scope: scope, generation: generation, snapshot: data,
                                      expectedToken: capture.writerToken) { previous in
            guard let old = try? JSONDecoder().decode(Self.self, from: previous) else { return false }
            return old.workspaceScope == scope && old.updatedAt < timestamp
        }
    }
}
