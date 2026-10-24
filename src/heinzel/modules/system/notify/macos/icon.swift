// Draws Heinzelmännchen's icon as a PNG file: `icon <path> <size> [maskable]`. An original drawing, the same shapes
// as web/public/favicon.svg (a 512 by 512 design): a pointed clay hat with a cream brim and bobble, a face with two
// eyes and a nose, a cream beard, on the app's dark #181818. `maskable` fills the whole square and keeps the drawing
// in the middle 80 percent, as installed apps' masks expect. Built and run only by `make notifier` and `make icons`.
import AppKit

let arguments = CommandLine.arguments
guard arguments.count >= 3, let pixels = Int(arguments[2]), pixels > 0 else {
    FileHandle.standardError.write("usage: icon <path> <size> [maskable]\n".data(using: .utf8)!)
    exit(64)
}
let maskable = arguments.count > 3 && arguments[3] == "maskable"

func color(_ hex: UInt32) -> NSColor {
    NSColor(
        srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
        blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}

let dark = color(0x181818)
let clay = color(0xD97757)
let cream = color(0xF5ECD9)
let skin = color(0xF2C9A8)
let nose = color(0xE8A07F)

let bitmap = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4,
    hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
let context = NSGraphicsContext.current!.cgContext
// The design's coordinates: 512 by 512, y down, like the SVG.
context.translateBy(x: 0, y: CGFloat(pixels))
context.scaleBy(x: CGFloat(pixels) / 512, y: -CGFloat(pixels) / 512)

dark.setFill()
if maskable {
    NSBezierPath(rect: NSRect(x: 0, y: 0, width: 512, height: 512)).fill()
    context.translateBy(x: 51.2, y: 51.2)
    context.scaleBy(x: 0.8, y: 0.8)
} else {
    NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: 512, height: 512), xRadius: 112, yRadius: 112).fill()
}

func circle(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat, _ fill: NSColor) {
    fill.setFill()
    NSBezierPath(ovalIn: NSRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r)).fill()
}

// The beard, behind the face.
let beard = NSBezierPath()
beard.move(to: NSPoint(x: 168, y: 352))
beard.curve(to: NSPoint(x: 344, y: 352), controlPoint1: NSPoint(x: 176, y: 500), controlPoint2: NSPoint(x: 336, y: 500))
beard.close()
cream.setFill()
beard.fill()
circle(256, 340, 72, skin)
circle(228, 336, 9, dark)
circle(284, 336, 9, dark)
circle(256, 362, 14, nose)
// The hat: a point leaning a little, a brim, a bobble.
let hat = NSBezierPath()
hat.move(to: NSPoint(x: 120, y: 292))
hat.curve(to: NSPoint(x: 286, y: 64), controlPoint1: NSPoint(x: 160, y: 200), controlPoint2: NSPoint(x: 220, y: 110))
hat.curve(to: NSPoint(x: 392, y: 292), controlPoint1: NSPoint(x: 330, y: 140), controlPoint2: NSPoint(x: 372, y: 220))
hat.close()
clay.setFill()
hat.fill()
cream.setFill()
NSBezierPath(roundedRect: NSRect(x: 108, y: 276, width: 296, height: 36), xRadius: 18, yRadius: 18).fill()
circle(286, 64, 20, cream)

NSGraphicsContext.restoreGraphicsState()
guard let png = bitmap.representation(using: .png, properties: [:]) else { exit(1) }
do {
    try png.write(to: URL(fileURLWithPath: arguments[1]))
} catch {
    exit(1)
}
