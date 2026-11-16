import Foundation

/// Accept each currently authoritative activation only once for the visible workspace.
struct WidgetViewRotation {
    private var handled: String?

    mutating func shouldReload(notificationID: String?, currentID: String?, currentEndpoint: String?,
                               visibleEndpoint: String?) -> Bool {
        guard let id = notificationID, id != handled, id == currentID,
              let endpoint = visibleEndpoint, currentEndpoint == endpoint else { return false }
        handled = id
        return true
    }
}
