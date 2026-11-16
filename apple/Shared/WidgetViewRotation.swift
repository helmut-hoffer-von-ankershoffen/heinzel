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

    static func shouldReloadRetirement<T: Equatable>(_ retired: T?, current: T?, endpoint: String?,
                                                    visibleEndpoint: String?, credentialFree: Bool) -> Bool {
        guard let retired, retired == current, credentialFree, let endpoint, let visibleEndpoint else { return false }
        return endpoint == visibleEndpoint
    }
}
