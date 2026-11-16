import Foundation
import WebKit

enum WidgetEnrollmentResponse {
    case ok(Data)
    case unauthorized
    case conflict
    case unavailable
}

/// Executes only the fixed paired-device routes in WebKit. The device bearer
/// stays in the page's storage and never crosses the native bridge.
@MainActor enum WidgetEnrollmentTransport {
    enum Operation { case negotiate, mutate }

    static let script = """
        if (location.origin !== new URL(origin).origin) return {state: 'unavailable'};
        const device = localStorage.getItem('hz.device');
        if (typeof device !== 'string' || !/^hzd_[A-Za-z0-9_-]{32,128}$/.test(device)) return {state: 'unauthorized'};
        const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('heinzel-widget-pair-v1|' + location.origin + '|' + device));
        const fingerprint = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
        if (location.origin !== new URL(origin).origin || localStorage.getItem('hz.device') !== device ||
            fingerprint !== expectedFingerprint) return {state: 'unavailable'};
        const target = operation === 'negotiate' ? '/api/_web/widget-fence' : '/api/_web/widget-fenced-credential';
        const headers = {Authorization: `Bearer ${device}`};
        if (operation === 'mutate') headers['Content-Type'] = 'application/json';
        try {
            const response = await fetch(target, {method: 'POST', headers,
                body: operation === 'mutate' ? payload : undefined, credentials: 'omit',
                cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(5000)});
            if (location.origin !== new URL(origin).origin || localStorage.getItem('hz.device') !== device) return {state: 'unavailable'};
            if (response.status === 401 || response.status === 403) return {state: 'unauthorized'};
            if (response.status === 409) return {state: 'conflict'};
            if (response.status !== 200 || response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json' || !response.body) return {state: 'unavailable'};
            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8', {fatal: true});
            let bytes = 0, text = '';
            while (true) {
                const part = await reader.read();
                if (part.done) break;
                bytes += part.value.byteLength;
                if (bytes > 4096) { await reader.cancel(); return {state: 'unavailable'}; }
                text += decoder.decode(part.value, {stream: true});
            }
            text += decoder.decode();
            if (location.origin !== new URL(origin).origin || localStorage.getItem('hz.device') !== device) return {state: 'unavailable'};
            const value = JSON.parse(text);
            const hex32 = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
            const hex64 = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
            if (!value || !hex32(value.epoch) || !hex32(value.revision) || !hex64(value.workspace_id)) return {state: 'unavailable'};
            if (operation === 'negotiate') {
                if (value.last_attempt != null && !hex32(value.last_attempt)) return {state: 'unavailable'};
                return {state: 'ok', body: JSON.stringify({epoch:value.epoch, revision:value.revision,
                    workspace_id:value.workspace_id, last_attempt:value.last_attempt ?? null})};
            }
            if (!hex32(value.attempt) || value.operation !== 'issue' || typeof value.expires_at !== 'string' ||
                !/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+(?:Z|[+]00:00)$/.test(value.expires_at)) return {state: 'unavailable'};
            return {state: 'ok', body: JSON.stringify({epoch:value.epoch, revision:value.revision,
                workspace_id:value.workspace_id, attempt:value.attempt, operation:value.operation, expires_at:value.expires_at})};
        } catch (_) { return {state: 'unavailable'}; }
        """

    static func exchange(_ operation: Operation, payload: Data?, in view: WKWebView, endpoint: URL, fingerprint: String,
                         isCurrent: () -> Bool) async -> WidgetEnrollmentResponse {
        guard endpoint.scheme == "https", view.url.map({ sameOrigin($0, endpoint) }) == true,
              isCurrent(), payload.map({ $0.count <= 4096 }) ?? true,
              operation == .negotiate || payload != nil else { return .unavailable }
        let body: String
        if let payload {
            guard let value = String(data: payload, encoding: .utf8) else { return .unavailable }
            body = value
        } else { body = "" }
        let value = try? await view.callAsyncJavaScript(script, arguments: [
            "origin": endpoint.absoluteString, "operation": operation == .negotiate ? "negotiate" : "mutate", "payload": body,
            "expectedFingerprint": fingerprint,
        ], in: nil, contentWorld: .defaultClient)
        guard isCurrent(), view.url.map({ sameOrigin($0, endpoint) }) == true,
              let result = value as? [String: Any], let state = result["state"] as? String else { return .unavailable }
        switch state {
        case "unauthorized": return .unauthorized
        case "conflict": return .conflict
        case "ok":
            guard let text = result["body"] as? String, text.utf8.count <= 4096 else { return .unavailable }
            return .ok(Data(text.utf8))
        default: return .unavailable
        }
    }

