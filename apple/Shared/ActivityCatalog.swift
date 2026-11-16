import Foundation
import CryptoKit
import SQLite3
import Darwin

struct CatalogHeinzel: Codable, Equatable, Identifiable {
    let id: String
    let name: String
    let state: String
    let activity: String?
    let asks: Int?
    let lastApproved: Double?

    init(id: String, name: String, state: String, activity: String? = nil, asks: Int? = nil, lastApproved: Double? = nil) {
        self.id = id; self.name = name; self.state = state
        self.activity = activity; self.asks = asks; self.lastApproved = lastApproved
    }
}

// The snapshot and its compact catalog are committed together. Readers see either
// the old generation or the new one, even if publication is interrupted.
final class ActivityCatalog {
    private var db: OpaquePointer?
    private let databaseURL: URL
    private let authorityURL: URL
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    private struct Authority: Codable, Equatable {
        let scope: String
        let generation: String
        var activation: Activation? = nil
        var revoked: Bool? = nil
        var cleanup: Activation? = nil
        var enrollment: Enrollment? = nil
        var expirationRetirement: String? = nil
    }

    /// Non-secret authority shared by the app and extension. Secrets stay in Keychain.
    struct Activation: Codable, Equatable, Sendable {
        let id: String
        let endpoint: String
        let workspaceID: String?
        let credentialGeneration: String?

        var scope: String? {
            guard let workspaceID, credentialGeneration != nil else { return nil }
            let input = Data((endpoint + "\n" + workspaceID).utf8)
            return SHA256.hash(data: input).map { String(format: "%02x", $0) }.joined()
        }
    }

    /// A reserved attempt blocks old captures while a destructive Core rotation
    /// is in flight. It remains pending after a crash or unknown network outcome.
    struct Enrollment: Codable, Equatable {
        let id: String
        let previous: Activation
    }

    /// Independent, non-secret fence. Legacy markers remain conservatively unknown.
    struct EnrollmentJournal: Codable, Equatable {
        enum Phase: String, Codable { case preparing, armed, receipted, legacyUnknown }
        let version: Int
        let reservation: Enrollment
        var revision: String
        let phase: Phase
        let workspaceID: String?
        let request: Data?
        var receipt: Data? = nil
        var credentialGeneration: String? = nil
        var activationID: String? = nil
        var pairingFingerprint: String? = nil
        var cancelled: Bool? = nil
        var recoveryStatus: Data? = nil
        var recoveryRequest: Data? = nil
        var recoveryReceipt: Data? = nil
        var recoveryConflicts: [RecoveryConflict]? = nil
    }

    struct RecoveryConflict: Codable, Equatable {
        let request: Data
        let status: Data
    }

    private struct JournalRequest: Decodable {
        let epoch: String
        let revision: String
        let attempt: String
        let operation: String
        let commitment: String
    }

    private struct JournalReceipt: Decodable {
        let epoch: String
        let workspace_id: String
        let attempt: String
        let operation: String
        let revision: String
        let expires_at: String
    }

    private struct JournalStatus: Decodable {
        let epoch: String
        let workspace_id: String
        let revision: String
        let last_attempt: String?
    }

    private struct JournalRevoke: Decodable {
        let epoch: String
        let revision: String
        let attempt: String
        let operation: String
        let commitment: String?
    }

    private struct JournalRevokeReceipt: Decodable {
        let epoch: String
        let workspace_id: String
        let attempt: String
        let operation: String
        let revision: String
        let expires_at: String?
    }

    private static func enrollmentJournal(_ data: Data) -> EnrollmentJournal? {
        if let journal = try? JSONDecoder().decode(EnrollmentJournal.self, from: data) {
            guard journal.version == 1, UUID(uuidString: journal.revision) != nil,
                  UUID(uuidString: journal.reservation.id) != nil,
                  journal.cancelled != false,
                  journal.pairingFingerprint.map({ $0.wholeMatch(of: /[a-f0-9]{64}/) != nil }) ?? true else { return nil }
            switch journal.phase {
            case .preparing:
                guard journal.workspaceID == nil, journal.request == nil, journal.receipt == nil,
                      journal.credentialGeneration == nil, journal.activationID == nil else { return nil }
            case .armed, .receipted:
                guard let workspace = journal.workspaceID, workspace.wholeMatch(of: /[a-f0-9]{64}/) != nil,
                      let request = journal.request, !request.isEmpty, request.count <= 4096,
                      let value = try? JSONDecoder().decode(JournalRequest.self, from: request),
                      value.epoch.wholeMatch(of: /[a-f0-9]{32}/) != nil,
                      value.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil,
                      value.attempt == journal.reservation.id.replacingOccurrences(of: "-", with: "").lowercased(),
                      value.operation == "issue", value.commitment.wholeMatch(of: /[a-f0-9]{64}/) != nil else { return nil }
                if journal.phase == .armed {
                    guard journal.receipt == nil, journal.credentialGeneration == nil, journal.activationID == nil else { return nil }
                } else {
                    guard let receiptData = journal.receipt, receiptData.count <= 4096,
                          let receipt = try? JSONDecoder().decode(JournalReceipt.self, from: receiptData),
                          receipt.epoch == value.epoch, receipt.workspace_id == workspace,
                          receipt.attempt == value.attempt, receipt.operation == "issue",
                          receipt.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil, receipt.revision != value.revision,
                          !receipt.expires_at.isEmpty,
                          let generation = journal.credentialGeneration, UUID(uuidString: generation) != nil,
                          let activation = journal.activationID, UUID(uuidString: activation) != nil else { return nil }
                }
            case .legacyUnknown: return nil
            }
            if let statusData = journal.recoveryStatus {
                guard journal.cancelled == true, (journal.phase == .armed || journal.phase == .receipted), journal.pairingFingerprint != nil,
                      statusData.count <= 4096, let status = try? JSONDecoder().decode(JournalStatus.self, from: statusData),
                      let issueData = journal.request, let issue = try? JSONDecoder().decode(JournalRequest.self, from: issueData),
                      status.epoch == issue.epoch, status.workspace_id == journal.workspaceID,
                      status.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil,
                      status.last_attempt.map({ $0.wholeMatch(of: /[a-f0-9]{32}/) != nil }) ?? true else { return nil }
                if let revokeData = journal.recoveryRequest {
                    guard revokeData.count <= 4096, let revoke = try? JSONDecoder().decode(JournalRevoke.self, from: revokeData),
                          revoke.epoch == issue.epoch, revoke.revision == status.revision,
                          revoke.attempt.wholeMatch(of: /[a-f0-9]{32}/) != nil, revoke.attempt != issue.attempt,
                          revoke.operation == "revoke", revoke.commitment == nil,
                          status.revision == issue.revision || status.last_attempt == issue.attempt else { return nil }
                    if let receiptData = journal.recoveryReceipt {
                        guard receiptData.count <= 4096,
                              let receipt = try? JSONDecoder().decode(JournalRevokeReceipt.self, from: receiptData),
                              receipt.epoch == revoke.epoch, receipt.workspace_id == journal.workspaceID,
                              receipt.attempt == revoke.attempt, receipt.operation == "revoke", receipt.expires_at == nil,
                              receipt.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil, receipt.revision != revoke.revision,
                              let object = try? JSONSerialization.jsonObject(with: receiptData) as? [String: Any],
                              object["expires_at"] is NSNull else { return nil }
                    }
                } else {
                    guard journal.recoveryReceipt == nil, status.revision != issue.revision,
                          let last = status.last_attempt, last != issue.attempt else { return nil }
                }
            } else if journal.recoveryRequest != nil || journal.recoveryReceipt != nil { return nil }
            if let conflicts = journal.recoveryConflicts {
                guard journal.cancelled == true, conflicts.count <= 8,
                      let issueData = journal.request, let issue = try? JSONDecoder().decode(JournalRequest.self, from: issueData) else { return nil }
                for conflict in conflicts {
                    guard conflict.request.count <= 4096, conflict.status.count <= 4096,
                          let revoke = try? JSONDecoder().decode(JournalRevoke.self, from: conflict.request),
                          let status = try? JSONDecoder().decode(JournalStatus.self, from: conflict.status),
                          revoke.epoch == issue.epoch, revoke.operation == "revoke", revoke.commitment == nil,
                          revoke.attempt.wholeMatch(of: /[a-f0-9]{32}/) != nil,
                          revoke.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil,
                          status.epoch == issue.epoch, status.workspace_id == journal.workspaceID,
                          status.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil, status.revision != revoke.revision,
                          let last = status.last_attempt, last.wholeMatch(of: /[a-f0-9]{32}/) != nil,
                          last != revoke.attempt else { return nil }
                }
            }
            return journal
        }
        guard let reservation = try? JSONDecoder().decode(Enrollment.self, from: data) else { return nil }
        return EnrollmentJournal(version: 1, reservation: reservation, revision: reservation.id,
                                 phase: .legacyUnknown, workspaceID: nil, request: nil)
    }

