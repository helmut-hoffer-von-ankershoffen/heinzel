import Observation
import WebKit

/// The fingerprint is scoped to one document. Swift never receives the bearer or persists the fingerprint.
@MainActor enum WorkspaceDocumentFacts {
    private static let authorityScript = """
        if (location.origin !== new URL(origin).origin) return null;
        const credentials = () => [...new Set([new URLSearchParams(location.hash.slice(1)).get('token'),
            sessionStorage.getItem('hz.token'), localStorage.getItem('hz.device')].filter(Boolean))];
        const keys = credentials();
        if (!keys.length) return null;
        const current = () => {
            const values = credentials();
            return keys.length === values.length && keys.every((key, index) => key === values[index]);
        };
        const salt = globalThis.heinzelInformationSalt ??= crypto.randomUUID();
        const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(salt + JSON.stringify(keys)));
        const identity = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
        if (!current()) return null;
        """

    static func authority(in view: WKWebView, origin: URL) async -> String? {
        try? await view.callAsyncJavaScript(authorityScript + "\nreturn identity;",
            arguments: ["origin": origin.absoluteString], in: nil, contentWorld: .defaultClient) as? String
    }

    static func version(in view: WKWebView, origin: URL, authority: String? = nil,
                        clientIdentity: String = "") async -> WorkspaceVersionObservation? {
        let data = try? await view.callAsyncJavaScript(authorityScript + """
            \nif (expected && identity !== expected) return null;
            const signal = AbortSignal.timeout(5000);
            const read = async path => {
                for (const key of keys) {
                    if (!current()) return null;
                    const headers = {Authorization: 'Bearer ' + key};
                    if (path === '/api/workspace-version' && clientIdentity) headers['X-Heinzel-Client'] = clientIdentity;
                    const response = await fetch(path, {headers,
                        mode: 'same-origin', redirect: 'error', credentials: 'omit', cache: 'no-store', signal});
                    if (!current()) return null;
                    if (response.status !== 401) return response;
                }
                return null;
            };
            const facts = await (async () => {
                const date = value => {
                    if (typeof value !== 'string') return null;
                    const m = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:[.][0-9]{1,6})?(?:Z|[+-]([0-9]{2}):([0-9]{2}))$/.exec(value);
                    if (!m) return null;
                    const [, year, month, day, hour, minute, second, offsetHour, offsetMinute] = m.map(Number);
                    if (year < 1970 || month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate() ||
                        hour > 23 || minute > 59 || second > 59 || (offsetHour || 0) > 23 || (offsetMinute || 0) > 59) return null;
                    const ms = Date.parse(value); return Number.isFinite(ms) ? ms : null;
                };
                const response = await read('/api/workspace-version');
                if (!response) return null;
                if (response.ok) {
                    const info = await response.json();
                    const flag = value => typeof value === 'boolean' ? value : null;
                    return {version:info?.web?.version ?? null, backendObservedAt:date(info?.observed_at),
                            backendIP:info?.web?.backend_ip ?? null,
                            webObservedSince:date(info?.web?.observed_since), webRunningSince:date(info?.web?.running_since),
                            installedVersion:info?.installed?.version ?? null,
                            installedUpdatedAt:date(info?.installed?.updated_at), daemonLastVersion:info?.daemon?.last_observed_version ?? null,
                            daemonLastObservedAt:date(info?.daemon?.last_observed_at),
                            daemonObservationStale:flag(info?.daemon?.observation_stale),
                            daemonRunningVersion:info?.daemon?.running_version ?? null,
                            daemonRunningSince:date(info?.daemon?.running_since), daemonRegisteredAt:date(info?.daemon?.registered_at),
                            daemonLoaded:flag(info?.daemon?.loaded), daemonOwned:flag(info?.daemon?.owned),
                            daemonStatusObservedAt:date(info?.daemon?.status_observed_at)};
                }
                if (response.status !== 404) return null;
                const fallback = await read('/api/system/info');
                if (!fallback?.ok) return null;
                const info = await fallback.json();
                return {version:info?.version ?? null};
            })();
            return current() ? facts : null;
            """, arguments: ["origin": origin.absoluteString, "expected": authority ?? "", "clientIdentity": clientIdentity], in: nil,
            contentWorld: .defaultClient) as? [String: Any]
        return data.flatMap { WorkspaceVersionObservation.fromPayload($0, now: .now) }
    }
}

struct WorkspaceInformationRequest: Equatable {
    let origin: URL
    let page: Int
    let networkRevision: Int
    let remoteAddress: String?
    let clientAddress: String?
}

/// While Information is visible, check only the selected document's local authority.
/// Network reads run separately, so a held response cannot delay invalidation.
@MainActor @Observable final class WorkspaceInformationFacts {
    struct Snapshot {
        let target: WorkspaceInformationRequest
        let version: WorkspaceVersionObservation
        let clientAddress: String?
    }

    private(set) var snapshot: Snapshot?
    private var generation = 0

    func observe(_ target: WorkspaceInformationRequest?, view: @escaping @MainActor () -> WKWebView?) async {
        generation &+= 1
        let run = generation
        snapshot = nil
        guard let target else { return }
        var previous: String?
        var read: Task<Void, Never>?
        defer {
            read?.cancel()
            if generation == run { snapshot = nil }
        }
        while !Task.isCancelled && generation == run {
            let document = view()
            let authority: String?
            if let document { authority = await WorkspaceDocumentFacts.authority(in: document, origin: target.origin) }
            else { authority = nil }
            guard !Task.isCancelled, generation == run else { return }
            guard document === view() else {
                read?.cancel()
                previous = nil
                snapshot = nil
                continue
            }
            if previous != authority {
                previous = authority
                read?.cancel()
                snapshot = nil
                if let document, let authority {
                    read = Task {
                        await refresh(target, in: document, authority: authority, isCurrent: {
                            !Task.isCancelled && self.generation == run && document === view()
                        })
                    }
                }
            }
            do { try await Task.sleep(for: .milliseconds(350)) } catch { return }
        }
    }

    private func refresh(_ target: WorkspaceInformationRequest, in document: WKWebView, authority: String, isCurrent: () -> Bool) async {
        while isCurrent() {
            let facts = await WorkspaceDocumentFacts.version(in: document, origin: target.origin, authority: authority)
            guard isCurrent() else { return }
            var clientAddress: String?
            if facts != nil {
                let route = await Endpoint.probeHost(at: target.origin)
                if route.issue == nil { clientAddress = route.clientAddress }
            }
            let current = await WorkspaceDocumentFacts.authority(in: document, origin: target.origin)
            guard isCurrent(), current == authority else { return }
            snapshot = facts.map { Snapshot(target: target, version: $0, clientAddress: clientAddress) }
            do { try await Task.sleep(for: .seconds(60)) } catch { return }
        }
    }
}
