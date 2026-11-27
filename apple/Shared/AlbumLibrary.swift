import CryptoKit
import Foundation
import Darwin

struct AlbumPlayableTrack {
    let id: String
    let title: String
    let url: URL
}

private enum AlbumFailure: LocalizedError {
    case catalog, media, storage
    var errorDescription: String? {
        switch self {
        case .catalog: "The album catalog is unavailable or invalid."
        case .media: "The download did not match the album. Resume to try again."
        case .storage: "The album could not be saved. Free device storage, then Resume to try again."
        }
    }
}

private struct AlbumObject: Codable, Hashable, Sendable {
    let key: String
    let bytes: Int
    let sha256: String
    let mimeType: String?

    var filename: String { (key as NSString).lastPathComponent }

    func validate(video: Bool) throws {
        let ext = (filename as NSString).pathExtension
        guard sha256.utf8.count == 64, sha256.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              key == "/media/sha256/\(sha256).\(ext)", (1...1_500_000_000).contains(bytes),
              video ? (ext == "mp4" && mimeType == "video/mp4") : ["webp", "jpg", "png"].contains(ext)
        else { throw AlbumFailure.catalog }
    }
}

private struct SoundAlbum: Codable, Sendable {
    struct Track: Codable, Sendable {
        let id: String
        let title: String
        let position: Int
        let media: AlbumObject
        let poster: AlbumObject
    }

    let id: String
    let cover: AlbumObject
    let tracks: [Track]
    var objects: [AlbumObject] {
        var seen = Set<String>()
        return (tracks.flatMap { [$0.media, $0.poster] } + [cover]).filter { seen.insert($0.key).inserted }
    }

    func validate() throws {
        guard !id.isEmpty, id == "dreaming-of-electric-snow", (1...40).contains(tracks.count), Set(tracks.map(\.id)).count == tracks.count
        else { throw AlbumFailure.catalog }
        try cover.validate(video: false)
        var sizes = [String: Int]()
        for (index, track) in tracks.enumerated() {
            guard !track.id.isEmpty, !track.title.isEmpty, track.position == index + 1 else { throw AlbumFailure.catalog }
            try track.media.validate(video: true)
            try track.poster.validate(video: false)
            for item in [track.media, track.poster, cover] {
                if let size = sizes[item.key], size != item.bytes { throw AlbumFailure.catalog }
                sizes[item.key] = item.bytes
            }
        }
        guard objects.count <= 81, objects.reduce(0, { $0 + $1.bytes }) <= 2_000_000_000 else {
            throw AlbumFailure.catalog
        }
    }
}

private final class AlbumTransfer: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

// Receipts live in app-private storage. Copying an object (even with preserved mtime)
// changes its inode or POSIX change time and requires a fresh digest.
private struct AlbumIdentity: Codable, Equatable, Sendable {
    let device: Int32
    let inode: UInt64
    let size: Int64
    let changed: Int64
    let changedNanos: Int64
    let modified: Int64
    let modifiedNanos: Int64

    static func read(_ url: URL) throws -> Self {
        var info = stat()
        guard lstat(url.path, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG else { throw AlbumFailure.media }
        return Self(device: info.st_dev, inode: info.st_ino, size: info.st_size,
                    changed: Int64(info.st_ctimespec.tv_sec), changedNanos: Int64(info.st_ctimespec.tv_nsec),
                    modified: Int64(info.st_mtimespec.tv_sec), modifiedNanos: Int64(info.st_mtimespec.tv_nsec))
    }
}

private struct AlbumReceipt: Codable, Sendable {
    let object: AlbumObject
    let identity: AlbumIdentity
}

@MainActor
final class AlbumLibrary {
    static let changed = Notification.Name("HeinzelAlbumChanged")
    static let progress = Notification.Name("HeinzelAlbumProgress")
    static let shared: AlbumLibrary = {
        let root = URL.applicationSupportDirectory.appending(path: "Heinzel/SoundAlbums", directoryHint: .isDirectory)
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil
        config.urlCredentialStorage = nil
        config.urlCache = nil
        return AlbumLibrary(root: root, catalogURL: URL(string: "https://latentheinzels.heinzel.team/media/catalog.json")!,
                            session: URLSession(configuration: config))
    }()

    private struct Journal: Codable {
        var album: SoundAlbum
        var phase: Phase
    }
    private enum Phase: String, Codable { case idle, downloading, paused, cancelled, ready, failed }
    private let root: URL
    private let catalogURL: URL
    private let session: URLSession
    private var journal: Journal?
    private var phase: Phase = .idle
    private var errorText: String?
    private var transfer: Task<Void, Never>?
    private var loading: Task<SoundAlbum, Error>?
    private var generation = 0
    private var activeObject: String?
    private var activeBytes = 0
    private var verified = [String: AlbumReceipt]()
    private var rejected = [String: AlbumIdentity]()
    private var checking = [String: Task<AlbumReceipt?, Never>]()
    private var intent: Phase = .idle
    private var verificationTail: Task<AlbumReceipt?, Never>?