    /// Synchronize the journal and directory before permitting a network request.
    private static func writeJournal(_ data: Data, _ url: URL) -> Bool {
        guard writeAuthority(data, url) else { return false }
        let file = open(url.path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
        guard file >= 0 else { return false }
        let synced = fsync(file) == 0
        close(file)
        guard synced else { return false }
        let directory = open(url.deletingLastPathComponent().path, O_RDONLY | O_CLOEXEC)
        guard directory >= 0 else { return false }
        defer { close(directory) }
        return fsync(directory) == 0
    }

    private static func removeJournal(_ url: URL) -> Bool {
        guard removeAuthority(url) else { return false }
        let directory = open(url.deletingLastPathComponent().path, O_RDONLY | O_CLOEXEC)
        guard directory >= 0 else { return false }
        defer { close(directory) }
        return fsync(directory) == 0
    }
    private struct WriterToken: Codable, Equatable {
        let authority: Data
        let epoch: String
    }

    private static func authorityURL(for url: URL) -> URL { url.appendingPathExtension("authority") }
    private static func secureModeURL(for url: URL) -> URL { url.appendingPathExtension("secure-mode") }
    private static func enrollmentURL(for url: URL) -> URL { url.appendingPathExtension("enrollment-pending") }

    struct ClearResult {
        let revoked: Bool
        let purged: Bool
        var stale: Bool = false
        var complete: Bool { revoked && purged }
    }

    private static func writeAuthority(_ data: Data, _ url: URL) -> Bool {
        do { try data.write(to: url, options: .atomic); return true } catch { return false }
    }

    private static func removeAuthority(_ url: URL) -> Bool {
        do {
            if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
            return true
        } catch { return false }
    }

    private static func withWriterLock<T>(url: URL, failure: T, _ body: () -> T) -> T {
        let fd = open(url.appendingPathExtension("lock").path,
                      O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { return failure }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_uid == getuid(), info.st_nlink == 1, info.st_mode & 0o077 == 0,
              flock(fd, LOCK_EX) == 0 else { return failure }
        defer { flock(fd, LOCK_UN) }
        return body()
    }

    static func refreshToken(url: URL) -> Data? {
        ActivityCatalog(url: url)?.refreshToken()
    }

    static func legacyRefreshToken(url: URL) -> Data? {
        withWriterLock(url: url, failure: nil) {
            guard !FileManager.default.fileExists(atPath: secureModeURL(for: url).path) else { return nil }
            return ActivityCatalog(url: url)?.refreshToken()
        }
    }

    private func refreshToken() -> Data? {
        let authority: Data
        if FileManager.default.fileExists(atPath: authorityURL.path) {
            guard let data = try? Data(contentsOf: authorityURL) else { return nil }
            authority = data
        } else { authority = Data() }
        guard let statement = prepare("SELECT value FROM writer_epoch WHERE id=1") else { return nil }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW, let value = sqlite3_column_text(statement, 0) else { return nil }
        return try? JSONEncoder().encode(WriterToken(authority: authority, epoch: String(cString: value)))
    }

    private func matches(_ expected: Data) -> Bool {
        guard let current = refreshToken(), let first = try? JSONDecoder().decode(WriterToken.self, from: current),
              let second = try? JSONDecoder().decode(WriterToken.self, from: expected) else { return false }
        return first == second
    }

    private func authorized(_ authority: Authority) -> Bool {
        guard let data = try? Data(contentsOf: authorityURL),
              let current = try? JSONDecoder().decode(Authority.self, from: data) else { return false }
        return current == authority
    }

    private func authority() -> Authority? {
        guard let data = try? Data(contentsOf: authorityURL) else { return nil }
        return try? JSONDecoder().decode(Authority.self, from: data)
    }

    static func activation(url: URL) -> Activation? {
        withWriterLock(url: url, failure: nil) {
            guard let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data),
                  current.enrollment == nil else { return nil }
            return current.activation
        }
    }

    /// A tombstone is not an authorization. Its handle permits only cleanup.
    static func pendingCleanup(url: URL) -> Activation? {
        withWriterLock(url: url, failure: nil) {
            guard let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data),
                  current.revoked == true else { return nil }
            return current.cleanup
        }
    }

