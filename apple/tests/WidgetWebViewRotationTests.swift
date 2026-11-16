import AppKit
import SwiftUI
import WebKit

@MainActor final class FixtureState: ObservableObject {
    @Published var currentID = "first"
    @Published var currentEndpoint = "https://example.test"
    @Published var visibleEndpoint = "https://example.test"
    let store = WKWebsiteDataStore.nonPersistent()
    var made: [WKWebView] = []
    var dismantled = 0
}

struct FixtureWebView: NSViewRepresentable {
    let fixture: FixtureState
    func makeCoordinator() -> FixtureState { fixture }
    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = fixture.store
        let view = WKWebView(frame: .zero, configuration: configuration)
        fixture.made.append(view)
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {}
    static func dismantleNSView(_ view: WKWebView, coordinator: FixtureState) {
        view.stopLoading()
        coordinator.dismantled += 1
    }
}

struct FixtureHost: View {
    @ObservedObject var fixture: FixtureState
    @State private var rotation = WidgetViewRotation()
    @State private var revision = 0
    var body: some View {
        FixtureWebView(fixture: fixture).id(revision)
            .onReceive(NotificationCenter.default.publisher(for: .fixtureRotation).receive(on: RunLoop.main)) { note in
                guard rotation.shouldReload(notificationID: note.object as? String, currentID: fixture.currentID,
                                            currentEndpoint: fixture.currentEndpoint,
                                            visibleEndpoint: fixture.visibleEndpoint) else { return }
                revision += 1
            }
    }
}

extension Notification.Name { static let fixtureRotation = Notification.Name("FixtureWidgetRotation") }

@main struct WidgetWebViewRotationTests {
    @MainActor static func main() {
        _ = NSApplication.shared
        let fixture = FixtureState()
        let host = NSHostingView(rootView: FixtureHost(fixture: fixture))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 320, height: 480),
                              styleMask: .borderless, backing: .buffered, defer: false)
        window.contentView = host
        func settle() {
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(0.25))
            host.layoutSubtreeIfNeeded()
        }
        settle()
        precondition(fixture.made.count == 1)
        let original = fixture.made[0]
        NotificationCenter.default.post(name: .fixtureRotation, object: "stale")
        settle()
        precondition(fixture.made.count == 1 && fixture.dismantled == 0)
        NotificationCenter.default.post(name: .fixtureRotation, object: "first")
        settle()
        precondition(fixture.made.count == 2 && fixture.dismantled == 1 && fixture.made[1] !== original)
        precondition(fixture.made[1].configuration.websiteDataStore === fixture.store)
        NotificationCenter.default.post(name: .fixtureRotation, object: "first")
        settle()
        precondition(fixture.made.count == 2)
        fixture.currentID = "second"
        fixture.visibleEndpoint = "https://other.test"
        NotificationCenter.default.post(name: .fixtureRotation, object: "second")
        settle()
        precondition(fixture.made.count == 2)
        fixture.visibleEndpoint = "https://example.test"
        NotificationCenter.default.post(name: .fixtureRotation, object: "second")
        settle()
        precondition(fixture.made.count == 3 && fixture.dismantled == 2)
        window.contentView = nil
        print("PASS: SwiftUI tears down and recreates real WebKit views")
    }
}
