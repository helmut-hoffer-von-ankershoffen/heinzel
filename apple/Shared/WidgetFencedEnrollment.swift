import CryptoKit
import Foundation
import Security

private struct FenceStatus: Decodable {
    let epoch: String
    let revision: String
    let workspaceID: String
    enum CodingKeys: String, CodingKey { case epoch, revision, workspaceID = "workspace_id" }
    var valid: Bool {
        epoch.wholeMatch(of: /[a-f0-9]{32}/) != nil && revision.wholeMatch(of: /[a-f0-9]{32}/) != nil &&
        workspaceID.wholeMatch(of: /[a-f0-9]{64}/) != nil
    }
}

private struct FenceReceipt: Decodable {
    let epoch: String
    let workspaceID: String
    let attempt: String
    let operation: String
    let revision: String
    let expiresAt: String
    enum CodingKeys: String, CodingKey { case epoch, workspaceID = "workspace_id", attempt, operation, revision, expiresAt = "expires_at" }
    var expiry: Date? {
        guard expiresAt.hasSuffix("Z") || expiresAt.hasSuffix("+00:00") else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let result = formatter.date(from: expiresAt) { return result }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: expiresAt)
    }
}

private struct FenceMutation: Codable, Equatable {
    let epoch: String
    let revision: String
    let attempt: String
    let operation: String
    let commitment: String
}

private struct PendingFence: Codable {
    let version: Int
    let reservation: ActivityCatalog.Enrollment
    let attempt: String
    let generation: String
    let secret: String
    var workspaceID: String? = nil
    var request: FenceMutation? = nil
    var requestData: Data? = nil

    var valid: Bool {
        guard version == 1, attempt == reservation.id.replacingOccurrences(of: "-", with: "").lowercased(),
              attempt.wholeMatch(of: /[a-f0-9]{32}/) != nil, UUID(uuidString: generation) != nil,
              secret.wholeMatch(of: /hzw_[A-Za-z0-9_-]{43}/) != nil else { return false }
        guard let request else { return requestData == nil && workspaceID == nil }
        let hash = SHA256.hash(data: Data(secret.utf8)).map { String(format: "%02x", $0) }.joined()
        guard let requestData, let workspaceID,
              workspaceID.wholeMatch(of: /[a-f0-9]{64}/) != nil,
              request.epoch.wholeMatch(of: /[a-f0-9]{32}/) != nil,
              request.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil,
              request.attempt == attempt, request.operation == "issue", request.commitment == hash else { return false }
        return (try? JSONDecoder().decode(FenceMutation.self, from: requestData)) == request
    }
}

enum WidgetFenceBinding {
    case ready(Data)
    case identityMismatch(ActivityCatalog.ClearResult)
    case conflict
    case unavailable
}

/// Local half of Core's fenced issuance. No raw bearer is sent through WebKit.
struct WidgetFencedEnrollment {
    let store: WidgetCredentialStore

    func canResume(_ reservation: ActivityCatalog.Enrollment) -> Bool {
        let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
        return ActivityCatalog.withEnrollment(url: store.catalogURL, expected: reservation) {
            guard let data = store.keychain.readPending(attempt),
                  let pending = try? JSONDecoder().decode(PendingFence.self, from: data),
                  pending.valid, pending.reservation == reservation else { return nil }
            return true
        } == true
    }

    func abandonInterruptedPreparation(_ reservation: ActivityCatalog.Enrollment) -> Bool {
        let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
        return ActivityCatalog.abandonPreparing(url: store.catalogURL, expected: reservation) {
            store.keychain.deletePending(attempt)
        }
    }

    /// Finish cleanup after the planned authority was published before a crash.
    func finishInterruptedInstallation(_ reservation: ActivityCatalog.Enrollment) -> ActivityCatalog.Activation? {
        let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
        return ActivityCatalog.finishReceipted(url: store.catalogURL, expected: reservation, verify: { journal, active in
            guard self.recoveryEvidenceMatches(journal),
                  case .data(let raw) = self.store.keychain.readStatus(),
                  let credential = try? JSONDecoder().decode(WidgetCredential.self, from: raw),
                  credential.valid(at: credential.expiresAt.addingTimeInterval(-1)),
                  credential.endpoint == active.endpoint, credential.workspaceID == active.workspaceID,
                  credential.generation == active.credentialGeneration, credential.pendingAttempt == attempt,
                  let requestData = journal.request, let request = try? JSONDecoder().decode(FenceMutation.self, from: requestData),
                  let receiptData = journal.receipt, let receipt = try? JSONDecoder().decode(FenceReceipt.self, from: receiptData),
                  receipt.expiry == credential.expiresAt else { return false }
            let commitment = SHA256.hash(data: Data(credential.token.utf8)).map { String(format: "%02x", $0) }.joined()
            return request.commitment == commitment
        }, cleanup: { self.store.keychain.deletePending(attempt) })
    }