    static func pairedDeviceFingerprint(in view: WKWebView, endpoint: URL, isCurrent: () -> Bool) async -> String? {
        guard endpoint.scheme == "https", view.url.map({ sameOrigin($0, endpoint) }) == true, isCurrent() else { return nil }
        let value = try? await view.callAsyncJavaScript("""
            if (location.origin !== new URL(origin).origin) return null;
            const value = localStorage.getItem('hz.device');
            if (typeof value !== 'string' || !/^hzd_[A-Za-z0-9_-]{32,128}$/.test(value)) return null;
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('heinzel-widget-pair-v1|' + location.origin + '|' + value));
            if (location.origin !== new URL(origin).origin || localStorage.getItem('hz.device') !== value) return null;
            return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
            """, arguments: ["origin": endpoint.absoluteString], in: nil, contentWorld: .defaultClient) as? String
        guard let value, value.wholeMatch(of: /[a-f0-9]{64}/) != nil, isCurrent(),
              view.url.map({ sameOrigin($0, endpoint) }) == true else { return nil }
        return value
    }

    private static func sameOrigin(_ first: URL, _ second: URL) -> Bool {
        guard let a = URLComponents(url: first, resolvingAgainstBaseURL: false),
              let b = URLComponents(url: second, resolvingAgainstBaseURL: false),
              a.scheme == "https", b.scheme == "https", a.user == nil, a.password == nil,
              b.user == nil, b.password == nil, b.path.isEmpty, b.query == nil, b.fragment == nil,
              let host = a.host, !host.isEmpty, let target = b.host, !target.isEmpty else { return false }
        return host.lowercased() == target.lowercased() && (a.port ?? 443) == (b.port ?? 443)
    }
}

enum WidgetEnrollmentOutcome {
    case installed(ActivityCatalog.Activation)
    case pending
    case unauthorized
    case conflict
    case identityMismatch(ActivityCatalog.ClearResult)
}

/// Explicitly invoked by a current foreground WebView; never runs on a timer.
/// The caller's check must bind view identity, document revision and workspace.
@MainActor struct WidgetEnrollmentCoordinator {
    let enrollment: WidgetFencedEnrollment

    func begin(expected: ActivityCatalog.Activation, in view: WKWebView, endpoint: URL, fingerprint: String,
               isCurrent: @escaping () -> Bool) async -> WidgetEnrollmentOutcome {
        guard isCurrent(), expected.endpoint == endpoint.absoluteString,
              let reservation = enrollment.prepare(expected: expected, pairingFingerprint: fingerprint) else { return .pending }
        return await resume(reservation, in: view, endpoint: endpoint, fingerprint: fingerprint, isCurrent: isCurrent)
    }

    /// Resume only the same persisted operation; conflicts never rebase it.
    func resume(_ reservation: ActivityCatalog.Enrollment, in view: WKWebView, endpoint: URL, fingerprint: String,
                isCurrent: @escaping () -> Bool) async -> WidgetEnrollmentOutcome {
        let current = {
            isCurrent() && reservation.previous.endpoint == endpoint.absoluteString &&
            ActivityCatalog.enrollmentPairingMatches(url: enrollment.store.catalogURL, expected: reservation, fingerprint: fingerprint) &&
            ActivityCatalog.withEnrollment(url: enrollment.store.catalogURL, expected: reservation, { true }) == true
        }
        guard current(), enrollment.canResume(reservation) else { return .pending }
        if let receipt = ActivityCatalog.savedEnrollmentReceipt(url: enrollment.store.catalogURL, expected: reservation) {
            switch enrollment.complete(receipt, reservation: reservation) {
            case .installed(let activation): return .installed(activation)
            case .identityMismatch(let result): return .identityMismatch(result)
            case .rejected: return .pending
            }
        }
        let status = await WidgetEnrollmentTransport.exchange(.negotiate, payload: nil, in: view, endpoint: endpoint, fingerprint: fingerprint, isCurrent: current)
        guard current() else { return .pending }
        switch status {
        case .unauthorized: return .identityMismatch(enrollment.cancel(reservation))
        case .conflict: return .conflict
        case .unavailable: return .pending
        case .ok(let data):
            switch enrollment.bind(data, reservation: reservation) {
            case .identityMismatch(let result): return .identityMismatch(result)
            case .conflict: return .conflict
            case .unavailable: return .pending
            case .ready(let request):
                guard let admission = ActivityCatalog.admitEnrollmentIssue(url: enrollment.store.catalogURL,
                                                                            expected: reservation, request: request) else { return .pending }
                switch admission {
                case .receipt(let receipt):
                    switch enrollment.complete(receipt, reservation: reservation) {
                    case .installed(let activation): return .installed(activation)
                    case .identityMismatch(let result): return .identityMismatch(result)
                    case .rejected: return .pending
                    }
                case .issue(let admitted):
                    let response = await WidgetEnrollmentTransport.exchange(.mutate, payload: admitted, in: view, endpoint: endpoint, fingerprint: fingerprint, isCurrent: current)
                    guard current() else { return .pending }
                    switch response {
                    case .unauthorized: return .identityMismatch(enrollment.cancel(reservation))
                    case .conflict: return .conflict
                    case .unavailable: return .pending
                    case .ok(let receipt):
                        switch enrollment.complete(receipt, reservation: reservation) {
                        case .installed(let activation): return .installed(activation)
                        case .identityMismatch(let result): return .identityMismatch(result)
                        case .rejected: return .pending
                        }
                    }
                }
            }
        }
    }
}
