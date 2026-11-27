import Foundation
import CoreFoundation

/// Measurements are consumed with the Watch action, never cached across web documents.
enum SoundDockGeometry {
    static func requestedWidth(_ payload: [String: NSNumber]?, viewport: CGFloat) -> CGFloat? {
        guard let payload, let width = payload["width"], let web = payload["viewportWidth"],
              CFGetTypeID(width) != CFBooleanGetTypeID(), CFGetTypeID(web) != CFBooleanGetTypeID(),
              width.doubleValue.isFinite, web.doubleValue.isFinite,
              width.doubleValue > 0, web.doubleValue > 0,
              width.doubleValue <= web.doubleValue,
              abs(web.doubleValue - Double(viewport)) < 2 else { return nil }
        return CGFloat(width.doubleValue)
    }

    static func width(requested: CGFloat?, available: CGFloat) -> CGFloat {
        let limit = available.isFinite && available > 0 ? available : 600
        let desired = requested.flatMap { $0.isFinite && $0 > 0 ? $0 : nil } ?? 480
        return min(limit, max(200, min(720, desired)))
    }

    static func aspect(_ size: CGSize) -> CGFloat {
        guard size.width.isFinite, size.height.isFinite, size.width > 0, size.height > 0 else { return 16 / 9 }
        return size.width / size.height
    }
}
