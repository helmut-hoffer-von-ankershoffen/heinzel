import AppKit
import SwiftUI

@MainActor private final class Observations {
    var open: (() -> Void)?
    var brand: NSView?
    var heading: NSView?
    var rows: NSView?
    var footer: NSView?
    var directory: NSView?
    var directoryEnabled = false
    var informationEnabled = false
}

private struct ObserveView: NSViewRepresentable {
    let report: (NSView, Bool) -> Void
    func makeNSView(context: Context) -> NSView { NSView() }
    func updateNSView(_ view: NSView, context: Context) { report(view, context.environment.isEnabled) }
}

@main struct WorkspaceInformationSurfaceTests {
    @MainActor static func wait(for condition: () -> Bool) async throws {
        for _ in 0..<100 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        preconditionFailure("The information surface did not finish changing faces")
    }

    @MainActor static func exercise(width: CGFloat, height: CGFloat) async throws {
        let observations = Observations()
        let surface = WorkspaceInformationSurface(version: "0.9.58", background: .white) { button in
            button.frame(width: width, height: height).background(ObserveView { view, enabled in
                observations.open = button.open
                observations.directory = view
                observations.directoryEnabled = enabled
            })
        } details: {
            Text("Workspace server 0.9.57").background(ObserveView { _, enabled in
                observations.informationEnabled = enabled
            })
        }
        let host = NSHostingView(rootView: surface)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await wait { observations.directoryEnabled && observations.open != nil }
        let original = observations.directory
        let size = host.bounds.size
        precondition(!observations.informationEnabled)
        let escape = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
            windowNumber: window.windowNumber, context: nil, characters: "\u{1B}", charactersIgnoringModifiers: "\u{1B}",
            isARepeat: false, keyCode: 53)!
        for _ in 0..<2 {
            observations.open?()
            try await wait { observations.informationEnabled && !observations.directoryEnabled }
            precondition(host.bounds.size == size && observations.directory === original)
            precondition(window.performKeyEquivalent(with: escape))
            try await wait { !observations.informationEnabled && observations.directoryEnabled }
            precondition(host.bounds.size == size && observations.directory === original)
        }
    }

    @MainActor static func checkStartFooter(rows: Int, size: CGSize, typeSize: DynamicTypeSize) async throws {
        let observations = Observations()
        let layout = WorkspaceStartLayout {
            Text("Heinzelmännchen").frame(height: 44)
                .background(ObserveView { view, _ in observations.brand = view })
        } heading: {
            Text("Workspaces").frame(height: 44)
                .background(ObserveView { view, _ in observations.heading = view })
        } rows: {
            VStack(spacing: 0) {
                ForEach(0..<rows, id: \.self) { index in Text("Workspace " + String(index)).frame(height: 50) }
            }.background(ObserveView { view, _ in observations.rows = view })
        } footer: {
            Text("v0.9.58").frame(width: 92, height: 44)
                .background(ObserveView { view, _ in observations.footer = view })
        }
        .environment(\.dynamicTypeSize, typeSize)
        .frame(width: size.width, height: size.height)
        let host = NSHostingView(rootView: layout)
        let window = NSWindow(contentRect: CGRect(origin: .zero, size: size), styleMask: [.borderless],
                              backing: .buffered, defer: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await wait { observations.footer?.window === window && observations.heading?.window === window }
        func frame(_ view: NSView?) -> CGRect { view!.convert(view!.bounds, to: nil) }
        let footer = frame(observations.footer)
        precondition(abs(footer.midX - size.width / 2) <= 1 && abs(footer.minY) <= 1)
        let brand = frame(observations.brand)
        precondition(abs(brand.maxY - (size.height - 8)) <= 1)
        let availableTop = brand.minY - 8
        if rows > 0 && CGFloat(rows * 50 + 50) < availableTop - footer.maxY {
            try await wait {
                let top = frame(observations.heading).maxY
                let bottom = frame(observations.rows).minY
                return abs(top + bottom - availableTop - footer.maxY) <= 2
            }
        } else if rows > 0 {
            try await wait { abs(frame(observations.heading).maxY - availableTop) <= 1 }
        }
    }

    @MainActor static func main() async throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        try await checkStartFooter(rows: 0, size: CGSize(width: 600, height: 360), typeSize: .large)
        for height: CGFloat in [600, 850, 1100] {
            for rows in [4, 24] {
                try await checkStartFooter(rows: rows, size: CGSize(width: 900, height: height),
                                           typeSize: rows == 24 ? .accessibility2 : .large)
            }
        }
        print("PASS: Mac footer stays at bottom center in empty and scrolling directories")
        try await exercise(width: 320, height: 600)
        try await exercise(width: 460, height: 340)
        print("PASS: information and Back keep the original directory and geometry for both surface sizes")
    }
}