    func finishExpiredReceipt(_ reservation: ActivityCatalog.Enrollment, clock: () -> Date = { .now }) -> ActivityCatalog.ClearResult? {
        guard let data = ActivityCatalog.savedEnrollmentReceipt(url: store.catalogURL, expected: reservation),
              let receipt = try? JSONDecoder().decode(FenceReceipt.self, from: data),
              let expiry = receipt.expiry, expiry <= clock() else { return nil }
        let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
        let result = ActivityCatalog.retireExpiredReceipt(url: store.catalogURL, expected: reservation, verify: {
            $0.receipt == data && expiry <= clock() && self.recoveryEvidenceMatches($0)
        }, cleanup: { self.store.keychain.deletePending(attempt) && self.store.purgeCredential() })
        if result.complete, let pinned = ActivityCatalog.activation(url: store.catalogURL),
           pinned.endpoint == reservation.previous.endpoint, pinned.workspaceID == receipt.workspaceID {
            NotificationCenter.default.post(name: WidgetCredentialStore.activationChanged, object: pinned.id)
        }
        return result
    }

    func prepare(expected: ActivityCatalog.Activation, pairingFingerprint: String? = nil) -> ActivityCatalog.Enrollment? {
        guard let reservation = store.beginEnrollment(expected: expected, pairingFingerprint: pairingFingerprint) else { return nil }
        var random = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, random.count, &random) == errSecSuccess else { return nil }
        let secret = "hzw_" + Data(random).base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
        let pending = PendingFence(version: 1, reservation: reservation, attempt: attempt,
                                   generation: UUID().uuidString, secret: secret)
        guard pending.valid, let data = try? JSONEncoder().encode(pending),
              ActivityCatalog.withEnrollment(url: store.catalogURL, expected: reservation, {
                  store.keychain.createPending(data, attempt: attempt) && store.keychain.readPending(attempt) == data ? true : nil
              }) == true else { return nil }
        return reservation
    }

    /// Clear only this reservation. Preserve any issued request outside Keychain
    /// before deleting its secret; the independent fence remains unresolved.
    func cancel(_ reservation: ActivityCatalog.Enrollment) -> ActivityCatalog.ClearResult {
        let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
        let pending: PendingFence? = ActivityCatalog.withEnrollment(url: store.catalogURL, expected: reservation) {
            guard let data = store.keychain.readPending(attempt),
                  let value = try? JSONDecoder().decode(PendingFence.self, from: data),
                  value.valid, value.reservation == reservation else { return nil }
            return value
        }
        if let pending, let request = pending.requestData, let workspace = pending.workspaceID {
            _ = ActivityCatalog.armEnrollment(url: store.catalogURL, expected: reservation,
                                              workspaceID: workspace, request: request)
        }
        return ActivityCatalog.clearEnrollment(url: store.catalogURL, expected: reservation,
                                               journalCheck: { recoveryEvidenceMatches($0) }) {
            let deleted = store.keychain.deletePending(attempt)
            let activeDeleted = store.purgeCredential()
            return deleted && activeDeleted
        }
    }

    /// Called only while the authority lock protects the checked journal.
    func recoveryEvidenceMatches(_ journal: ActivityCatalog.EnrollmentJournal) -> Bool {
        let attempt = journal.reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
        switch store.keychain.readPendingStatus(attempt) {
        case .unavailable: return false
        case .missing: return true
        case .data(let data):
            guard let pending = try? JSONDecoder().decode(PendingFence.self, from: data),
                  pending.valid, pending.reservation == journal.reservation else { return false }
            switch journal.phase {
            case .preparing: return pending.request == nil && pending.workspaceID == nil
            case .armed, .receipted: return pending.requestData == journal.request && pending.workspaceID == journal.workspaceID
            case .legacyUnknown: return false
            }
        }
    }

    /// The exact committed request is persisted before it can be sent or retried.
    func bind(_ data: Data, reservation: ActivityCatalog.Enrollment) -> WidgetFenceBinding {
        guard data.count <= 4096, let status = try? JSONDecoder().decode(FenceStatus.self, from: data), status.valid else { return .unavailable }
        if let identity = reservation.previous.workspaceID, identity != status.workspaceID {
            return .identityMismatch(cancel(reservation))
        }
        let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
        let binding: WidgetFenceBinding = ActivityCatalog.withEnrollment(url: store.catalogURL, expected: reservation) {
            guard let raw = store.keychain.readPending(attempt),
                  var pending = try? JSONDecoder().decode(PendingFence.self, from: raw),
                  pending.valid, pending.reservation == reservation else { return .unavailable }
            if let request = pending.request, let saved = pending.requestData {
                guard request.epoch == status.epoch, status.workspaceID == pending.workspaceID,
                      (try? JSONDecoder().decode(FenceMutation.self, from: saved)) == request else { return .conflict }
                return .ready(saved)
            }
            guard pending.request == nil, pending.requestData == nil, pending.workspaceID == nil else { return .unavailable }
            let commitment = SHA256.hash(data: Data(pending.secret.utf8)).map { String(format: "%02x", $0) }.joined()
            let request = FenceMutation(epoch: status.epoch, revision: status.revision, attempt: attempt,
                                        operation: "issue", commitment: commitment)
            let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
            guard let requestData = try? encoder.encode(request) else { return .unavailable }
            pending.request = request; pending.requestData = requestData; pending.workspaceID = status.workspaceID
            guard let encoded = try? JSONEncoder().encode(pending), store.keychain.updatePending(encoded, attempt: attempt),
                  store.keychain.readPending(attempt) == encoded else { return .unavailable }
            return .ready(requestData)
        } ?? .unavailable
        if case .ready(let request) = binding {
            guard ActivityCatalog.armEnrollment(url: store.catalogURL, expected: reservation,
                                                workspaceID: status.workspaceID, request: request) else { return .unavailable }
        }
        return binding
    }

    private func cleanPreviousPending(except attempt: String) -> Bool {
        switch store.keychain.readStatus() {
        case .unavailable: return false
        case .missing: return true
        case .data(let data):
            guard let previous = try? JSONDecoder().decode(WidgetCredential.self, from: data) else { return false }
            guard let old = previous.pendingAttempt, old != attempt else { return true }
            return store.keychain.deletePending(old)
        }
    }

    func complete(_ data: Data, reservation: ActivityCatalog.Enrollment, now: Date = .now,
                  clock: () -> Date = { .now }) -> WidgetIssuanceResult {
        guard data.count <= 4096, let receipt = try? JSONDecoder().decode(FenceReceipt.self, from: data),
              receipt.epoch.wholeMatch(of: /[a-f0-9]{32}/) != nil,
              receipt.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil,
              receipt.workspaceID.wholeMatch(of: /[a-f0-9]{64}/) != nil,
              let expiry = receipt.expiry else { return .rejected }
        let attempt = reservation.id.replacingOccurrences(of: "-", with: "").lowercased()
        let material: (PendingFence, Data)? = ActivityCatalog.withEnrollment(url: store.catalogURL, expected: reservation) {
            guard let data = store.keychain.readPending(attempt),
                  let value = try? JSONDecoder().decode(PendingFence.self, from: data),
                  value.valid, value.reservation == reservation else { return nil }
            return (value, data)
        }
        guard let (pending, pendingData) = material, let request = pending.request, let workspaceID = pending.workspaceID,
              receipt.epoch == request.epoch, receipt.attempt == request.attempt, receipt.operation == "issue",
              receipt.revision != request.revision else { return .rejected }
        if receipt.workspaceID != workspaceID {
            return .identityMismatch(cancel(reservation))
        }
        let credential = WidgetCredential(endpoint: reservation.previous.endpoint, workspaceID: workspaceID,
                                          generation: pending.generation, token: pending.secret, expiresAt: expiry,
                                          pendingAttempt: attempt)
        let validationTime = expiry <= now ? expiry.addingTimeInterval(-1) : now
        guard credential.valid(at: validationTime), let encoded = try? JSONEncoder().encode(credential),
              let requestData = pending.requestData,
              let journal = ActivityCatalog.recordEnrollmentReceipt(url: store.catalogURL, expected: reservation,
                  workspaceID: workspaceID, request: requestData, receipt: data, generation: credential.generation) else { return .rejected }
        if expiry <= clock() {
            let result = ActivityCatalog.retireExpiredReceipt(url: store.catalogURL, expected: reservation, verify: {
                $0 == journal && expiry <= clock() && self.recoveryEvidenceMatches($0)
            }, cleanup: { self.store.keychain.deletePending(attempt) && self.store.purgeCredential() })
            if result.complete, let pinned = ActivityCatalog.activation(url: store.catalogURL),
               pinned.endpoint == reservation.previous.endpoint, pinned.workspaceID == workspaceID {
                NotificationCenter.default.post(name: WidgetCredentialStore.activationChanged, object: pinned.id)
            }
            return .identityMismatch(result)
        }
        guard let (installed, cleaned) = ActivityCatalog.installReceipted(url: store.catalogURL, expected: reservation,
                  receipt: journal, install: {
                      credential.valid(at: clock()) && store.keychain.readPending(attempt) == pendingData &&
                      cleanPreviousPending(except: attempt) && store.keychain.write(encoded) && store.keychain.read() == encoded
                  }, authorize: { credential.valid(at: clock()) && store.keychain.read() == encoded },
                  cleanup: { store.keychain.deletePending(attempt) }) else { return .rejected }
        if !cleaned {
            NotificationCenter.default.post(name: ActivityWidgetData.clearFailed, object: nil)
        }
        NotificationCenter.default.post(name: WidgetCredentialStore.activationChanged, object: installed.id)
        return .installed(installed)
    }
}