    static func workspaceScope(url: URL, endpoint: String, legacy: String) -> String? {
        withWriterLock(url: url, failure: nil) {
            guard FileManager.default.fileExists(atPath: secureModeURL(for: url).path) else { return legacy }
            guard let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data),
                  current.revoked != true, current.enrollment == nil, let activation = current.activation,
                  activation.endpoint == endpoint else { return nil }
            return activation.scope
        }
    }

    static func reserveEnrollment(url: URL, expected: Activation, pairingFingerprint: String? = nil) -> Enrollment? {
        guard pairingFingerprint.map({ $0.wholeMatch(of: /[a-f0-9]{64}/) != nil }) ?? true else { return nil }
        return withWriterLock(url: url, failure: nil) {
            guard !FileManager.default.fileExists(atPath: enrollmentURL(for: url).path) else { return nil }
            guard let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data),
                  current.activation == expected, current.revoked != true, current.enrollment == nil else { return nil }
            let attempt = Enrollment(id: UUID().uuidString, previous: expected)
            // Persist the unresolved attempt first. A crash between these writes
            // may block enrollment unnecessarily, but cannot start a second POST.
            var journal = EnrollmentJournal(version: 1, reservation: attempt, revision: UUID().uuidString,
                                             phase: .preparing, workspaceID: nil, request: nil)
            journal.pairingFingerprint = pairingFingerprint
            guard let marker = try? JSONEncoder().encode(journal),
                  writeJournal(marker, enrollmentURL(for: url)) else { return nil }
            let pending = Authority(scope: "", generation: UUID().uuidString, activation: expected, enrollment: attempt)
            guard let encoded = try? JSONEncoder().encode(pending),
                  writeAuthority(encoded, authorityURL(for: url)) else { return nil }
            return attempt
        }
    }

    /// This handle is non-authorizing. A pending attempt must not cause an
    /// automatic retry, because Core may already have rotated its credential.
    static func pendingEnrollment(url: URL) -> Enrollment? {
        withWriterLock(url: url, failure: nil) {
            guard let data = try? Data(contentsOf: enrollmentURL(for: url)) else { return nil }
            return enrollmentJournal(data)?.reservation
        }
    }

    /// Runs a noninteractive local operation only while both reservation files
    /// still agree. Never use this closure for network or user interaction.
    static func withEnrollment<T>(url: URL, expected: Enrollment, _ action: () -> T?) -> T? {
        withWriterLock(url: url, failure: nil) {
            guard let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data),
                  let marker = try? Data(contentsOf: enrollmentURL(for: url)),
                  enrollmentJournal(marker)?.reservation == expected,
                  current.enrollment == expected, current.activation == expected.previous,
                  current.revoked != true else { return nil }
            return action()
        }
    }

    static func enrollmentPairingMatches(url: URL, expected: Enrollment, fingerprint: String) -> Bool {
        guard fingerprint.wholeMatch(of: /[a-f0-9]{64}/) != nil else { return false }
        return withWriterLock(url: url, failure: false) {
            guard let data = try? Data(contentsOf: enrollmentURL(for: url)),
                  let journal = enrollmentJournal(data), journal.reservation == expected else { return false }
            return journal.cancelled != true && journal.pairingFingerprint == fingerprint
        }
    }

    /// The exact, non-secret mutation must be durable before WebKit sends it.
    static func armEnrollment(url: URL, expected: Enrollment, workspaceID: String, request: Data) -> Bool {
        guard workspaceID.wholeMatch(of: /[a-f0-9]{64}/) != nil, !request.isEmpty, request.count <= 4096 else { return false }
        return withWriterLock(url: url, failure: false) {
            guard let authorityData = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: authorityData),
                  authority.enrollment == expected, authority.activation == expected.previous, authority.revoked != true,
                  let data = try? Data(contentsOf: enrollmentURL(for: url)),
                  let current = enrollmentJournal(data), current.reservation == expected, current.cancelled != true else { return false }
            if current.phase == .armed || current.phase == .receipted {
                return current.workspaceID == workspaceID && current.request == request && writeJournal(data, enrollmentURL(for: url))
            }
            guard current.phase == .preparing || current.phase == .legacyUnknown else { return false }
            var next = EnrollmentJournal(version: 1, reservation: expected, revision: UUID().uuidString,
                                         phase: .armed, workspaceID: workspaceID, request: request)
            next.pairingFingerprint = current.pairingFingerprint
            guard let encoded = try? JSONEncoder().encode(next), enrollmentJournal(encoded) == next else { return false }
            return writeJournal(encoded, enrollmentURL(for: url))
        }
    }

    /// Record a validated receipt and a stable installation identity before
    /// writing the active Keychain item. Exact retries retain both identities.
    static func recordEnrollmentReceipt(url: URL, expected: Enrollment, workspaceID: String,
                                        request: Data, receipt: Data, generation: String) -> EnrollmentJournal? {
        withWriterLock(url: url, failure: nil) {
            guard let authorityData = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: authorityData),
                  authority.enrollment == expected, authority.activation == expected.previous, authority.revoked != true,
                  let marker = try? Data(contentsOf: enrollmentURL(for: url)),
                  let journal = enrollmentJournal(marker), journal.reservation == expected, journal.cancelled != true,
                  journal.workspaceID == workspaceID, journal.request == request else { return nil }
            if journal.phase == .receipted {
                guard journal.receipt == receipt, journal.credentialGeneration == generation,
                      writeJournal(marker, enrollmentURL(for: url)) else { return nil }
                return journal
            }
            guard journal.phase == .armed else { return nil }
            var next = EnrollmentJournal(version: 1, reservation: expected, revision: UUID().uuidString,
                                         phase: .receipted, workspaceID: workspaceID, request: request)
            next.receipt = receipt; next.credentialGeneration = generation; next.activationID = UUID().uuidString
            next.pairingFingerprint = journal.pairingFingerprint
            guard let encoded = try? JSONEncoder().encode(next), enrollmentJournal(encoded) == next,
                  writeJournal(encoded, enrollmentURL(for: url)) else { return nil }
            return next
        }
    }

    static func savedEnrollmentReceipt(url: URL, expected: Enrollment) -> Data? {
        withWriterLock(url: url, failure: nil) {
            guard let data = try? Data(contentsOf: enrollmentURL(for: url)),
                  let journal = enrollmentJournal(data), journal.reservation == expected,
                  journal.phase == .receipted else { return nil }
            return journal.receipt
        }
    }

    enum EnrollmentAdmission {
        case issue(Data)
        case receipt(Data)
    }

    /// Serializes request admission with receipt recording. An exact request
    /// admitted before a receipt may still be in flight; Core deduplicates it.
    static func admitEnrollmentIssue(url: URL, expected: Enrollment, request: Data) -> EnrollmentAdmission? {
        withWriterLock(url: url, failure: nil) {
            guard let authorityData = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: authorityData),
                  authority.enrollment == expected, authority.activation == expected.previous, authority.revoked != true,
                  let marker = try? Data(contentsOf: enrollmentURL(for: url)),
                  let journal = enrollmentJournal(marker), journal.reservation == expected, journal.cancelled != true,
                  journal.request == request, writeJournal(marker, enrollmentURL(for: url)) else { return nil }
            if journal.phase == .receipted, let receipt = journal.receipt { return .receipt(receipt) }
            guard journal.phase == .armed else { return nil }
            return .issue(request)
        }
    }

    enum EnrollmentRecoveryPlan {
        case revoke(Data)
        case superseded
    }

    private static func ownsRecoveryTombstone(_ authority: Authority, journal: EnrollmentJournal, expected: Enrollment) -> Bool {
        guard authority.revoked == true, let cleanup = authority.cleanup else { return false }
        if cleanup == expected.previous { return true }
        guard journal.phase == .receipted, let id = journal.activationID, let workspace = journal.workspaceID,
              let generation = journal.credentialGeneration, cleanup.id == id,
              cleanup.endpoint == expected.previous.endpoint, cleanup.workspaceID == workspace else { return false }
        return cleanup.credentialGeneration == generation || cleanup.credentialGeneration == nil
    }

    static func cancelledRecoveryMatches(url: URL, expected: Enrollment, fingerprint: String) -> Bool {
        guard fingerprint.wholeMatch(of: /[a-f0-9]{64}/) != nil else { return false }
        return withWriterLock(url: url, failure: false) {
            guard let marker = try? Data(contentsOf: enrollmentURL(for: url)), let journal = enrollmentJournal(marker),
                  journal.reservation == expected, journal.cancelled == true, (journal.phase == .armed || journal.phase == .receipted),
                  journal.pairingFingerprint == fingerprint,
                  let data = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: data) else { return false }
            return ownsRecoveryTombstone(authority, journal: journal, expected: expected)
        }
    }

    /// Only a cancelled, pairing-bound reservation may plan remote cleanup.
    /// The observed status and exact conditional revoke are synced before use.
    static func planCancelledRecovery(url: URL, expected: Enrollment, fingerprint: String,
                                      observedStatus: Data) -> EnrollmentRecoveryPlan? {
        guard observedStatus.count <= 4096,
              let status = try? JSONDecoder().decode(JournalStatus.self, from: observedStatus) else { return nil }
        return withWriterLock(url: url, failure: nil) {
            let markerURL = enrollmentURL(for: url)
            guard let marker = try? Data(contentsOf: markerURL), var journal = enrollmentJournal(marker),
                  journal.reservation == expected, journal.cancelled == true, (journal.phase == .armed || journal.phase == .receipted),
                  journal.pairingFingerprint == fingerprint, fingerprint.wholeMatch(of: /[a-f0-9]{64}/) != nil,
                  let issueData = journal.request, let issue = try? JSONDecoder().decode(JournalRequest.self, from: issueData),
                  status.epoch == issue.epoch, status.workspace_id == journal.workspaceID,
                  status.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil,
                  status.last_attempt.map({ $0.wholeMatch(of: /[a-f0-9]{32}/) != nil }) ?? true,
                  let data = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: data),
                  ownsRecoveryTombstone(authority, journal: journal, expected: expected) else { return nil }
            if journal.recoveryReceipt != nil {
                guard writeJournal(marker, markerURL) else { return nil }
                return .superseded
            }
            if let existing = journal.recoveryRequest {
                guard writeJournal(marker, markerURL) else { return nil }
                return .revoke(existing)
            }
            if journal.recoveryStatus != nil {
                guard writeJournal(marker, markerURL) else { return nil }
                return .superseded
            }
            let isSuperseded = status.revision != issue.revision && status.last_attempt != nil && status.last_attempt != issue.attempt
            guard isSuperseded || status.revision == issue.revision || status.last_attempt == issue.attempt else { return nil }
            journal.recoveryStatus = observedStatus
            if !isSuperseded {
                let attempt = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
                let payload: [String: Any] = ["epoch": issue.epoch, "revision": status.revision,
                    "attempt": attempt, "operation": "revoke", "commitment": NSNull()]
                guard let request = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) else { return nil }
                journal.recoveryRequest = request
            }
            journal.revision = UUID().uuidString
            guard let encoded = try? JSONEncoder().encode(journal), enrollmentJournal(encoded) == journal,
                  writeJournal(encoded, markerURL) else { return nil }
            return journal.recoveryRequest.map(EnrollmentRecoveryPlan.revoke) ?? .superseded
        }
    }

    static func recordRecoveryReceipt(url: URL, expected: Enrollment, fingerprint: String,
                                      request: Data, receipt: Data) -> Bool {
        withWriterLock(url: url, failure: false) {
            let markerURL = enrollmentURL(for: url)
            guard let marker = try? Data(contentsOf: markerURL), var journal = enrollmentJournal(marker),
                  journal.reservation == expected, journal.cancelled == true, journal.pairingFingerprint == fingerprint,
                  journal.recoveryRequest == request,
                  let authorityData = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: authorityData),
                  ownsRecoveryTombstone(authority, journal: journal, expected: expected) else { return false }
            if let existing = journal.recoveryReceipt {
                return existing == receipt && writeJournal(marker, markerURL)
            }
            journal.recoveryReceipt = receipt; journal.revision = UUID().uuidString
            guard let encoded = try? JSONEncoder().encode(journal), enrollmentJournal(encoded) == journal else { return false }
            return writeJournal(encoded, markerURL)
        }
    }

    /// After an authenticated 409, a freshly fetched advanced revision proves
    /// that the old conditional request cannot commit. Preserve both as evidence.
    static func resolveRecoveryConflict(url: URL, expected: Enrollment, fingerprint: String,
                                        failedRequest: Data, observedStatus: Data) -> EnrollmentRecoveryPlan? {
        guard observedStatus.count <= 4096, let status = try? JSONDecoder().decode(JournalStatus.self, from: observedStatus) else { return nil }
        return withWriterLock(url: url, failure: nil) {
            let markerURL = enrollmentURL(for: url)
            guard let marker = try? Data(contentsOf: markerURL), var journal = enrollmentJournal(marker),
                  journal.reservation == expected, journal.cancelled == true, journal.pairingFingerprint == fingerprint,
                  journal.recoveryRequest == failedRequest, journal.recoveryReceipt == nil,
                  let revoke = try? JSONDecoder().decode(JournalRevoke.self, from: failedRequest),
                  let issueData = journal.request, let issue = try? JSONDecoder().decode(JournalRequest.self, from: issueData),
                  status.epoch == issue.epoch, status.workspace_id == journal.workspaceID,
                  status.revision.wholeMatch(of: /[a-f0-9]{32}/) != nil, status.revision != revoke.revision,
                  status.revision != issue.revision,
                  let last = status.last_attempt, last.wholeMatch(of: /[a-f0-9]{32}/) != nil, last != revoke.attempt,
                  let data = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: data),
                  ownsRecoveryTombstone(authority, journal: journal, expected: expected) else { return nil }
            var history = journal.recoveryConflicts ?? []
            guard history.count < 8 else { return nil }
            history.append(RecoveryConflict(request: failedRequest, status: observedStatus))
            journal.recoveryConflicts = history; journal.recoveryStatus = observedStatus; journal.recoveryRequest = nil
            if last == issue.attempt {
                let attempt = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
                let payload: [String: Any] = ["epoch": issue.epoch, "revision": status.revision,
                    "attempt": attempt, "operation": "revoke", "commitment": NSNull()]
                guard let request = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]) else { return nil }
                journal.recoveryRequest = request
            }
            journal.revision = UUID().uuidString
            guard let encoded = try? JSONEncoder().encode(journal), enrollmentJournal(encoded) == journal,
                  writeJournal(encoded, markerURL) else { return nil }
            return journal.recoveryRequest.map(EnrollmentRecoveryPlan.revoke) ?? .superseded
        }
    }

    /// Terminal evidence is durable before owned cleanup; the logout tombstone remains.
    static func finishCancelledRecovery(url: URL, expected: Enrollment, fingerprint: String?,
                                        journalSync: (Data, URL) -> Bool = ActivityCatalog.writeJournal,
                                        verify: (EnrollmentJournal) -> Bool, cleanup: () -> Bool) -> ClearResult {
        withWriterLock(url: url, failure: ClearResult(revoked: false, purged: false)) {
            let markerURL = enrollmentURL(for: url)
            guard let marker = try? Data(contentsOf: markerURL), let journal = enrollmentJournal(marker),
                  journal.reservation == expected, journal.cancelled == true, journal.pairingFingerprint != nil,
                  fingerprint.map({ journal.pairingFingerprint == $0 }) ?? true,
                  journal.recoveryStatus != nil, journal.recoveryRequest == nil || journal.recoveryReceipt != nil,
                  let authorityData = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: authorityData),
                  ownsRecoveryTombstone(authority, journal: journal, expected: expected) else {
                return ClearResult(revoked: false, purged: false, stale: true)
            }
            guard writeJournal(authorityData, authorityURL(for: url)), journalSync(marker, markerURL),
                  verify(journal), cleanup(), ActivityCatalog(url: url)?.purge() == true,
                  removeJournal(markerURL) else { return ClearResult(revoked: true, purged: false) }
            return ClearResult(revoked: true, purged: true)
        }
    }

    /// Publish only the activation named by the durable receipt. The journal
    /// remains until the pending item is deleted and marker removal is synced.
    static func installReceipted(url: URL, expected: Enrollment, receipt: EnrollmentJournal,
                                 syncAuthority: (Data, URL) -> Bool = ActivityCatalog.writeJournal,
                                 retireMarker: (URL) -> Bool = ActivityCatalog.removeJournal,
                                 install: () -> Bool, authorize: () -> Bool, cleanup: () -> Bool) -> (Activation, Bool)? {
        withWriterLock(url: url, failure: nil) {
            guard let marker = try? Data(contentsOf: enrollmentURL(for: url)),
                  let currentJournal = enrollmentJournal(marker), currentJournal == receipt,
                  receipt.phase == .receipted, let workspace = receipt.workspaceID,
                  let generation = receipt.credentialGeneration, let id = receipt.activationID,
                  let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data),
                  current.enrollment == expected, current.activation == expected.previous, current.revoked != true,
                  receipt.reservation == expected, writeJournal(marker, enrollmentURL(for: url)), install() else { return nil }
            let next = Activation(id: id, endpoint: expected.previous.endpoint,
                                  workspaceID: workspace, credentialGeneration: generation)
            let authority = Authority(scope: "", generation: UUID().uuidString, activation: next)
            guard authorize(), let encoded = try? JSONEncoder().encode(authority),
                  syncAuthority(encoded, authorityURL(for: url)) else { return nil }
            let complete = cleanup() && retireMarker(enrollmentURL(for: url))
            return (next, complete)
        }
    }

    static func finishReceipted(url: URL, expected: Enrollment,
                                verify: (EnrollmentJournal, Activation) -> Bool,
                                cleanup: () -> Bool) -> Activation? {
        withWriterLock(url: url, failure: nil) {
            let markerURL = enrollmentURL(for: url)
            guard let marker = try? Data(contentsOf: markerURL), let journal = enrollmentJournal(marker),
                  journal.reservation == expected, journal.phase == .receipted,
                  let data = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: data),
                  authority.revoked != true, authority.enrollment == nil, let active = authority.activation,
                  active.id == journal.activationID, active.endpoint == expected.previous.endpoint,
                  active.workspaceID == journal.workspaceID, active.credentialGeneration == journal.credentialGeneration,
                  writeJournal(marker, markerURL), writeJournal(data, authorityURL(for: url)),
                  verify(journal, active), cleanup(), removeJournal(markerURL) else { return nil }
            return active
        }
    }

    /// A committed but expired receipt cannot authorize installation. Retire
    /// only the matching reservation after durable revocation and owned cleanup.
    static func retireExpiredReceipt(url: URL, expected: Enrollment,
                                     verify: (EnrollmentJournal) -> Bool,
                                     retireMarker: (URL) -> Bool = ActivityCatalog.removeJournal,
                                     cleanup: () -> Bool) -> ClearResult {
        withWriterLock(url: url, failure: ClearResult(revoked: false, purged: false)) {
            let markerURL = enrollmentURL(for: url)
            guard let marker = try? Data(contentsOf: markerURL), let journal = enrollmentJournal(marker),
                  journal.reservation == expected, journal.phase == .receipted,
                  journal.recoveryStatus == nil, journal.recoveryRequest == nil, journal.recoveryReceipt == nil,
                  let id = journal.activationID, let workspace = journal.workspaceID,
                  let data = try? Data(contentsOf: authorityURL(for: url)),
                  let authority = try? JSONDecoder().decode(Authority.self, from: data) else {
                return ClearResult(revoked: false, purged: false)
            }
            let pinned = Activation(id: id, endpoint: expected.previous.endpoint, workspaceID: workspace, credentialGeneration: nil)
            let installed = Activation(id: id, endpoint: expected.previous.endpoint, workspaceID: workspace,
                                       credentialGeneration: journal.credentialGeneration)
            if authority.revoked == true && authority.expirationRetirement != expected.id &&
                [expected.previous, pinned, installed].contains(where: { $0 == authority.cleanup }) {
                // Explicit logout may finish owned cleanup but can never restore authorization.
                guard writeJournal(data, authorityURL(for: url)), writeJournal(marker, markerURL),
                      verify(journal), cleanup(), ActivityCatalog(url: url)?.purge() == true,
                      retireMarker(markerURL) else { return ClearResult(revoked: true, purged: false) }
                return ClearResult(revoked: true, purged: true)
            }
            if authority.enrollment == expected && authority.activation == expected.previous && authority.revoked != true {
                let tombstone = Authority(scope: "", generation: UUID().uuidString, revoked: true,
                                          cleanup: expected.previous, expirationRetirement: expected.id)
                guard let encoded = try? JSONEncoder().encode(tombstone),
                      writeJournal(encoded, authorityURL(for: url)) else { return ClearResult(revoked: false, purged: false) }
            } else if authority.activation == installed && authority.enrollment == nil && authority.revoked != true {
                let tombstone = Authority(scope: "", generation: UUID().uuidString, revoked: true,
                                          cleanup: installed, expirationRetirement: expected.id)
                guard let encoded = try? JSONEncoder().encode(tombstone),
                      writeJournal(encoded, authorityURL(for: url)) else { return ClearResult(revoked: false, purged: false) }
            } else if !(authority.revoked == true && [expected.previous, installed].contains(where: { $0 == authority.cleanup }) &&
                         authority.expirationRetirement == expected.id) &&
                        !(authority.activation == pinned && authority.enrollment == nil && authority.revoked != true) {
                return ClearResult(revoked: false, purged: false, stale: true)
            } else if !writeJournal(data, authorityURL(for: url)) {
                return ClearResult(revoked: false, purged: false)
            }
            guard writeJournal(marker, markerURL), verify(journal), cleanup(),
                  ActivityCatalog(url: url)?.purge() == true else {
                return ClearResult(revoked: true, purged: false)
            }
            let pinnedAuthority = Authority(scope: "", generation: UUID().uuidString, activation: pinned)
            guard let pinnedData = try? JSONEncoder().encode(pinnedAuthority),
                  writeJournal(pinnedData, authorityURL(for: url)), retireMarker(markerURL) else {
                return ClearResult(revoked: true, purged: false)
            }
            return ClearResult(revoked: true, purged: true)
        }
    }

    /// A preparing journal has never authorized an issue POST. Repairing this
    /// interrupted local reservation must never restore a tombstoned authority.
    static func abandonPreparing(url: URL, expected: Enrollment,
                                 syncAuthority: (Data, URL) -> Bool = ActivityCatalog.writeJournal,
                                 retireMarker: (URL) -> Bool = ActivityCatalog.removeJournal,
                                 deletePending: () -> Bool) -> Bool {
        withWriterLock(url: url, failure: false) {
            let markerURL = enrollmentURL(for: url)
            guard let marker = try? Data(contentsOf: markerURL), let journal = enrollmentJournal(marker),
                  journal.reservation == expected, journal.phase == .preparing,
                  writeJournal(marker, markerURL),
                  let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data) else { return false }
            if current.enrollment == expected && current.activation == expected.previous && current.revoked != true {
                let restored = Authority(scope: "", generation: UUID().uuidString, activation: expected.previous)
                guard let encoded = try? JSONEncoder().encode(restored),
                      syncAuthority(encoded, authorityURL(for: url)) else { return false }
            } else if !(current.activation == expected.previous && current.enrollment == nil && current.revoked != true) &&
                        !(current.revoked == true && current.cleanup == expected.previous) {
                return false
            } else if !syncAuthority(data, authorityURL(for: url)) {
                return false
            }
            guard deletePending() else { return false }
            return retireMarker(markerURL)
        }
    }

    static func completeEnrollment(url: URL, expected: Enrollment, workspaceID: String,
                                   generation: String, install: () -> Bool,
                                   write: (Data, URL) -> Bool = ActivityCatalog.writeAuthority) -> Activation? {
        guard workspaceID.wholeMatch(of: /[a-f0-9]{64}/) != nil,
              expected.previous.workspaceID == nil || expected.previous.workspaceID == workspaceID,
              UUID(uuidString: generation) != nil, generation != expected.previous.credentialGeneration else { return nil }
        return withWriterLock(url: url, failure: nil) {
            guard let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data),
                  let marker = try? Data(contentsOf: enrollmentURL(for: url)),
                  enrollmentJournal(marker)?.reservation == expected,
                  current.enrollment == expected, current.activation == expected.previous,
                  current.revoked != true, install() else { return nil }
            let next = Activation(id: UUID().uuidString, endpoint: expected.previous.endpoint,
                                  workspaceID: workspaceID, credentialGeneration: generation)
            let authority = Authority(scope: "", generation: UUID().uuidString, activation: next)
            guard let encoded = try? JSONEncoder().encode(authority),
                  write(encoded, authorityURL(for: url)) else { return nil }
            _ = removeAuthority(enrollmentURL(for: url))
            return next
        }
    }

    /// Explicit user clearing may cancel a live reservation locally but must
    /// retain its independent unknown-outcome marker until safely resolved.
    static func clearEnrollment(url: URL, expected: Enrollment, journalCheck: (EnrollmentJournal) -> Bool,
                                deleteCredential: () -> Bool) -> ClearResult {
        withWriterLock(url: url, failure: ClearResult(revoked: false, purged: false)) {
            guard let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data) else {
                return ClearResult(revoked: false, purged: false)
            }
            guard current.enrollment == expected, current.activation == expected.previous else {
                return ClearResult(revoked: false, purged: false, stale: true)
            }
            let tombstone = Authority(scope: "", generation: UUID().uuidString, revoked: true, cleanup: expected.previous)
            guard let encoded = try? JSONEncoder().encode(tombstone),
                  writeJournal(encoded, authorityURL(for: url)) else { return ClearResult(revoked: false, purged: false) }
            let markerURL = enrollmentURL(for: url)
            guard let marker = try? Data(contentsOf: markerURL), let journal = enrollmentJournal(marker),
                  journal.reservation == expected, journal.phase != .legacyUnknown,
                  writeJournal(marker, markerURL) else { return ClearResult(revoked: true, purged: false) }
            var cancelled = journal
            cancelled.cancelled = true; cancelled.revision = UUID().uuidString
            guard let cancellation = try? JSONEncoder().encode(cancelled),
                  writeJournal(cancellation, markerURL), journalCheck(cancelled) else { return ClearResult(revoked: true, purged: false) }
            let deleted = deleteCredential()
            let purged = ActivityCatalog(url: url)?.purge() ?? false
            return ClearResult(revoked: true, purged: deleted && purged)
        }
    }

    /// Start a new activation by revoking the old publication before credential work.
    /// The endpoint must be canonicalized and transport-validated by the caller.
    static func activate(url: URL, endpoint: String) -> Activation? {
        guard let parts = URLComponents(string: endpoint), parts.scheme == "https",
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.path.isEmpty, parts.query == nil, parts.fragment == nil,
              parts.port.map({ (1...65535).contains($0) }) ?? true else { return nil }
        return withWriterLock(url: url, failure: nil) {
            // This non-secret marker prevents an absent or damaged
            // authority from being interpreted as a legacy writable store.
            guard writeAuthority(Data("1".utf8), secureModeURL(for: url)) else { return nil }
            let activation = Activation(id: UUID().uuidString, endpoint: endpoint,
                                        workspaceID: nil, credentialGeneration: nil)
            let next = Authority(scope: "", generation: UUID().uuidString, activation: activation)
            guard let data = try? JSONEncoder().encode(next), writeAuthority(data, authorityURL(for: url)) else { return nil }
            return activation
        }
    }

    /// Install while fenced against switch/forget. A failed authority write leaves
    /// an unusable orphan in Keychain; no secret is put in the shared container.
    static func installCredential(url: URL, expected: Activation, workspaceID: String,
                                  generation: String, install: () -> Bool,
                                  write: (Data, URL) -> Bool = ActivityCatalog.writeAuthority) -> Activation? {
        guard workspaceID.wholeMatch(of: /[a-f0-9]{64}/) != nil,
              expected.workspaceID == nil || expected.workspaceID == workspaceID,
              UUID(uuidString: generation) != nil, generation != expected.credentialGeneration else { return nil }
        return withWriterLock(url: url, failure: nil) {
            guard let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data),
                  current.activation == expected, current.enrollment == nil, install() else { return nil }
            // Every successful installation creates a distinct CAS identity,
            // even if a caller later reuses an older credential generation.
            let next = Activation(id: UUID().uuidString, endpoint: expected.endpoint,
                                  workspaceID: workspaceID, credentialGeneration: generation)
            let authority = Authority(scope: "", generation: UUID().uuidString, activation: next)
            guard let encoded = try? JSONEncoder().encode(authority),
                  write(encoded, authorityURL(for: url)) else { return nil }
            return next
        }
    }

    /// Revoke before attempting noninteractive Keychain deletion; a stale failure
    /// cannot invalidate a later activation or replacement credential.
    static func invalidate(url: URL, expected: Activation, recovery: Enrollment? = nil,
                           journalSync: (Data, URL) -> Bool = ActivityCatalog.writeJournal,
                           recoveryCheck: (EnrollmentJournal) -> Bool = { _ in false },
                           deleteCredential: () -> Bool) -> ClearResult {
        withWriterLock(url: url, failure: ClearResult(revoked: false, purged: false)) {
            guard let data = try? Data(contentsOf: authorityURL(for: url)),
                  let current = try? JSONDecoder().decode(Authority.self, from: data) else {
                return ClearResult(revoked: false, purged: false)
            }
            if current.activation == expected && current.enrollment == nil {
                let tombstone = Authority(scope: "", generation: UUID().uuidString, revoked: true, cleanup: expected)
                guard let encoded = try? JSONEncoder().encode(tombstone),
                      writeJournal(encoded, authorityURL(for: url)) else { return ClearResult(revoked: false, purged: false) }
            } else if current.revoked != true || current.cleanup != expected {
                return ClearResult(revoked: false, purged: false, stale: true)
            } else if current.expirationRetirement != nil {
                // A new clear request supersedes any automatic expiry recovery.
                let tombstone = Authority(scope: "", generation: UUID().uuidString, revoked: true, cleanup: expected)
                guard let encoded = try? JSONEncoder().encode(tombstone),
                      writeJournal(encoded, authorityURL(for: url)) else { return ClearResult(revoked: true, purged: false) }
            }
            let markerURL = enrollmentURL(for: url)
            var markerInfo = stat()
            let markerStatus = lstat(markerURL.path, &markerInfo)
            if markerStatus != 0 && errno != ENOENT { return ClearResult(revoked: true, purged: false) }
            if markerStatus == 0 {
                guard let recovery else { return ClearResult(revoked: true, purged: false) }
                guard let marker = try? Data(contentsOf: markerURL),
                      let journal = enrollmentJournal(marker), journal.reservation == recovery,
                      (recovery.previous == expected || (journal.phase == .receipted &&
                       journal.activationID == expected.id && journal.workspaceID == expected.workspaceID &&
                       journal.credentialGeneration == expected.credentialGeneration && recovery.previous.endpoint == expected.endpoint)),
                      journal.phase != .legacyUnknown, journalSync(marker, markerURL) else {
                    return ClearResult(revoked: true, purged: false)
                }
                var cancelled = journal
                cancelled.cancelled = true; cancelled.revision = UUID().uuidString
                guard let cancellation = try? JSONEncoder().encode(cancelled), writeJournal(cancellation, markerURL),
                      recoveryCheck(cancelled) else { return ClearResult(revoked: true, purged: false) }
            } else if recovery != nil {
                return ClearResult(revoked: true, purged: false)
            }
            let deleted = deleteCredential()
            let purged = ActivityCatalog(url: url)?.purge() ?? false
            return ClearResult(revoked: true, purged: deleted && purged)
        }
    }

    init?(url: URL) {
        databaseURL = url
        authorityURL = Self.authorityURL(for: url)
        guard sqlite3_open_v2(url.path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else { return nil }
        sqlite3_busy_timeout(db, 2000)
        guard run("PRAGMA secure_delete=ON"),
              run("CREATE TABLE IF NOT EXISTS snapshot (scope TEXT PRIMARY KEY, generation TEXT NOT NULL, data BLOB NOT NULL)"),
              run("CREATE TABLE IF NOT EXISTS catalog_v2 (id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL, activity TEXT, asks INTEGER, approved REAL, ordinal INTEGER NOT NULL)"),
              run("CREATE TABLE IF NOT EXISTS snapshots_v2 (scope TEXT NOT NULL, generation TEXT PRIMARY KEY, data BLOB NOT NULL)"),
              run("CREATE TABLE IF NOT EXISTS writer_epoch (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"),
              run("INSERT OR IGNORE INTO writer_epoch VALUES (1, 'initial')"),
              run("CREATE TABLE IF NOT EXISTS catalog_v3 (generation TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, state TEXT NOT NULL, activity TEXT, asks INTEGER, approved REAL, ordinal INTEGER NOT NULL, PRIMARY KEY (generation, id))"),
              run("CREATE INDEX IF NOT EXISTS catalog_v3_name ON catalog_v3(generation, name COLLATE NOCASE, id)"),
              run("CREATE INDEX IF NOT EXISTS catalog_v3_id ON catalog_v3(generation, id COLLATE NOCASE)"),
              run("INSERT OR IGNORE INTO snapshots_v2 SELECT scope, generation, data FROM snapshot"),
              run("INSERT OR IGNORE INTO catalog_v3 SELECT snapshot.generation, catalog_v2.id, catalog_v2.name, catalog_v2.state, catalog_v2.activity, catalog_v2.asks, catalog_v2.approved, catalog_v2.ordinal FROM catalog_v2 CROSS JOIN snapshot") else { return nil }
    }

    deinit { sqlite3_close(db) }

    private func run(_ sql: String) -> Bool { sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK }

    private func prepare(_ sql: String) -> OpaquePointer? {
        var statement: OpaquePointer?
        return sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK ? statement : nil
    }

    private func bind(_ text: String, to statement: OpaquePointer?, at index: Int32) {
        sqlite3_bind_text(statement, index, text, -1, Self.transient)
    }

    func publish(scope: String, generation: String, snapshot: Data, entries: [CatalogHeinzel], expectedToken: Data,
                 write: (Data, URL) -> Bool = ActivityCatalog.writeAuthority) -> Bool {
        Self.withWriterLock(url: databaseURL, failure: false) {
            publishLocked(scope: scope, generation: generation, snapshot: snapshot, entries: entries,
                          expectedToken: expectedToken, preserveCatalog: false, write: write)
        }
    }

    /// Publish only a bounded projection while retaining every existing catalog row.
    /// Validate the current snapshot under the same lock and never recapture the token.
    func publishPartial(scope: String, generation: String, snapshot: Data, entries: [CatalogHeinzel], expectedToken: Data,
                        acceptPrevious: (Data) -> Bool) -> Bool {
        Self.withWriterLock(url: databaseURL, failure: false) {
            guard matches(expectedToken), let current = authority(), current.activation?.scope == scope,
                  current.scope == scope, let previous = self.snapshot(), acceptPrevious(previous) else { return false }
            return publishLocked(scope: scope, generation: generation, snapshot: snapshot, entries: entries,
                                 expectedToken: expectedToken, preserveCatalog: true, write: Self.writeAuthority)
        }
    }

    private func publishLocked(scope: String, generation: String, snapshot: Data, entries: [CatalogHeinzel],
                               expectedToken: Data, preserveCatalog: Bool, write: (Data, URL) -> Bool) -> Bool {
        guard matches(expectedToken) else { return false }
        guard run("BEGIN IMMEDIATE") else { return false }
        var committed = false
        defer { if !committed { _ = run("ROLLBACK") } }
        guard let token = try? JSONDecoder().decode(WriterToken.self, from: expectedToken) else { return false }
        let previous = try? JSONDecoder().decode(Authority.self, from: token.authority)
        guard token.authority.isEmpty || previous != nil else { return false }
        guard previous?.enrollment == nil else { return false }
        if FileManager.default.fileExists(atPath: Self.secureModeURL(for: databaseURL).path),
           previous?.activation == nil { return false }
        guard previous?.revoked != true else { return false }
        if let activation = previous?.activation {
            guard let boundScope = activation.scope, boundScope == scope else { return false }
        }
        let current = previous?.generation ?? ""
        guard run("INSERT OR IGNORE INTO snapshots_v2 SELECT scope, generation, data FROM snapshot"),
              run("INSERT OR IGNORE INTO catalog_v3 SELECT snapshot.generation, catalog_v2.id, catalog_v2.name, catalog_v2.state, catalog_v2.activity, catalog_v2.asks, catalog_v2.approved, catalog_v2.ordinal FROM catalog_v2 CROSS JOIN snapshot"),
              let cleanItems = prepare("DELETE FROM catalog_v3 WHERE generation != ?"),
              let cleanSnapshots = prepare("DELETE FROM snapshots_v2 WHERE generation != ?") else { return false }
        defer { sqlite3_finalize(cleanItems); sqlite3_finalize(cleanSnapshots) }
        bind(current, to: cleanItems, at: 1); bind(current, to: cleanSnapshots, at: 1)
        guard sqlite3_step(cleanItems) == SQLITE_DONE, sqlite3_step(cleanSnapshots) == SQLITE_DONE else { return false }
        if preserveCatalog {
            guard let copy = prepare("INSERT INTO catalog_v3 SELECT ?, id, name, state, activity, asks, approved, ordinal FROM catalog_v3 WHERE generation=?") else { return false }
            defer { sqlite3_finalize(copy) }
            bind(generation, to: copy, at: 1); bind(current, to: copy, at: 2)
            guard sqlite3_step(copy) == SQLITE_DONE else { return false }
        }
        let insertSQL = preserveCatalog
            ? "INSERT INTO catalog_v3 VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(generation,id) DO UPDATE SET name=excluded.name, state=excluded.state, activity=excluded.activity, asks=excluded.asks"
            : "INSERT INTO catalog_v3 VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        guard let insert = prepare(insertSQL) else { return false }
        defer { sqlite3_finalize(insert) }
        for (ordinal, item) in entries.enumerated() {
            bind(generation, to: insert, at: 1); bind(item.id, to: insert, at: 2)
            bind(item.name, to: insert, at: 3); bind(item.state, to: insert, at: 4)
            if let activity = item.activity { bind(activity, to: insert, at: 5) } else { sqlite3_bind_null(insert, 5) }
            if let asks = item.asks { sqlite3_bind_int64(insert, 6, Int64(asks)) } else { sqlite3_bind_null(insert, 6) }
            if let approved = item.lastApproved { sqlite3_bind_double(insert, 7, approved) } else { sqlite3_bind_null(insert, 7) }
            sqlite3_bind_int64(insert, 8, Int64(ordinal))
            guard sqlite3_step(insert) == SQLITE_DONE else { return false }
            sqlite3_reset(insert); sqlite3_clear_bindings(insert)
        }
        guard let statement = prepare("INSERT INTO snapshots_v2 VALUES (?, ?, ?)") else { return false }
        defer { sqlite3_finalize(statement) }
        bind(scope, to: statement, at: 1)
        bind(generation, to: statement, at: 2)
        snapshot.withUnsafeBytes { bytes in
            _ = sqlite3_bind_blob(statement, 3, bytes.baseAddress, Int32(bytes.count), Self.transient)
        }
        guard sqlite3_step(statement) == SQLITE_DONE, run("COMMIT") else { return false }
        committed = true
        guard matches(expectedToken) else { return false }
        guard let authority = try? JSONEncoder().encode(Authority(scope: scope, generation: generation, activation: previous?.activation)) else { return false }
        guard write(authority, authorityURL) else { return false }
        _ = run("DELETE FROM catalog_v2")
        _ = run("DELETE FROM snapshot")
        _ = run("DROP TABLE IF EXISTS catalog")
        return true
    }

    // Revoke the cross-process authority before opening or locking SQLite. A failed
    // purge can leave bytes on disk, but no reader may use that generation.
    static func clear(url: URL, expectedToken: Data? = nil, write: (Data, URL) -> Bool = ActivityCatalog.writeAuthority,
                      remove: (URL) -> Bool = ActivityCatalog.removeAuthority) -> ClearResult {
        withWriterLock(url: url, failure: ClearResult(revoked: false, purged: false)) {
            if let expectedToken {
                guard let currentData = ActivityCatalog(url: url)?.refreshToken(),
                      let current = try? JSONDecoder().decode(WriterToken.self, from: currentData),
                      let expected = try? JSONDecoder().decode(WriterToken.self, from: expectedToken) else {
                    return ClearResult(revoked: false, purged: false)
                }
                if current != expected { return ClearResult(revoked: false, purged: false, stale: true) }
            }
            let authority = authorityURL(for: url)
            let previousData = try? Data(contentsOf: authority)
            let previous = previousData.flatMap { try? JSONDecoder().decode(Authority.self, from: $0) }
            // Secure activations must retain their tombstone: deleting the authority
            // would make a later writer look like an unconfigured legacy install.
            let secure = FileManager.default.fileExists(atPath: secureModeURL(for: url).path) ||
                previous?.activation != nil || previous?.revoked == true
            let next = Authority(scope: "", generation: UUID().uuidString,
                                 revoked: secure ? true : nil, cleanup: previous?.activation ?? previous?.cleanup)
            let tombstone = try? JSONEncoder().encode(next)
            let written = tombstone.map { write($0, authority) } == true
            let revoked = written || (!secure && remove(authority))
            // Cleanup is independent: even if authority revocation failed, removing
            // every generation makes the previous authority resolve to no data.
            let purged = ActivityCatalog(url: url)?.purge() ?? false
            return ClearResult(revoked: revoked, purged: purged)
        }
    }

    private func purge() -> Bool {
        guard run("BEGIN IMMEDIATE") else { return false }
        guard let epoch = prepare("UPDATE writer_epoch SET value=? WHERE id=1") else { _ = run("ROLLBACK"); return false }
        defer { sqlite3_finalize(epoch) }
        bind(UUID().uuidString, to: epoch, at: 1)
        guard sqlite3_step(epoch) == SQLITE_DONE else { _ = run("ROLLBACK"); return false }
        guard run("DROP TABLE IF EXISTS catalog"), run("DELETE FROM catalog_v2"), run("DELETE FROM snapshot"),
              run("DELETE FROM catalog_v3"), run("DELETE FROM snapshots_v2"), run("COMMIT") else { _ = run("ROLLBACK"); return false }
        return true
    }

    func snapshot() -> Data? {
        guard let token = try? Data(contentsOf: authorityURL),
              let authority = try? JSONDecoder().decode(Authority.self, from: token),
              let statement = prepare("SELECT data FROM snapshots_v2 WHERE scope=? AND generation=?") else { return nil }
        defer { sqlite3_finalize(statement) }
        bind(authority.scope, to: statement, at: 1); bind(authority.generation, to: statement, at: 2)
        guard sqlite3_step(statement) == SQLITE_ROW, let bytes = sqlite3_column_blob(statement, 0) else { return nil }
        guard authorized(authority) else { return nil }
        return Data(bytes: bytes, count: Int(sqlite3_column_bytes(statement, 0)))
    }

    func search(scope: String, generation: String, query: String = "", offset: Int = 0, limit: Int = 50) -> [CatalogHeinzel] {
        guard let authority = authority(), authority.scope == scope, authority.generation == generation,
              authorized(authority) else { return [] }
        guard let statement = prepare("SELECT id, name, state, activity, asks, approved FROM catalog_v3 WHERE EXISTS (SELECT 1 FROM snapshots_v2 WHERE scope=? AND generation=?) AND generation=? AND (name LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\') ORDER BY name COLLATE NOCASE, id LIMIT ? OFFSET ?") else { return [] }
        defer { sqlite3_finalize(statement) }
        bind(scope, to: statement, at: 1)
        bind(generation, to: statement, at: 2)
        let escaped = query.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "%", with: "\\%").replacingOccurrences(of: "_", with: "\\_")
        bind(generation, to: statement, at: 3)
        bind(escaped + "%", to: statement, at: 4)
        bind(escaped + "%", to: statement, at: 5)
        sqlite3_bind_int(statement, 6, Int32(max(0, min(limit, 50))))
        sqlite3_bind_int64(statement, 7, Int64(max(0, offset)))
        let result = rows(statement)
        return authorized(authority) ? result : []
    }

    func find(scope: String, generation: String, id: String) -> CatalogHeinzel? {
        guard let authority = authority(), authority.scope == scope, authority.generation == generation,
              authorized(authority) else { return nil }
        guard let statement = prepare("SELECT id, name, state, activity, asks, approved FROM catalog_v3 WHERE id=? AND EXISTS (SELECT 1 FROM snapshots_v2 WHERE scope=? AND generation=?) AND generation=?") else { return nil }
        defer { sqlite3_finalize(statement) }
        bind(id, to: statement, at: 1); bind(scope, to: statement, at: 2)
        bind(generation, to: statement, at: 3)
        bind(generation, to: statement, at: 4)
        let result = rows(statement).first
        return authorized(authority) ? result : nil
    }

    private func rows(_ statement: OpaquePointer) -> [CatalogHeinzel] {
        var result: [CatalogHeinzel] = []
        while sqlite3_step(statement) == SQLITE_ROW {
            guard let id = sqlite3_column_text(statement, 0), let name = sqlite3_column_text(statement, 1),
                  let state = sqlite3_column_text(statement, 2) else { continue }
            let activity = sqlite3_column_text(statement, 3).map { String(cString: $0) }
            let asks = sqlite3_column_type(statement, 4) == SQLITE_NULL ? nil : Int(sqlite3_column_int64(statement, 4))
            let approved = sqlite3_column_type(statement, 5) == SQLITE_NULL ? nil : sqlite3_column_double(statement, 5)
            result.append(CatalogHeinzel(id: String(cString: id), name: String(cString: name), state: String(cString: state),
                                         activity: activity, asks: asks, lastApproved: approved))
        }
        return result
    }
}
