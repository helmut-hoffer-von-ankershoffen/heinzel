// Native MCP observer launcher. The SDK has already admitted the protected package under its update lock.
// The launcher validates one request and replaces its own process with the provisioned driver under Seatbelt.
// The SDK's original process handle and deadline therefore cover the driver too.
//
// Provisioned release build: compile with swiftc -O -D HZ_MCP_OBSERVER_PROVISIONED and a reviewed
// Provisioning.swift defining observerProvisioning. Its paths and controlDigest must come from the same
// ProtectedObserverInstall inventory as the SDK. Never include HZ_MCP_OBSERVER_FIXTURE in a release.
// Build each supported macOS architecture and combine slices with lipo, as for the notifier. Sign the
// launcher and driver with the approved release identity using codesign --options runtime. Verify each
// with codesign --verify --strict and require hardened runtime without debugging or loader exceptions.
// Hash the signed artifacts before publishing pins. An administrator provisions those exact files
// and the stable update lock under protected ancestry.
// The reviewed driver must prove current managed policy and emit the native observation protocol.
// A readable policy source is a prerequisite, not evidence of a successful managed-policy read.

import CoreFoundation
import Darwin
import Foundation

struct ObserverProvisioning {
    let client: String
    let controlDigest: String
    let driver: String
    let managedPolicy: String
    let dependencies: [String]
}

private let requestLimit = 4096
private let requestDeadline: UInt64 = 5_000_000_000

private enum Refusal: Error {
    case unavailable
}

private func readRequest() throws -> Data {
    let deadline = DispatchTime.now().uptimeNanoseconds + requestDeadline
    var input = Data()
    var chunk = [UInt8](repeating: 0, count: requestLimit + 1)
    while true {
        let now = DispatchTime.now().uptimeNanoseconds
        guard now < deadline else { throw Refusal.unavailable }
        let milliseconds = Int32((deadline - now + 999_999) / 1_000_000)
        var descriptor = pollfd(fd: STDIN_FILENO, events: Int16(POLLIN), revents: 0)
        let ready = poll(&descriptor, 1, milliseconds)
        if ready < 0 && errno == EINTR { continue }
        guard ready > 0 else { throw Refusal.unavailable }
        let capacity = min(chunk.count, requestLimit + 1 - input.count)
        let count = chunk.withUnsafeMutableBytes { Darwin.read(STDIN_FILENO, $0.baseAddress, capacity) }
        if count < 0 && errno == EINTR { continue }
        guard count >= 0 else { throw Refusal.unavailable }
        if count == 0 { break }
        input.append(contentsOf: chunk.prefix(count))
        guard input.count <= requestLimit else { throw Refusal.unavailable }
    }
    guard input.last == 10, input.dropLast().allSatisfy({ $0 != 10 }) else { throw Refusal.unavailable }
    return input
}

private func hex(_ value: Any?, count: Int) -> Bool {
    guard let text = value as? String, text.utf8.count == count else { return false }
    return text.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
}

private func cleanAbsolute(_ path: String) -> Bool {
    path.hasPrefix("/") && !path.utf8.contains(where: { $0 < 32 || $0 == 127 })
        && !path.split(separator: "/").contains("..")
}

private func physical(_ path: String) -> String? {
    guard cleanAbsolute(path), let resolved = realpath(path, nil) else { return nil }
    defer { free(resolved) }
    return String(cString: resolved)
}

private func accountHome() -> String? {
    #if HZ_MCP_OBSERVER_FIXTURE
    return physical(observerFixtureHome)
    #else
    guard let account = getpwuid(getuid()) else { return nil }
    return physical(String(cString: account.pointee.pw_dir))
    #endif
}

private func validate(_ input: Data, provision: ObserverProvisioning) throws -> (String, String) {
    let payload = Data(input.dropLast())
    guard let request = try JSONSerialization.jsonObject(with: payload) as? [String: Any],
        Set(request.keys) == ["challenge", "client", "control", "home", "installation", "operation", "server",
                             "version", "workspace"],
        let version = request["version"] as? NSNumber, CFGetTypeID(version) != CFBooleanGetTypeID(),
        version == 1,
        request["operation"] as? String == "inspect",
        request["client"] as? String == provision.client,
        request["control"] as? String == provision.controlDigest,
        hex(request["control"], count: 64), hex(request["challenge"], count: 64),
        hex(request["installation"], count: 64),
        let server = request["server"] as? String, server.hasPrefix("heinzel-"),
        hex(String(server.dropFirst(8)), count: 32),
        let home = request["home"] as? String, let workspace = request["workspace"] as? String,
        accountHome() == home, physical(workspace) == workspace
    else { throw Refusal.unavailable }
    // Foundation's parser otherwise accepts duplicate object keys. The SDK sends canonical JSON.
    let canonical = try JSONSerialization.data(withJSONObject: request, options: [.sortedKeys, .withoutEscapingSlashes])
    guard canonical == payload else { throw Refusal.unavailable }
    return (home, workspace)
}

