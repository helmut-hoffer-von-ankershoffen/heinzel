// Heinzel Notifier: hz's own macOS notifier (ADR 0043), built by `make notifier` and shipped in the wheel.
//
// hz starts it through LaunchServices (`open -n`), which puts it in the person's GUI session, also from the daemon.
//   --status                                   print the notification authorization: authorized, denied, ...
//   --message M [--title T] [--subtitle S] [--open URL] [--hat COLOUR] [--sound] [--id ID] [--thread T]
//                                              ask for authorization once, post one notification, print the result
//   (no arguments)                             launched by a click on a notification: open its URL, then quit
// Everything arrives as arguments, never as code; text is bounded; a URL opens only when it is hz's web app on this
// machine (http://127.0.0.1:<port>/...).
import AppKit
import Darwin
import UserNotifications

let messageLifetime = 80.0
let shortLifetime = 20.0
let titleLimit = 80
let bodyLimit = 240

func argument(_ flag: String) -> String? {
    let args = CommandLine.arguments
    guard let index = args.firstIndex(of: flag), index + 1 < args.count else { return nil }
    return args[index + 1]
}

func bounded(_ text: String, _ limit: Int) -> String {
    text.count <= limit ? text : String(text.prefix(limit - 1)) + "…"
}

/// Only hz's own web app on this machine: http, 127.0.0.1, an explicit port.
func appURL(_ text: String?) -> URL? {
    guard let text, let url = URL(string: text), url.scheme == "http", url.host == "127.0.0.1", url.port != nil,
        url.user == nil, url.password == nil
    else { return nil }
    return url
}

/// Only the shared hat palette can select a face; all artwork comes from this signed app bundle.
let hats: [String: String] = [
    "red": "#E8572A", "rust": "#B5532F", "ochre": "#B7841C", "green": "#43A047", "teal": "#1F9488",
    "blue": "#1E88E5", "purple": "#8E5BD8", "slate": "#64748B",
]

struct HelperFace {
    let attachment: UNNotificationAttachment
    let directory: URL
}

/// A bounded raster of bundled artwork in an atomically created owner-only temporary directory.
func face(hat: String?) -> HelperFace? {
    guard let hat, let colour = hats[hat], let source = Bundle.main.url(forResource: "face", withExtension: "svg"),
        var svg = try? String(contentsOf: source, encoding: .utf8)
    else { return nil }
    var template = Array(FileManager.default.temporaryDirectory.appendingPathComponent("hz-face-XXXXXX").path.utf8CString)
    let path = template.withUnsafeMutableBufferPointer { buffer -> String? in
        guard let pointer = buffer.baseAddress, let created = mkdtemp(pointer) else { return nil }
        return String(cString: created)
    }
    guard let path else { return nil }
    let directory = URL(fileURLWithPath: path, isDirectory: true)
    var retained = false
    defer {
        if !retained { try? FileManager.default.removeItem(at: directory) }
    }
    svg = svg.replacingOccurrences(of: "#E8572A", with: colour)
    do {
        let drawing = directory.appendingPathComponent("face.svg")
        try svg.write(to: drawing, atomically: true, encoding: .utf8)
        let size = 192
        guard let image = NSImage(contentsOf: drawing),
            let bitmap = NSBitmapImageRep(
                bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0,
                bitsPerPixel: 0)
        else { return nil }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        NSGraphicsContext.current?.imageInterpolation = .high
        image.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
        NSGraphicsContext.restoreGraphicsState()
        guard let png = bitmap.representation(using: .png, properties: [:]) else { return nil }
        let file = directory.appendingPathComponent("face.png")
        try png.write(to: file)
        let attachment = try UNNotificationAttachment(identifier: "face", url: file, options: nil)
        retained = true
        return HelperFace(attachment: attachment, directory: directory)
    } catch { return nil }
}

/// A stable notice id replaces an earlier post; malformed manual arguments receive an independent id.
func identifier(_ text: String?) -> String {
    guard let text, !text.isEmpty, text.count <= 120,
        text.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || "._:-".contains($0)) })
    else { return UUID().uuidString }
    return text
}

/// Leave at once. `exit` runs atexit handlers and can wait on a lock the main thread holds (macOS asking for
/// permission, a click being handled); an instance stuck there lives on and is never counted as done. `_exit` cannot.
func leave(_ code: Int32) -> Never {
    fflush(stdout)
    _exit(code)
}

func finish(_ result: String, _ code: Int32) -> Never {
    print(result)
    leave(code)
}

/// The longest an instance lives: macOS's question (60 s) and a margin to post, or a click's few seconds. A timer on
/// its own thread, not the main run loop, which is what may be stuck. Nothing, however it hangs, outlives it.
func exitAfter(_ seconds: Double) {
    DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + seconds) { leave(3) }
}

final class Notifier: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        exitAfter(CommandLine.arguments.contains("--message") ? messageLifetime : shortLifetime)
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        if CommandLine.arguments.contains("--status") {
            center.getNotificationSettings { settings in
                switch settings.authorizationStatus {
                case .authorized: finish("authorized", 0)
                case .denied: finish("denied", 0)
                case .notDetermined: finish("not-determined", 0)
                case .provisional: finish("provisional", 0)
                default: finish("unknown", 0)
                }
            }
            return
        }
        guard let message = argument("--message") else {
            // Launched by a click: the response arrives through the delegate; quit if it never does.
            DispatchQueue.main.asyncAfter(deadline: .now() + 10) { leave(0) }
            return
        }
        // macOS asks the person once; nobody may answer for a long time (at night, from the daemon).
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { finish("waiting", 3) }
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { finish("denied", 2) }
            let content = UNMutableNotificationContent()
            content.title = bounded(argument("--title") ?? "Heinzelmännchen", titleLimit)
            if let subtitle = argument("--subtitle") { content.subtitle = bounded(subtitle, titleLimit) }
            content.body = bounded(message, bodyLimit)
            if CommandLine.arguments.contains("--sound") { content.sound = .default }
            if let thread = argument("--thread") { content.threadIdentifier = bounded(thread, 200) }
            if let url = appURL(argument("--open")) { content.userInfo = ["url": url.absoluteString] }
            let helperFace = face(hat: argument("--hat"))
            if let helperFace { content.attachments = [helperFace.attachment] }
            let request = UNNotificationRequest(identifier: identifier(argument("--id")), content: content, trigger: nil)
            center.add(request) { error in
                // The system owns its attachment copy; remove only the private directory this process created.
                if let helperFace { try? FileManager.default.removeItem(at: helperFace.directory) }
                if let error { finish("failed: \(error.localizedDescription)", 1) }
                finish("posted", 0)
            }
        }
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
        withCompletionHandler done: @escaping () -> Void
    ) {
        if let url = appURL(response.notification.request.content.userInfo["url"] as? String) {
            NSWorkspace.shared.open(url)
        }
        done()
        leave(0)
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter, willPresent notification: UNNotification,
        withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        done([.banner, .list, .sound])
    }
}

let app = NSApplication.shared
let notifier = Notifier()
app.delegate = notifier
app.setActivationPolicy(.accessory)
app.run()
