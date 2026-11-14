import Darwin
import Foundation
import Network

/// A WebKit guest uses this proxy for HTTPS. TLS terminates at the destination.
final class BrowserNetwork: @unchecked Sendable {
    private let queue = DispatchQueue(label: "team.heinzel.browser-network")
    private let listener: NWListener
    private let password = UUID().uuidString + UUID().uuidString
    private let deniedHost: String?
    private var tunnels: [UUID: BrowserTunnel] = [:]
    private var closed = false

    init(deniedHost: String?) throws {
        self.deniedHost = deniedHost
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
    }

    func start() async throws -> ProxyConfiguration {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                guard !closed else {
                    continuation.resume(throwing: URLError(.cancelled))
                    return
                }
                listener.stateUpdateHandler = { [weak self] state in
                    guard let self else { return }
                    switch state {
                    case .ready:
                        listener.stateUpdateHandler = { [weak self] state in
                            if case .failed = state { self?.close() }
                        }
                        guard let port = listener.port else {
                            continuation.resume(throwing: URLError(.cannotConnectToHost))
                            return
                        }
                        var proxy = ProxyConfiguration(httpCONNECTProxy: .hostPort(host: "127.0.0.1", port: port))
                        proxy.applyCredential(username: "guest", password: password)
                        proxy.allowFailover = false
                        continuation.resume(returning: proxy)
                    case .failed(let error):
                        listener.stateUpdateHandler = nil
                        continuation.resume(throwing: error)
                    case .cancelled:
                        listener.stateUpdateHandler = nil
                        continuation.resume(throwing: URLError(.cancelled))
                    default: break
                    }
                }
                listener.newConnectionHandler = { [weak self] client in
                    guard let self, !closed, tunnels.count < 32 else { client.cancel(); return }
                    let id = UUID()
                    let token = Data("guest:\(password)".utf8).base64EncodedString()
                    let tunnel = BrowserTunnel(
                        client: client, queue: queue, token: token, deniedHost: deniedHost
                    ) { [weak self] in
                        self?.tunnels.removeValue(forKey: id)
                    }
                    tunnels[id] = tunnel
                    tunnel.start()
                }
                listener.start(queue: queue)
            }
        }
    }

    func close() {
        queue.async { [self] in
            closed = true
            listener.cancel()
            let pending = Array(tunnels.values)
            for tunnel in pending { tunnel.close() }
        }
    }
}

/// All mutable tunnel state is confined to the listener queue.
private final class BrowserTunnel: @unchecked Sendable {
    private let client: NWConnection
    private let queue: DispatchQueue
    private let token: String
    private let deniedHost: String?
    private let onClose: () -> Void
    private let timer: DispatchSourceTimer
    private var upstream: NWConnection?
    private var header = Data()
    private var closed = false

    init(
        client: NWConnection, queue: DispatchQueue, token: String, deniedHost: String?,
        onClose: @escaping () -> Void
    ) {
        self.client = client
        self.queue = queue
        self.token = token
        self.deniedHost = deniedHost
        self.onClose = onClose
        timer = DispatchSource.makeTimerSource(queue: queue)
    }

    func start() {
        timer.setEventHandler { [weak self] in self?.close() }
        timer.schedule(deadline: .now() + 5)
        timer.activate()
        client.stateUpdateHandler = { [weak self] state in
            if case .failed = state { self?.close() }
        }
        client.start(queue: queue)
        readRequest()
    }

    func close() {
        guard !closed else { return }
        closed = true
        timer.cancel()
        client.cancel()
        upstream?.cancel()
        onClose()
    }

    private func readRequest() {
        client.receive(minimumIncompleteLength: 1, maximumLength: 8192) { [weak self] data, _, complete, error in
            guard let self, !closed else { return }
            guard let data, error == nil, !complete else { close(); return }
            header.append(data)
            guard header.count <= 8192 else { close(); return }
            guard let boundary = header.range(of: Data("\r\n\r\n".utf8)) else { readRequest(); return }
            let pending = Data(header[boundary.upperBound...])
            guard let request = String(data: header[..<boundary.lowerBound], encoding: .ascii) else { close(); return }
            header.removeAll()
            connect(request: request, pending: pending)
        }
    }

