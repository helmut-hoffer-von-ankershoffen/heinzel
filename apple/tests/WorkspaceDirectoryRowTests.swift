import AppKit
import SwiftUI

@main struct WorkspaceDirectoryRowTests {
    @MainActor static func pixels(name: String, version: String, width: Int, active: Bool, manual: Bool) -> [UInt8] {
        let row = WorkspaceDirectoryRow(name: name, detail: "Online", version: version, symbol: "network",
            session: active ? .connected : .other, userAdded: manual, busy: false, open: {}, perform: { _ in })
        precondition(row.action == (active ? .disconnect : (manual ? .remove : nil)))
        let view = row.frame(width: CGFloat(width), height: 100)
            .foregroundStyle(.black).background(.white).environment(\.colorScheme, .light)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = renderer.cgImage!
        let stride = image.width * 4
        var rgba = [UInt8](repeating: 255, count: stride * image.height)
        rgba.withUnsafeMutableBytes { buffer in
            let context = CGContext(data: buffer.baseAddress, width: image.width, height: image.height, bitsPerComponent: 8,
                                    bytesPerRow: stride, space: CGColorSpaceCreateDeviceRGB(),
                                    bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
            context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        }
        return rgba
    }

    /// Changing one monospaced digit isolates the rendered version from the name, status and action.
    @MainActor static func versionX(name: String, width: Int, active: Bool = false, manual: Bool = false) -> Int {
        let first = pixels(name: name, version: "0.9.58", width: width, active: active, manual: manual)
        let second = pixels(name: name, version: "0.9.51", width: width, active: active, manual: manual)
        let pixelWidth = width * 2
        for x in 0..<pixelWidth {
            for offset in stride(from: x * 4, to: first.count, by: pixelWidth * 4) {
                // In this light fixture the caption ink is 128 on white (255). Look for a
                // glyph stroke; independently rasterized unchanged edges can round a level apart.
                if abs(Int(first[offset]) - Int(second[offset])) >= 64 { return x }
            }
        }
        preconditionFailure("Changing the version must change a visible digit stroke")
    }

    @MainActor static func main() {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        let source = URL(string: "https://studio.example")!
        let destination = URL(string: "https://wren.example")!
        let revoked = WorkspaceDirectoryRow.Session(origin: source, retained: source, confirmed: nil)
        precondition(revoked == .needsRecovery)
        precondition(WorkspaceDirectoryRow.Session(origin: destination, retained: source, confirmed: nil) == .other)
        let retained = WorkspaceDirectoryRow(name: "Studio", detail: "Sign in", version: nil,
            symbol: "icloud", session: revoked, userAdded: false, busy: false, open: {}, perform: { _ in })
        precondition(retained.action == .disconnect)
        var ivyPositions: [Int] = []
        var alderPositions: [Int] = []
        for width in [320, 390, 420] {
            let position = versionX(name: "Ivy", width: width)
            let longer = versionX(name: "Alder Studio", width: width)
            precondition(longer > position + 20, "Width \(width): Ivy at \(position) px, Alder Studio at \(longer) px")
            for (active, manual) in [(true, true), (true, false), (false, true)] {
                for (name, expected) in [("Ivy", position), ("Alder Studio", longer)] {
                    let actual = versionX(name: name, width: width, active: active, manual: manual)
                    precondition(actual == expected, "\(name), width \(width), active \(active), manual \(manual): \(actual) px vs \(expected) px")
                }
            }
            ivyPositions.append(position)
            alderPositions.append(longer)
            print("PASS: width", width, "places the version beside its workspace name (Ivy \(position) px, Alder Studio \(longer) px)")
        }
        precondition(Set(ivyPositions).count == 1 && Set(alderPositions).count == 1,
                     "Changing the row width moved a version: Ivy \(ivyPositions), Alder Studio \(alderPositions)")
        // The extra 44pt taken by a real action eventually wraps the version after a long name.
        // An inactive discovered row must be able to use that space.
        let usesReleasedSpace = (7...23).contains { length in
            let name = String(repeating: "W", count: length)
            return versionX(name: name, width: 320) > versionX(name: name, width: 320, manual: true) + 60
        }
        precondition(usesReleasedSpace, "An unused row action must release horizontal space")
        print("PASS: rows without an action use the available width")
    }
}
