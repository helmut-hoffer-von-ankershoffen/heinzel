#if os(macOS)
import Foundation

@MainActor
private final class FakeMenuSystem: MenuSystemAdapter {
    var calls: [String] = []
    var conflict = false
    var loginStatus = "Off"
    func unregister() throws { calls.append("unregister") }
    func register(_ shortcut: QuickChatShortcut, action: @escaping @MainActor () -> Void) throws {
        calls.append("register")
        if conflict { throw NSError(domain: "fixture", code: 1) }
    }
    func setLogin(_ enabled: Bool) throws {
        calls.append(enabled ? "login-on" : "login-off")
        loginStatus = enabled ? "Approval required" : "Off"
    }
}

@main
struct MenuPreferencesTests {
    @MainActor static func main() {
        let suite = "MenuPreferencesTests.\(UUID())"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let adapter = FakeMenuSystem()
        let model = MenuPreferences(defaults: defaults, system: adapter)
        precondition(model.shortcut == .standard)
        model.start {}
        model.start {}
        precondition(adapter.calls == ["unregister", "register"])
        precondition(model.loginStatus == "Off")
        print("PASS default-option-space-single-handler-login-off")

        let alternative = QuickChatShortcut(key: 40, modifiers: 256)
        model.apply(alternative)
        precondition(Array(adapter.calls.suffix(2)) == ["unregister", "register"])
        precondition(MenuPreferences(defaults: defaults, system: adapter).shortcut == alternative)
        model.apply(nil)
        precondition(adapter.calls.last == "unregister")
        precondition(MenuPreferences(defaults: defaults, system: adapter).shortcut == nil)
        print("PASS replacement-order-persisted-choice-disable")

        adapter.conflict = true
        model.apply(.standard)
        precondition(model.shortcutMessage.contains("unavailable"))
        precondition(model.shortcut == .standard)
        let before = adapter.calls
        model.apply(QuickChatShortcut(key: 49, modifiers: 0))
        precondition(adapter.calls == before)
        model.stop()
        precondition(adapter.calls.last == "unregister")
        print("PASS conflict-invalid-choice-shutdown")

        model.setLogin(true)
        precondition(model.loginStatus == "Approval required")
        adapter.loginStatus = "Off"
        model.refreshLogin()
        precondition(model.loginStatus == "Off")
        model.setLogin(false)
        precondition(adapter.calls.last == "login-off")
        print("PASS observed-login-approval-revocation-disable")
    }
}
#endif