private func sandboxArguments(provision: ObserverProvisioning, config: String, workspace: String) -> [String] {
    let executablePaths = [provision.driver] + provision.dependencies
    let readPaths = executablePaths + [provision.managedPolicy, config]
    func literals(_ indices: Range<Int>) -> String {
        indices.map { "(literal (param \"read\($0)\"))" }.joined(separator: " ")
    }
    let profile = """
        (version 1)
        (deny default)
        (allow sysctl-read)
        (allow file-read-metadata)
        (allow process-exec (literal (param "read0")))
        (allow file-read-data
          (require-all (vnode-type DIRECTORY)
            (require-any (literal (param "workspace")) (path-ancestors (param "workspace")))))
        (allow file-read-data (literal "/") (subpath "/System/Library") (subpath "/usr/lib") \(literals(readPaths.indices)))
        (allow file-map-executable (subpath "/System/Library") (subpath "/usr/lib") \(literals(executablePaths.indices)))
        """
    let parameters = readPaths.enumerated().flatMap { ["-D", "read\($0.offset)=\($0.element)"] }
    return ["/usr/bin/sandbox-exec", "-p", profile, "-D", "workspace=\(workspace)"] + parameters
        + [provision.driver, "observe-mcp-config"]
}

private func replaceInput(_ input: Data) throws {
    var descriptors = [Int32](repeating: -1, count: 2)
    guard pipe(&descriptors) == 0 else { throw Refusal.unavailable }
    defer { close(descriptors[0]); close(descriptors[1]) }
    guard fcntl(descriptors[1], F_SETFL, O_NONBLOCK) == 0 else { throw Refusal.unavailable }
    let written = input.withUnsafeBytes { Darwin.write(descriptors[1], $0.baseAddress, $0.count) }
    guard written == input.count, dup2(descriptors[0], STDIN_FILENO) == STDIN_FILENO else {
        throw Refusal.unavailable
    }
}

private func requirePolicySource(_ path: String) throws {
    let descriptor = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
    guard descriptor >= 0 else { throw Refusal.unavailable }
    defer { close(descriptor) }
    var info = stat()
    var byte: UInt8 = 0
    guard fstat(descriptor, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
        info.st_size > 0, Darwin.read(descriptor, &byte, 1) == 1
    else { throw Refusal.unavailable }
}

private func run(_ provision: ObserverProvisioning) throws {
    guard getuid() != 0, getuid() == geteuid(), getgid() == getegid(),
        CommandLine.arguments.dropFirst() == ["observe-mcp-config"],
        ["claude", "codex"].contains(provision.client),
        ([provision.driver, provision.managedPolicy] + provision.dependencies).allSatisfy({ physical($0) == $0 })
    else { throw Refusal.unavailable }
    let request = try readRequest()
    let (home, workspace) = try validate(request, provision: provision)
    try requirePolicySource(provision.managedPolicy)
    let config = home + (provision.client == "claude" ? "/.claude.json" : "/.codex/config.toml")
    let directory = open(workspace, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
    guard directory >= 0 else { throw Refusal.unavailable }
    let changed = fchdir(directory)
    close(directory)
    guard changed == 0, let cwd = getcwd(nil, 0) else { throw Refusal.unavailable }
    defer { free(cwd) }
    guard String(cString: cwd) == workspace else { throw Refusal.unavailable }
    try replaceInput(request)
    let arguments = sandboxArguments(provision: provision, config: config, workspace: workspace)
    var argv = arguments.map { strdup($0) } + [nil]
    let environmentStrings: [String] = ["PATH=/usr/bin:/bin", "LC_ALL=C"]
    var environment = environmentStrings.map { strdup($0) } + [nil]
    guard argv.dropLast().allSatisfy({ $0 != nil }), environment.dropLast().allSatisfy({ $0 != nil }) else {
        throw Refusal.unavailable
    }
    execve("/usr/bin/sandbox-exec", &argv, &environment)
    throw Refusal.unavailable
}

#if HZ_MCP_OBSERVER_PROVISIONED
do {
    try run(observerProvisioning)
} catch {
    exit(1)
}
#else
// The regular source build has no reviewed vendor/policy driver and cannot observe a profile.
exit(1)
#endif
