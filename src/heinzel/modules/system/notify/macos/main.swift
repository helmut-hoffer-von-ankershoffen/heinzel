// Heinzel Notifier: hz's own macOS notifier (ADR 0043), built by `make notifier` and shipped in the wheel.
//
// hz starts it through LaunchServices (`open -n`), which puts it in the person's GUI session, also from the daemon.
//   --status                                   print the notification authorization: authorized, denied, ...
//   --message M [--title T] [--subtitle S] [--open URL]
//                                              ask for authorization once, post one notification, print the result
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
            if let url = appURL(argument("--open")) { content.userInfo = ["url": url.absoluteString] }
            let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
            center.add(request) { error in
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
        done([.banner, .list])
    }
}

let app = NSApplication.shared
let notifier = Notifier()
app.delegate = notifier
app.setActivationPolicy(.accessory)
app.run()
