#if os(macOS)
import AppKit
import Carbon
import ServiceManagement
import SwiftUI

/// Persist physical key codes, independent of the current keyboard layout.
struct QuickChatShortcut: Codable, Equatable {
    var key: UInt32 = 49
    var modifiers: UInt32 = UInt32(optionKey)
    var keyName: String? = nil
    static let standard = Self()
    var label: String {
        let flags = modifiers
        return "\(flags & UInt32(controlKey) != 0 ? "⌃" : "")\(flags & UInt32(optionKey) != 0 ? "⌥" : "")\(flags & UInt32(shiftKey) != 0 ? "⇧" : "")\(flags & UInt32(cmdKey) != 0 ? "⌘" : "")\(key == 49 ? "Space" : keyName ?? "Custom key")"
    }
    var valid: Bool {
        key < 128 && modifiers != 0 && modifiers & ~UInt32(cmdKey | optionKey | controlKey | shiftKey) == 0
    }
}

@MainActor
protocol MenuSystemAdapter: AnyObject {
    func unregister() throws
    func register(_ shortcut: QuickChatShortcut, action: @escaping @MainActor () -> Void) throws
    var loginStatus: String { get }
    func setLogin(_ enabled: Bool) throws
}

@MainActor @Observable
final class MenuPreferences {
    private(set) var shortcut: QuickChatShortcut?
    private(set) var shortcutMessage = ""
    private(set) var loginStatus = ""
    private(set) var loginError: String?
    private let defaults: UserDefaults
    private let system: MenuSystemAdapter
    private var action: (@MainActor () -> Void)?
    private var started = false

    init(defaults: UserDefaults = .standard, system: MenuSystemAdapter = MacMenuSystemAdapter()) {
        self.defaults = defaults
        self.system = system
        if defaults.bool(forKey: "quickChatShortcutDisabled") {
            shortcut = nil
        } else if let data = defaults.data(forKey: "quickChatShortcut"),
                  let saved = try? JSONDecoder().decode(QuickChatShortcut.self, from: data), saved.valid {
            shortcut = saved
        } else {
            shortcut = .standard
        }
    }

    func start(action: @escaping @MainActor () -> Void) {
        guard !started else { return }
        started = true
        self.action = action
        apply(shortcut)
        refreshLogin()
    }

    func apply(_ choice: QuickChatShortcut?) {
        guard choice?.valid != false else {
            shortcutMessage = "Choose a key with Command, Option, Control or Shift."
            return
        }
        do {
            try system.unregister()
            shortcut = choice
            defaults.set(choice == nil, forKey: "quickChatShortcutDisabled")
            defaults.set(choice.flatMap { try? JSONEncoder().encode($0) }, forKey: "quickChatShortcut")
            if let choice, let action { try system.register(choice, action: action) }
            shortcutMessage = choice == nil ? "Shortcut off" : "Shortcut active"
        } catch {
            shortcutMessage = "Shortcut unavailable. It may be used by macOS or another app. Choose another combination or turn it off. (\(error.localizedDescription))"
        }
    }

    func refreshLogin() { loginStatus = system.loginStatus }
    func setLogin(_ enabled: Bool) {
        loginError = nil
        do { try system.setLogin(enabled) }
        catch { loginError = "Could not change launch at login: \(error.localizedDescription)" }
        refreshLogin()
    }
    func stop() {
        do { try system.unregister() }
        catch { shortcutMessage = "Could not release shortcut: \(error.localizedDescription)" }
        started = false
    }
}

@MainActor
final class MacMenuSystemAdapter: MenuSystemAdapter {
    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private var action: (@MainActor () -> Void)?

    func unregister() throws {
        if let hotKey {
            let status = UnregisterEventHotKey(hotKey)
            guard status == noErr else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
            self.hotKey = nil
        }
        if let handler {
            let status = RemoveEventHandler(handler)
            guard status == noErr else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
            self.handler = nil
        }
        action = nil
    }

    func register(_ shortcut: QuickChatShortcut, action: @escaping @MainActor () -> Void) throws {
        try unregister()
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let installed = InstallEventHandler(GetApplicationEventTarget(), { _, _, context in
            guard let context else { return OSStatus(eventNotHandledErr) }
            MainActor.assumeIsolated {
                Unmanaged<MacMenuSystemAdapter>.fromOpaque(context).takeUnretainedValue().action?()
            }
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), &handler)
        guard installed == noErr else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(installed)) }
        self.action = action
        let status = RegisterEventHotKey(shortcut.key, shortcut.modifiers,
                                        EventHotKeyID(signature: 0x485A5143, id: 1),
                                        GetApplicationEventTarget(), 0, &hotKey)
        guard status == noErr else {
            try unregister()
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }

    var loginStatus: String {
        switch SMAppService.mainApp.status {
        case .enabled: "On"
        case .notRegistered: "Off"
        case .requiresApproval: "Approval required or revoked in System Settings"
        case .notFound: "App registration unavailable"
        @unknown default: "Unknown login status"
        }
    }
    func setLogin(_ enabled: Bool) throws {
        if enabled { try SMAppService.mainApp.register() }
        else { try SMAppService.mainApp.unregister() }
    }
}

struct ShortcutRecorder: NSViewRepresentable {
    let changed: (QuickChatShortcut) -> Void
    func makeNSView(context: Context) -> Recorder { Recorder(changed: changed) }
    func updateNSView(_ view: Recorder, context: Context) { view.changed = changed }
    final class Recorder: NSTextField {
        var changed: (QuickChatShortcut) -> Void
        init(changed: @escaping (QuickChatShortcut) -> Void) {
            self.changed = changed
            super.init(frame: .zero)
            placeholderString = "Click, then press shortcut"
            isEditable = false
            setAccessibilityLabel("Record Quick Chat shortcut")
        }
        required init?(coder: NSCoder) { fatalError("init(coder:) unavailable") }
        override var acceptsFirstResponder: Bool { true }
        override func mouseDown(with event: NSEvent) { window?.makeFirstResponder(self) }
        override func performKeyEquivalent(with event: NSEvent) -> Bool {
            guard window?.firstResponder === self else { return false }
            keyDown(with: event)
            return true
        }
        override func keyDown(with event: NSEvent) {
            if event.keyCode == 53 { window?.makeFirstResponder(nil); return }
            let flags = event.modifierFlags
            var modifiers: UInt32 = 0
            for (flag, carbon) in [(NSEvent.ModifierFlags.command, cmdKey), (.option, optionKey),
                                   (.control, controlKey), (.shift, shiftKey)] where flags.contains(flag) {
                modifiers |= UInt32(carbon)
            }
            changed(QuickChatShortcut(key: UInt32(event.keyCode), modifiers: modifiers,
                                      keyName: event.charactersIgnoringModifiers?.uppercased()))
            stringValue = "\(flags.contains(.control) ? "⌃" : "")\(flags.contains(.option) ? "⌥" : "")\(flags.contains(.shift) ? "⇧" : "")\(flags.contains(.command) ? "⌘" : "")\(event.keyCode == 49 ? "Space" : event.charactersIgnoringModifiers?.uppercased() ?? "Key")"
            window?.makeFirstResponder(nil)
        }
    }
}
#endif
