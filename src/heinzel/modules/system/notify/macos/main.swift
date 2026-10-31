// Heinzel Notifier: hz's own macOS notifier (ADR 0043), built by `make notifier` and shipped in the wheel.
//
// hz starts it through LaunchServices (`open -n`), which puts it in the person's GUI session, also from the daemon.
//   --status                                   print the notification authorization and what the person chose:
//                                              `authorized banners=banner sound=enabled`, `denied ...`, ...
//   --delivered                                print the identifiers macOS still holds in Notification Center
//   --message M [--title T] [--subtitle S] [--open URL] [--hat COLOUR] [--sound] [--id ID] [--thread T]
//                                              ask for authorization once, post one notification, print the result.
//                                              A helper's notification: its name and reason as the subtitle, its face
//                                              (the bundled drawing, in its hat colour) beside it, the system sound
//                                              with --sound. A notification with the id of an earlier one replaces it.
//   (no arguments)                             launched by a click on a notification: open its URL, then quit
// Everything arrives as arguments, never as code; text is bounded; a URL opens only when it is hz's web app on this
// machine (http://127.0.0.1:<port>/...).
import AppKit
import UserNotifications

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

/// The hat colours of web/src/components/heinzel.ts (HATS); a test keeps the two in step.
let hats: [String: String] = [
    "red": "#E8572A", "rust": "#B5532F", "ochre": "#B7841C", "green": "#43A047", "teal": "#1F9488",
    "blue": "#1E88E5", "purple": "#8E5BD8", "slate": "#64748B",
]
let defaultHat = "#E8572A"
let faceSize = 192

/// What macOS knows a notification by: letters, digits and `._:-`, at most 120 of them; anything else gets a new one.
func identifier(_ text: String?) -> String {
    guard let text, !text.isEmpty, text.count <= 120,
        text.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || "._:-".contains($0)) })
    else { return UUID().uuidString }
    return text
}

/// The helper's face: the bundled drawing with its hat in `hat`'s colour, as a PNG file; nil if it cannot be drawn.
func face(hat: String?) -> URL? {
    guard let hat, let colour = hats[hat], let source = Bundle.main.url(forResource: "face", withExtension: "svg"),
        var svg = try? String(contentsOf: source, encoding: .utf8)
    else { return nil }
    svg = svg.replacingOccurrences(of: defaultHat, with: colour)
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("hz-face-\(UUID().uuidString)")
    do {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let drawing = directory.appendingPathComponent("face.svg")
        try svg.write(to: drawing, atomically: true, encoding: .utf8)
        guard let image = NSImage(contentsOf: drawing),
            let bitmap = NSBitmapImageRep(
                bitmapDataPlanes: nil, pixelsWide: faceSize, pixelsHigh: faceSize, bitsPerSample: 8,
                samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0,
                bitsPerPixel: 0)
        else { return nil }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        NSGraphicsContext.current?.imageInterpolation = .high
        image.draw(in: NSRect(x: 0, y: 0, width: faceSize, height: faceSize))
        NSGraphicsContext.restoreGraphicsState()
        guard let png = bitmap.representation(using: .png, properties: [:]) else { return nil }
        let file = directory.appendingPathComponent("face.png")
        try png.write(to: file)
        return file
    } catch { return nil }
}

func finish(_ result: String, _ code: Int32) -> Never {
    print(result)
    fflush(stdout)
    exit(code)
}

final class Notifier: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        if CommandLine.arguments.contains("--status") {
            center.getNotificationSettings { settings in
                let style: String
                switch settings.alertStyle {
                case .banner: style = "banner"
                case .alert: style = "alert"
                default: style = "none"
                }
                let sound = settings.soundSetting == .enabled ? "enabled" : "disabled"
                let detail = " banners=\(style) sound=\(sound)"
                switch settings.authorizationStatus {
                case .authorized: finish("authorized" + detail, 0)
                case .denied: finish("denied" + detail, 0)
                case .notDetermined: finish("not-determined", 0)
                case .provisional: finish("provisional" + detail, 0)
                default: finish("unknown", 0)
                }
            }
            return
        }
        if CommandLine.arguments.contains("--delivered") {
            center.getDeliveredNotifications { held in
                finish(held.isEmpty ? "none" : held.map { $0.request.identifier }.sorted().joined(separator: ","), 0)
            }
            return
        }
        guard let message = argument("--message") else {
            // Launched by a click: the response arrives through the delegate; quit if it never does.
            DispatchQueue.main.asyncAfter(deadline: .now() + 10) { NSApp.terminate(nil) }
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
            if let file = face(hat: argument("--hat")),
                let attachment = try? UNNotificationAttachment(identifier: "face", url: file, options: nil)
            {
                content.attachments = [attachment]
            }
            // The same identifier replaces the earlier notification: a repeat of one notice never stacks.
            let request = UNNotificationRequest(identifier: identifier(argument("--id")), content: content, trigger: nil)
            center.add(request) { error in
                // macOS moved the PNG into its own store; the drawing and its folder are ours to remove.
                if let file = content.attachments.first?.url {
                    try? FileManager.default.removeItem(at: file.deletingLastPathComponent())
                }
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
        NSApp.terminate(nil)
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