    private func connect(request: String, pending: Data) {
        let lines = request.components(separatedBy: "\r\n")
        let start = lines[0].split(separator: " ", omittingEmptySubsequences: false)
        guard start.count == 3, start[0] == "CONNECT", ["HTTP/1.0", "HTTP/1.1"].contains(start[2]),
              let host = BrowserAddress.host(String(start[1])), host != deniedHost,
              lines.count <= 17 else { close(); return }

        var authorization: String?
        for line in lines.dropFirst() {
            guard let separator = line.firstIndex(of: ":"),
                  !line.hasPrefix(" "), !line.hasPrefix("\t") else { close(); return }
            switch line[..<separator].lowercased() {
            case "transfer-encoding", "content-length": close(); return
            case "proxy-authorization":
                guard authorization == nil else { close(); return }
                authorization = line[line.index(after: separator)...].trimmingCharacters(in: .whitespaces)
            default: break
            }
        }
        guard authorization == "Basic " + token else {
            reply("407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm=\"Heinzel\"\r\nContent-Length: 0\r\nConnection: close") { [weak self] in self?.close() }
            return
        }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let address = BrowserAddress.resolve(host)
            self?.queue.async { [weak self] in
                guard let self, !closed else { return }
                guard let address else { close(); return }
                openUpstream(address, pending: pending)
            }
        }
    }

    private func openUpstream(_ address: NWEndpoint.Host, pending: Data) {
        let connection = NWConnection(host: address, port: .https, using: .tcp)
        upstream = connection
        connection.stateUpdateHandler = { [weak self, weak connection] state in
            guard let self, let connection, !closed else { return }
            switch state {
            case .ready:
                reply("200 Connection Established") { [weak self] in
                    guard let self, !closed else { return }
                    if pending.isEmpty {
                        pipe(client, to: connection)
                    } else {
                        send(pending, to: connection) { [weak self] in
                            guard let self else { return }
                            pipe(client, to: connection)
                        }
                    }
                    pipe(connection, to: client)
                }
            case .failed, .cancelled: close()
            default: break
            }
        }
        connection.start(queue: queue)
    }

    private func reply(_ response: String, then: @escaping @Sendable () -> Void) {
        send(Data("HTTP/1.1 \(response)\r\n\r\n".utf8), to: client, then: then)
    }

    private func send(_ data: Data, to destination: NWConnection, then: @escaping @Sendable () -> Void) {
        destination.send(content: data, completion: .contentProcessed { [weak self] error in
            guard let self, !closed else { return }
            if error == nil { then() } else { close() }
        })
    }

    private func pipe(_ source: NWConnection, to destination: NWConnection) {
        timer.schedule(deadline: .now() + 60)
        source.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, complete, error in
            guard let self, !closed else { return }
            guard error == nil else { close(); return }
            if let data, !data.isEmpty {
                send(data, to: destination) { [weak self] in
                    if complete { self?.close() } else { self?.pipe(source, to: destination) }
                }
            } else if complete {
                close()
            } else {
                pipe(source, to: destination)
            }
        }
    }
}

enum BrowserAddress {
    static func host(_ authority: String) -> String? {
        guard authority.utf8.count <= 257,
              authority.wholeMatch(of: /(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?:443/) != nil else { return nil }
        let host = authority.dropLast(4).lowercased()
        let local = ["localhost", "local", "internal", "test", "invalid", "lan", "corp", "intranet", "ts.net", "arpa"]
        return local.contains(where: { host == $0 || host.hasSuffix("." + $0) }) ? nil : host
    }

    static func resolve(_ host: String) -> NWEndpoint.Host? {
        var hints = addrinfo()
        hints.ai_family = AF_UNSPEC
        hints.ai_socktype = SOCK_STREAM
        var head: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, nil, &hints, &head) == 0, let head else { return nil }
        defer { freeaddrinfo(head) }

        var interfaces: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&interfaces) == 0 else { return nil }
        defer { freeifaddrs(interfaces) }
        var local = Set<String>()
        var interface = interfaces
        while let current = interface {
            let row = current.pointee
            if let address = row.ifa_addr, let value = numeric(address, socklen_t(address.pointee.sa_len)) {
                local.insert(value.components(separatedBy: "%")[0])
            }
            interface = row.ifa_next
        }

        var addresses: [NWEndpoint.Host] = []
        var cursor: UnsafeMutablePointer<addrinfo>? = head
        while let current = cursor {
            let row = current.pointee
            guard addresses.count < 32, let address = row.ai_addr,
                  let value = numeric(address, row.ai_addrlen), !local.contains(value),
                  let endpoint = publicEndpoint(value) else { return nil }
            addresses.append(endpoint)
            cursor = row.ai_next
        }
        return addresses.first
    }

    private static func numeric(_ address: UnsafePointer<sockaddr>, _ length: socklen_t) -> String? {
        var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
        guard getnameinfo(address, length, &buffer, socklen_t(buffer.count), nil, 0, NI_NUMERICHOST) == 0 else { return nil }
        return String(decoding: buffer.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
    }

    static func publicEndpoint(_ value: String) -> NWEndpoint.Host? {
        if let address = IPv4Address(value) {
            let octets = Array(address.rawValue)
            let first = octets[0], second = octets[1], third = octets[2]
            let reserved = first == 0 || first == 10 || first == 127 || first >= 224
                || (first == 100 && (64...127).contains(second))
                || (first == 169 && second == 254) || (first == 172 && (16...31).contains(second))
                || (first == 192 && ((second == 0 && [0, 2].contains(third)) || (second == 88 && third == 99) || second == 168))
                || (first == 198 && ([18, 19].contains(second) || (second == 51 && third == 100)))
                || (first == 203 && second == 0 && third == 113)
            return reserved ? nil : .ipv4(address)
        }
        guard !value.contains("%"), let address = IPv6Address(value) else { return nil }
        let bytes = Array(address.rawValue)
        guard bytes[0] & 0xe0 == 0x20 else { return nil }
        let reserved = (bytes[0] == 0x20 && bytes[1] == 1
                        && (bytes[2] < 2 || (bytes[2] == 0x0d && bytes[3] == 0xb8)))
            || (bytes[0] == 0x20 && bytes[1] == 2)
            || (bytes[0] == 0x3f && bytes[1] == 0xff && bytes[2] < 16)
        return reserved ? nil : .ipv6(address)
    }
}