    init(root: URL, catalogURL: URL, session: URLSession) {
        self.root = root
        self.catalogURL = catalogURL
        self.session = session
        let intentFile = root.appending(path: "intent.json")
        if FileManager.default.fileExists(atPath: intentFile.path) {
            do {
                intent = try JSONDecoder().decode(Phase.self, from: Data(contentsOf: intentFile))
                phase = intent == .downloading ? .paused : intent
            } catch {
                intent = .paused
                phase = .failed
                errorText = AlbumFailure.storage.localizedDescription
            }
        }
        let file = root.appending(path: "album.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return }
        do {
            let previous = try JSONDecoder().decode(Journal.self, from: Data(contentsOf: file))
            try previous.album.validate()
            journal = previous
            if intent == .idle || intent == .downloading {
                phase = previous.phase == .downloading ? .paused : previous.phase
            }
        } catch { phase = .failed; errorText = AlbumFailure.storage.localizedDescription }
    }

    private func local(_ object: AlbumObject) -> URL { root.appending(path: object.filename) }

    private func receiptURL(_ object: AlbumObject) -> URL { root.appending(path: object.filename + ".receipt.json") }

    private func available(_ object: AlbumObject) -> URL? {
        let url = local(object)
        guard let identity = try? AlbumIdentity.read(url), identity.size == object.bytes else { return nil }
        if verified[object.key] == nil,
           let data = try? Data(contentsOf: receiptURL(object)),
           let receipt = try? JSONDecoder().decode(AlbumReceipt.self, from: data),
           receipt.object == object, receipt.identity == identity {
            verified[object.key] = receipt
        }
        if let receipt = verified[object.key], receipt.object == object, receipt.identity == identity { return url }
        if checking[object.key] == nil, rejected[object.key] != identity {
            let task = verification(object)
            Task {
                let receipt = await task.value
                checking[object.key] = nil
                verified[object.key] = receipt
                if receipt == nil { rejected[object.key] = identity }
                notify()
            }
        }
        return nil
    }

    private func verification(_ object: AlbumObject) -> Task<AlbumReceipt?, Never> {
        if let task = checking[object.key] { return task }
        let url = local(object), receiptFile = receiptURL(object)
        let predecessor = verificationTail
        let task = Task.detached(priority: .utility) { () -> AlbumReceipt? in
            _ = await predecessor?.value
            do {
                let before = try AlbumIdentity.read(url)
                guard before.size == object.bytes, try Self.matches(url, object),
                      try AlbumIdentity.read(url) == before else { return nil }
                let receipt = AlbumReceipt(object: object, identity: before)
                try JSONEncoder().encode(receipt).write(to: receiptFile, options: .atomic)
                return receipt
            } catch { return nil }
        }
        checking[object.key] = task
        verificationTail = task
        return task
    }

    private nonisolated static func matches(_ url: URL, _ object: AlbumObject) throws -> Bool {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var digest = SHA256()
        var size = 0
        while let chunk = try handle.read(upToCount: 512 * 1024), !chunk.isEmpty {
            size += chunk.count
            guard size <= object.bytes else { return false }
            digest.update(data: chunk)
        }
        return size == object.bytes && digest.finalize().map { String(format: "%02x", $0) }.joined() == object.sha256
    }

    func savedPoster(for id: String?) -> URL? {
        guard let track = journal?.album.tracks.first(where: { $0.id == id }) else { return nil }
        return available(track.poster)
    }

    func savedTracks() throws -> [AlbumPlayableTrack] {
        journal?.album.tracks.compactMap { track in
            available(track.media).map { AlbumPlayableTrack(id: track.id, title: track.title, url: $0) }
        } ?? []
    }

    private func remote(_ object: AlbumObject) -> URL {
        URL(string: object.key, relativeTo: catalogURL)!.absoluteURL
    }

    private func load() async throws -> SoundAlbum {
        struct Catalog: Decodable { let schemaVersion: Int; let albums: [SoundAlbum] }
        let limit = 2 * 1024 * 1024
        let (stream, response) = try await session.bytes(from: catalogURL, delegate: AlbumTransfer())
        guard (response as? HTTPURLResponse)?.statusCode == 200, response.url == catalogURL,
              response.expectedContentLength <= limit else { throw AlbumFailure.catalog }
        var data = Data()
        for try await byte in stream {
            guard data.count < limit else { throw AlbumFailure.catalog }
            data.append(byte)
        }
        let catalog = try JSONDecoder().decode(Catalog.self, from: data)
        guard catalog.schemaVersion == 1, let album = catalog.albums.first(where: { $0.id == "dreaming-of-electric-snow" }),
              Set(catalog.albums.map(\.id)).count == catalog.albums.count else { throw AlbumFailure.catalog }
        try album.validate()
        return album
    }

    func catalogTracks() async throws -> [AlbumPlayableTrack] {
        if journal == nil {
            if loading == nil { loading = Task { try await self.load() } }
            guard let request = loading else { throw AlbumFailure.catalog }
            do {
                let album = try await request.value
                loading = nil
                if journal == nil {
                    let initial = (intent == .paused || intent == .cancelled) ? intent : Phase.idle
                    let record = Journal(album: album, phase: initial)
                    try writeJournal(record)
                    journal = record
                    phase = initial
                }
            } catch {
                loading = nil
                if journal == nil { phase = .failed; errorText = (error as? AlbumFailure)?.localizedDescription ?? AlbumFailure.catalog.localizedDescription; notify() }
                throw error
            }
        }
        guard let album = journal?.album else { throw AlbumFailure.catalog }
        let tracks = album.tracks.map { track in
            AlbumPlayableTrack(id: track.id, title: track.title, url: available(track.media) ?? remote(track.media))
        }
        return tracks
    }

    func playableTracks() async throws -> [AlbumPlayableTrack] {
        let tracks = try await catalogTracks()
        if phase == .idle || (phase == .ready && journal?.album.objects.contains { available($0) == nil } == true) {
            try resumeDownloads()
        }
        return tracks
    }

    private func notify() { NotificationCenter.default.post(name: Self.changed, object: nil) }

    private func clearProgress() {
        activeObject = nil
        activeBytes = 0
        NotificationCenter.default.post(name: Self.progress, object: nil)
    }

    private func writeJournal(_ record: Journal) throws {
        do {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
            try JSONEncoder().encode(record).write(to: root.appending(path: "album.json"), options: .atomic)
        } catch {
            phase = .failed
            clearProgress()
            errorText = AlbumFailure.storage.localizedDescription
            notify()
            throw AlbumFailure.storage
        }
    }

    private func persist(_ next: Phase) throws {
        if var record = journal {
            record.phase = next
            try writeJournal(record)
            journal = record
        }
        phase = next
        errorText = nil
        notify()
    }

    private func stop(_ next: Phase) throws {
        generation += 1
        transfer?.cancel()
        // The catalog is shared with playback; transfer intent must not cancel it.
        clearProgress()
        try writeIntent(next)
        try persist(next)
    }

    private func writeIntent(_ next: Phase) throws {
        // The latest command also wins in memory if persisting it fails.
        intent = next
        do {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
            try JSONEncoder().encode(next).write(to: root.appending(path: "intent.json"), options: .atomic)
        } catch { throw AlbumFailure.storage }
    }

    func pauseDownloads() throws { try stop(.paused) }
    func cancelDownloads() throws { try stop(.cancelled) }

    func resumeDownloads() throws {
        try writeIntent(.downloading)
        guard let album = journal?.album else {
            // Also fetch a catalog when Resume is the first action after relaunch.
            Task { _ = try? await playableTracks() }
            return
        }
        try persist(.downloading)
        // A cancelled worker still owns its partial until its awaited cleanup ends.
        // Its defer will restart only if this remains the latest intent.
        guard transfer == nil else { return }
        let ticket = generation
        transfer = Task {
            defer {
                clearProgress()
                transfer = nil
                if ticket != generation && intent == .downloading {
                    do { try resumeDownloads() }
                    catch {
                        phase = .failed
                        errorText = AlbumFailure.storage.localizedDescription
                    }
                }
                notify()
            }
            do {
                for object in album.objects {
                    try Task.checkCancellation()
                    if available(object) != nil { continue }
                    let repair = verification(object)
                    if let receipt = await repair.value {
                        checking[object.key] = nil
                        verified[object.key] = receipt
                        continue
                    }
                    checking[object.key] = nil
                    try Task.checkCancellation()
                    guard ticket == generation else { return }
                    let remaining = album.objects.filter { available($0) == nil }.reduce(0) { $0 + $1.bytes }
                    let destination = local(object), remoteURL = remote(object), root = self.root, session = self.session
                    activeObject = object.key
                    activeBytes = 0
                    let worker = Task.detached(priority: .utility) {
                        try await Self.download(object, from: remoteURL, to: destination, root: root,
                                                session: session, remaining: remaining) { bytes in
                            await self.reportProgress(bytes, object: object.key, ticket: ticket)
                        }
                    }
                    let receipt = try await withTaskCancellationHandler {
                        try await worker.value
                    } onCancel: { worker.cancel() }
                    // Completed verified objects survive cancellation and are reusable after restart.
                    verified[object.key] = receipt
                    rejected[object.key] = nil
                    clearProgress()
                    try Task.checkCancellation()
                    guard generation == ticket else { return }
                    notify()
                }
                try Task.checkCancellation()
                guard ticket == generation else { return }
                try persist(.ready)
            } catch {
                clearProgress()
                guard ticket == generation else { return }
                try? persist(.failed)
                errorText = (error as? AlbumFailure)?.localizedDescription ?? "Download interrupted. Resume to try again."
                notify()
            }
        }
    }

    private func reportProgress(_ bytes: Int, object: String, ticket: Int) {
        guard ticket == generation, phase == .downloading, activeObject == object else { return }
        activeBytes = bytes
        NotificationCenter.default.post(name: Self.progress, object: nil)
    }

    private nonisolated static func requireSpace(_ root: URL, bytes: Int) throws {
        let attributes = try FileManager.default.attributesOfFileSystem(forPath: root.path)
        guard let free = attributes[.systemFreeSize] as? NSNumber,
              free.int64Value >= Int64(bytes) + 256_000_000 else { throw AlbumFailure.storage }
    }

    private nonisolated static func download(_ object: AlbumObject, from remote: URL, to destination: URL,
                                             root: URL, session: URLSession, remaining: Int,
                                             progress: @Sendable (Int) async -> Void) async throws -> AlbumReceipt {
        let temporary = root.appending(path: object.filename + ".partial")
        try? FileManager.default.removeItem(at: temporary)
        try requireSpace(root, bytes: remaining)
        // Streaming avoids URLSession's potentially unbounded download temporary file.
        let (stream, response) = try await session.bytes(from: remote, delegate: AlbumTransfer())
        guard (response as? HTTPURLResponse)?.statusCode == 200, response.url == remote,
              response.expectedContentLength <= object.bytes else { throw AlbumFailure.media }
        guard FileManager.default.createFile(atPath: temporary.path, contents: nil) else { throw AlbumFailure.storage }
        defer { try? FileManager.default.removeItem(at: temporary) }
        let handle = try FileHandle(forWritingTo: temporary)
        defer { try? handle.close() }
        var count = 0
        var buffer = Data()
        var digest = SHA256()
        let clock = ContinuousClock()
        var lastProgress = clock.now
        for try await byte in stream {
            try Task.checkCancellation()
            guard count < object.bytes else { throw AlbumFailure.media }
            count += 1
            buffer.append(byte)
            if buffer.count == 64 * 1024 {
                try requireSpace(root, bytes: buffer.count)
                try handle.write(contentsOf: buffer)
                digest.update(data: buffer)
                buffer.removeAll(keepingCapacity: true)
                if lastProgress.duration(to: clock.now) >= .milliseconds(200) {
                    await progress(count)
                    lastProgress = clock.now
                }
            }
        }
        try requireSpace(root, bytes: buffer.count)
        try handle.write(contentsOf: buffer)
        digest.update(data: buffer)
        await progress(count)
        guard count == object.bytes,
              digest.finalize().map({ String(format: "%02x", $0) }).joined() == object.sha256 else { throw AlbumFailure.media }
        try handle.synchronize()
        try handle.close()
        // POSIX rename publishes a complete object atomically, preserving existing verified objects on failure.
        guard rename(temporary.path, destination.path) == 0 else { throw AlbumFailure.storage }
        let receipt = AlbumReceipt(object: object, identity: try AlbumIdentity.read(destination))
        try JSONEncoder().encode(receipt).write(to: destination.appendingPathExtension("receipt.json"), options: .atomic)
        return receipt
    }

    func snapshot() -> [String: Any] {
        let objects = journal?.album.objects ?? []
        let saved = objects.filter { available($0) != nil }
        if phase == .ready && saved.count != objects.count && checking.isEmpty {
            phase = .failed
            clearProgress()
            errorText = "Some saved files are missing or damaged. Resume to download them again."
        }
        let verifiedBytes = saved.reduce(0) { $0 + $1.bytes }
        let totalBytes = objects.reduce(0) { $0 + $1.bytes }
        let partialBytes = phase == .downloading && !saved.contains(where: { $0.key == activeObject }) ? activeBytes : 0
        return ["phase": phase.rawValue, "verified": saved.count, "total": objects.count,
                "verifiedBytes": verifiedBytes, "receivedBytes": min(totalBytes, verifiedBytes + partialBytes), "totalBytes": totalBytes,
                "error": errorText as Any? ?? NSNull()]
    }
}
