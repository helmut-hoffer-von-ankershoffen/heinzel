import Foundation
import UserNotifications

/// Local alerts from the existing notice stream, with independent consent for each connected workspace.
@MainActor
final class WorkspaceNotifications: NSObject, UNUserNotificationCenterDelegate {
    static let shared = WorkspaceNotifications()
    static let opened = Notification.Name("heinzelWorkspaceNoticeOpened")
    static let changed = Notification.Name("heinzelWorkspaceNoticeChanged")

    private let center = UNUserNotificationCenter.current()
    private let preference = "heinzelNativeNotificationWorkspaces"
    private var authorizing: String?
    private var requestingStartupAuthorization = false
    private var authorizationCancelled = false
    private var openedRoute: (scope: String, path: String)?

    func install() { center.delegate = self }

    func requestStartupAuthorization(whileForeground: () -> Bool) async {
        guard !requestingStartupAuthorization, authorizing == nil else { return }
        requestingStartupAuthorization = true
        defer { requestingStartupAuthorization = false }
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .notDetermined, !Task.isCancelled, whileForeground() else { return }
        _ = try? await center.requestAuthorization(options: [.alert])
        NotificationCenter.default.post(name: Self.changed, object: nil)
    }

    private func scope(for endpoint: Endpoint) -> String {
        // The locally owned Mac service binds a fresh loopback port at each launch.
        endpoint.origin.scheme == "http" ? "local" : endpoint.origin.absoluteString
    }

    private var enabled: Set<String> {
        get { Set(UserDefaults.standard.stringArray(forKey: preference) ?? []) }
        set { UserDefaults.standard.set(newValue.sorted(), forKey: preference) }
    }

    func status(for endpoint: Endpoint) async -> String {
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .denied:
            return "denied"
        case .authorized, .ephemeral:
            guard settings.alertSetting == .enabled || settings.notificationCenterSetting == .enabled else {
                return "denied"
            }
            return enabled.contains(scope(for: endpoint)) ? "on" : "off"
        default:
            return "off"
        }
    }

    func setEnabled(
        _ choice: Bool, for endpoint: Endpoint, workspaceIsCurrent: () -> Bool,
        confirm: () async -> Bool
    ) async -> String {
        let key = scope(for: endpoint)
        if !choice {
            if authorizing == key { authorizationCancelled = true }
            enabled.remove(key)
            NotificationCenter.default.post(name: Self.changed, object: nil)
            return await status(for: endpoint)
        }
        guard authorizing == nil, !requestingStartupAuthorization, !enabled.contains(key) else {
            return await status(for: endpoint)
        }
        authorizing = key
        authorizationCancelled = false
        defer { authorizing = nil }
        guard await confirm(), !authorizationCancelled, workspaceIsCurrent() else {
            return await status(for: endpoint)
        }
        if (try? await center.requestAuthorization(options: [.alert])) == true,
           !authorizationCancelled, workspaceIsCurrent() {
            enabled.insert(key)
            NotificationCenter.default.post(name: Self.changed, object: nil)
        }
        return await status(for: endpoint)
    }

    func show(_ value: [String: Any], for endpoint: Endpoint, workspaceIsCurrent: () -> Bool) async -> Bool {
        guard let id = value["id"] as? String, !id.isEmpty, id.utf8.count <= 256,
              let title = value["title"] as? String, !title.isEmpty, title.utf8.count <= 1024,
              let body = value["body"] as? String, body.utf8.count <= 8192,
              let path = value["path"] as? String, Self.permitsPath(path, for: endpoint) else { return false }
        let expiry = value["expires"]
        guard expiry is NSNull || expiry is Double else { return false }
        let expires = expiry as? Double ?? .infinity
        guard expires > Date().timeIntervalSince1970 * 1000,
              await status(for: endpoint) == "on", workspaceIsCurrent(),
              expires > Date().timeIntervalSince1970 * 1000 else { return false }
        let key = scope(for: endpoint)
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.userInfo = ["workspace": key, "path": path]
        do {
            try await center.add(UNNotificationRequest(identifier: key + ":" + id, content: content, trigger: nil))
            return true
        } catch {
            return false
        }
    }

    static func permitsPath(_ path: String, for endpoint: Endpoint) -> Bool {
        guard path.utf8.count <= 4096, path.hasPrefix("/"), !path.hasPrefix("//"), !path.contains("\\"),
              !path.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains),
              let destination = URL(string: path, relativeTo: endpoint.origin)?.absoluteURL else { return false }
        return endpoint.owns(destination)
    }

    func takeOpenedPath(for endpoint: Endpoint) -> String? {
        guard let selection = openedRoute, selection.scope == scope(for: endpoint) else { return nil }
        openedRoute = nil
        return Self.permitsPath(selection.path, for: endpoint) ? selection.path : nil
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .list]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse
    ) async {
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier,
              let scope = response.notification.request.content.userInfo["workspace"] as? String,
              let path = response.notification.request.content.userInfo["path"] as? String else { return }
        await MainActor.run {
            openedRoute = (scope, path)
            NotificationCenter.default.post(name: Self.opened, object: nil)
        }
    }
}
