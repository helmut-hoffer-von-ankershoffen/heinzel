import CoreFoundation
import SwiftUI
import WebKit
#if DEBUG
import OSLog
#endif

#if os(macOS)
struct TitleBarDragLayout {
    let width: CGFloat
    let regions: [CGRect]
    let excluded: [CGRect]

    init?(_ message: Any) {
        guard let layout = message as? [String: Any],
              let width = layout["width"] as? Double, width.isFinite, width > 0,
              let regions = layout["regions"] as? [[Double]],
              let excluded = layout["excluded"] as? [[Double]] else { return nil }
        self.width = width
        self.regions = regions.compactMap(Self.rectangle)
        self.excluded = excluded.compactMap(Self.rectangle)
    }

    private static func rectangle(_ values: [Double]) -> CGRect? {
        guard values.count == 4, values.allSatisfy(\.isFinite), values[2] > 0, values[3] > 0 else { return nil }
        return CGRect(x: values[0], y: values[1], width: values[2], height: values[3])
    }

    @MainActor
    func contains(_ event: NSEvent, in view: WKWebView) -> Bool {
        guard let window = view.window, view.bounds.width > 0 else { return false }
        for role in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] {
            if let button = window.standardWindowButton(role), !button.isHiddenOrHasHiddenAncestor,
               button.bounds.contains(button.convert(event.locationInWindow, from: nil)) { return false }
        }
        let local = view.convert(event.locationInWindow, from: nil)
        guard view.bounds.contains(local) else { return false }
        let scale = width / view.bounds.width
        let y = view.isFlipped ? local.y - view.bounds.minY : view.bounds.maxY - local.y
        let point = CGPoint(x: (local.x - view.bounds.minX) * scale, y: y * scale)
        return regions.contains { $0.contains(point) } && !excluded.contains { $0.contains(point) }
    }
}
#endif
