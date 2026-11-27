import UserNotifications

@main struct WorkspaceAlertAccessTests {
    static func main() {
        typealias Case = (UNAuthorizationStatus, UNNotificationSetting, UNNotificationSetting, Bool?,
                          WorkspaceAlertAccess, String)
        var cases: [Case] = [
            (.denied, .enabled, .enabled, true, .denied, "denied"),
            (.notDetermined, .disabled, .disabled, true, .permissionNeeded, "off"),
            (.provisional, .disabled, .enabled, true, .permissionNeeded, "off"),
            (.authorized, .disabled, .disabled, true, .deliveryDisabled, "denied"),
            (.authorized, .enabled, .disabled, nil, .appAllowed, "off"),
            (.authorized, .enabled, .enabled, false, .workspaceOff, "off"),
            (.authorized, .enabled, .disabled, true, .workspaceOn, "on"),
            (.authorized, .disabled, .enabled, true, .workspaceOn, "on"),
            (.authorized, .notSupported, .disabled, false, .deliveryDisabled, "denied"),
        ]
        #if os(iOS)
        cases.append((.ephemeral, .enabled, .enabled, true, .workspaceOn, "on"))
        #endif
        for (authorization, alerts, center, optedIn, expected, bridge) in cases {
            let access = WorkspaceAlertAccess(authorization: authorization, alerts: alerts,
                                              notificationCenter: center, workspaceOptIn: optedIn)
            precondition(access == expected && access.bridgeValue == bridge)
        }
        print("PASS: notification diagnostics and delivery agree in \(cases.count) cases")
    }
}
