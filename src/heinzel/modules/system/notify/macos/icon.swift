// Draws Heinzelmännchen's icon as a PNG file: `icon <svg> <png> <size> [tile|maskable]`. The artwork is heinzel.svg
// beside this file, rendered by AppKit (macOS 14 or later reads SVG); nothing is drawn here but its background.
// Without a style the drawing stays transparent, like the favicon. `tile` puts it on the app's dark #181818 rounded
// square, as the notifier's and the web app's icons show it. `maskable` fills the whole square and keeps the face and
// the ears inside the middle 80 percent circle that installed apps' masks keep. Run only by `make notifier` and
// `make icons`.
import AppKit

let arguments = CommandLine.arguments
let style = arguments.count > 4 ? arguments[4] : ""
guard arguments.count >= 4, let pixels = Int(arguments[3]), pixels > 0, ["", "tile", "maskable"].contains(style)
else {
    FileHandle.standardError.write("usage: icon <svg> <png> <size> [tile|maskable]\n".data(using: .utf8)!)
    exit(64)
}
guard let artwork = NSImage(contentsOf: URL(fileURLWithPath: arguments[1])) else {
    FileHandle.standardError.write("icon: can't read \(arguments[1]) (macOS 14 or later reads SVG)\n".data(using: .utf8)!)
    exit(66)
}

let bitmap = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4,
    hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSGraphicsContext.current!.imageInterpolation = .high
let side = CGFloat(pixels)
let square = NSRect(x: 0, y: 0, width: side, height: side)
let dark = NSColor(srgbRed: 0x18 / 255, green: 0x18 / 255, blue: 0x18 / 255, alpha: 1)

// How large the drawing is on its background; it stands on the bottom edge, the tunic cut off like a portrait's.
var scale: CGFloat = 1
switch style {
case "tile":
    let tile = NSBezierPath(roundedRect: square, xRadius: side * 112 / 512, yRadius: side * 112 / 512)
    dark.setFill()
    tile.fill()
    tile.addClip()
    scale = 0.84
case "maskable":
    dark.setFill()
    NSBezierPath(rect: square).fill()
    scale = 0.8
default:
    break
}
let size = side * scale
artwork.draw(in: NSRect(x: (side - size) / 2, y: 0, width: size, height: size))

NSGraphicsContext.restoreGraphicsState()
guard let png = bitmap.representation(using: .png, properties: [:]) else { exit(1) }
do {
    try png.write(to: URL(fileURLWithPath: arguments[2]))
} catch {
    exit(1)
}
