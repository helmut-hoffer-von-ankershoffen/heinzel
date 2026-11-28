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

    func approvedSavedCover() -> URL? {
        guard let cover = journal?.album.cover,
              cover.sha256 == "bb465f44cecd01f1f84b3353704ca9fa7b2bdc995ff495a8879b1dcaf570d84c" else { return nil }
        return available(cover)
    }

    func approvedSavedPoster(for id: String?) -> URL? {
        guard let id, let track = journal?.album.tracks.first(where: { $0.id == id }),
              Self.approvedPosters[id] == track.poster.sha256 else { return nil }
        return available(track.poster)
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

    static let approvedFilmHash = "306c4b7f00c9f89aa80b624f7dcaeace22c52a14ece3b3f7a1e9b107d6c0f126"
    // Owner-approved artwork identity, independent of the mutable catalogue.
    private static let approvedPosters: [String: String] = [
        "berlin-morning": "763a964cc4c38c240d3bfabce097cd44ebaa695484e7f41a682b06bbfb453003",
        "berlin-morning-to-lunch": "81318ff897b886d388923de0d9ebfc9c0d4d4c762356d4ff1a3f0512abb507a9",
        "berlin-lunch": "7ebd70158e2cbe0683aca0c50fe7abc4e48db4db2fcf0a98a1535da3723215a8",
        "berlin-lunch-to-afternoon": "c48ba40358a0da807e901035a07635240c6a5071d8b48dca7b4799ac11ffd73e",
        "berlin-afternoon": "dee805ba6ca7b94c5bfb95d3256f110dae6dcda618979d4ee4bb62444f3a90a8",
        "berlin-afternoon-to-evening": "48931a8b4600171dc40645f95e85a2af3412ed17808f4fa11ce778beb2a6b563",
        "berlin-evening": "87d8cf437c37e1f55d35022031df2e2519c78dc96fc46bd5f7ed267ac38bf1af",
        "berlin-evening-to-night": "04fb7562010cfb021b0b9214bd92b75f31397cac4011fd58ff7eb8df1225407f",
        "berlin-night": "530686fad27106b58fb3b41ea9b0cdb85650c0cdbdcb9c71ca152834bb6327ab",
        "paris-morning": "103f9481b1173a0337a630e9909a40a59aae22b5e4b6ad6f4bc532e306b5b35a",
        "paris-morning-to-lunch": "a6c004830a9f5bdee497aeb9a37ffd96bb6e134c14635282226da85540c39e7d",
        "paris-lunch": "94427927713e2171b2636f1779852a584f473a403ee040989ab73320087471a9",
        "paris-lunch-to-afternoon": "c9a3648deb83bf069d5ced888f30213b187aed6ad6e8b0ca9fc13aa669a90a4c",
        "paris-afternoon": "35dbe174890f0945fbb3386ca6c109b2ab02f1b6dbc2cf20f344cc2c95242350",
        "paris-afternoon-to-evening": "87e829792c340d4bb82882126b255028994164fa6ae3ee7034d80d950e1ccb0c",
        "paris-evening": "c1f093f198bbf7c831e966ba032e56be255d3bc08e76b786188ff0d66084a409",
        "paris-evening-to-night": "853d6aaabe54502278ea28e1298b3efca55f2208d481459740cdbdf314e3e5cc",
        "paris-night": "7838f64760efa9fd6938693869522f2f979e521bd8da3a21f9ba866c108c654e",
        "san-francisco-morning": "97d21864bbc52ab9413a904fe06914fdacc2019eb9799fa8b3339a0004ae89b0",
        "san-francisco-morning-to-lunch": "592fe6991fb07603f20ba005cfe7970cd683c9ec142ce991d799dfd9d9f5e7b8",
        "san-francisco-lunch": "ca35ddbfcefc75110c29f89b14d927dbe2f754219bbfabb7d7b9c6079cb2f4e4",
        "san-francisco-lunch-to-afternoon": "3012738645c1f4674aaac1b4b30178f9175980331b926eb46c14448f302b578f",
        "san-francisco-afternoon": "9176233de641eaa8e69f77aedf615618106102fff7dcee6e9e850d2f4655c644",
        "san-francisco-afternoon-to-evening": "4c0eb70cce17c5d3d691470ab4cd70f3a46ffcc1782ca653906e11a44bc4462f",
        "san-francisco-evening": "20afdea76065f9b2d0d8bc85f2944c3dfaf13c65bd1119104320c3d56fd3d330",
        "san-francisco-evening-to-night": "d9c7b5964d98a48212ce5213dae05beb99ee67317eef45c1cecf259dc015ef43",
        "san-francisco-night": "daf79e91a4f6e6b653f61433305273eb2d24f7b85a1fa29f1642522e592694b9",
        "shenzhen-morning": "f1fc601ebbbcd37dd704ad596b91dd5670f957f39859be7ec7f5bee84ee9430e",
        "shenzhen-morning-to-lunch": "02caeb3c6d092024245a7b1a8d9b95690ff43d33712dc630fc5c7f7f65ad4f62",
        "shenzhen-lunch": "b9ede73c18cbd8eb821445db03dae61ffde555efdbe70aa7ba059b8510e9fb06",
        "shenzhen-lunch-to-afternoon": "4e6afceb36a156d8c56c27907650a3e4f4e1ef6919211406007ba59d473d583c",
        "shenzhen-afternoon": "fc7c6e7ae84295099d69a2a9b4a7c236732352d3bffd732444fcd65419842ccb",
        "shenzhen-afternoon-to-evening": "4169c51b1d5dec55a06dd65d2e212c1c57529671470f8629905a71cd07a31f25",
        "shenzhen-evening": "641e851af8d35b10357c8e8a8ff29a461c71ed4c380fc711c0ae36e2fb27b3f3",
        "shenzhen-evening-to-night": "f25af4e995206af1bd6c37f286a2e8ee6fef83ed83de81f187d1f0f7407745dd",
        "shenzhen-night": "caacf566f916d60e8a8947bdb5b955fa1f3da71e8b4e3f9d9d1dbcd3c7a34654",
        "berlin-evening-extended": "57f977d38e0a9aefa81ad0a5559ac9559a6dadee62c0363045d3716203a08802",
    ]

    func approvedFilm(for id: String?) -> Bool {
        guard id == "berlin-evening-extended",
              let track = journal?.album.tracks.first(where: { $0.id == id }) else { return false }
        return track.media.sha256 == Self.approvedFilmHash
    }

    static var approvedFilmWebURL: URL {
        URL(string: "heinzel-album://film/\(approvedFilmHash).mp4")!
    }

    static var approvedFilmStreamURL: URL {
        URL(string: "https://latentheinzels.heinzel.team/media/sha256/\(approvedFilmHash).mp4")!
    }

    // Never resolve a path supplied by a web document.
    func availableApprovedFilm() -> URL? {
        guard approvedFilm(for: "berlin-evening-extended"),
              let track = journal?.album.tracks.first(where: { $0.id == "berlin-evening-extended" }),
              track.media.sha256 == Self.approvedFilmHash else { return nil }
        return available(track.media)
    }

    func mediaSnapshot(selectedID: String?) -> [String: Any] {
        let album = journal?.album
        let selected = album?.tracks.first { $0.id == selectedID }
        let film = approvedFilm(for: selectedID)
        // Expose only catalogue-derived HTTPS URLs, never native filesystem paths.
        func publicURL(_ object: AlbumObject?) -> Any {
            guard let object else { return NSNull() }
            let url = remote(object)
            guard url.scheme == "https", url.host == "latentheinzels.heinzel.team" else { return NSNull() }
            return url.absoluteString
        }
        let still = selected.flatMap { Self.approvedPosters[$0.id] == $0.poster.sha256 ? $0.poster : nil }
        let cover = album?.cover.sha256 == "bb465f44cecd01f1f84b3353704ca9fa7b2bdc995ff495a8879b1dcaf570d84c" ? album?.cover : nil
        return ["albumID": album?.id as Any? ?? NSNull(),
                "selectedTrackID": selected?.id as Any? ?? NSNull(),
                "tracks": album?.tracks.map { ["id": $0.id, "title": $0.title] } ?? [],
                "standardMediaID": selected?.media.sha256 as Any? ?? NSNull(),
                "extendedMediaID": film ? Self.approvedFilmHash as Any : NSNull(),
                "cover": publicURL(cover), "still": publicURL(still),
                "extendedVideo": film ? ["url": (availableApprovedFilm() != nil ? Self.approvedFilmWebURL : Self.approvedFilmStreamURL).absoluteString,
                    "sha256": Self.approvedFilmHash, "trackID": "berlin-evening-extended",
                    "admission": availableApprovedFilm() != nil ? "local" : "remote"] as Any : NSNull(),
                "visualApproval": film ? "approved" : (still != nil ? "still" : "unavailable")]
    }
    static func byteRange(_ value: String?, count: Int) -> Range<Int>? {
        guard count > 0 else { return nil }
        guard let value else { return 0..<count }
        guard value.hasPrefix("bytes=") else { return nil }
        let parts = value.dropFirst(6).split(separator: "-", omittingEmptySubsequences: false)
        guard parts.count == 2 else { return nil }
        func number(_ text: Substring) -> Int? {
            guard !text.isEmpty, text.allSatisfy({ $0.isASCII && $0.isNumber }) else { return nil }
            return Int(text)
        }
        if parts[0].isEmpty {
            guard let suffix = number(parts[1]), suffix > 0 else { return nil }
            return max(0, count - suffix)..<count
        }
        guard let start = number(parts[0]), start < count else { return nil }
        if parts[1].isEmpty { return start..<count }
        guard let end = number(parts[1]), end >= start else { return nil }
        return start..<(min(end, count - 1) + 1)
    }


}
