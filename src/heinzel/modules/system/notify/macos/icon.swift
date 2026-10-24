// Draws Heinzel Notifier's icon, 🧝 on a rounded square, as a PNG file: `icon <path> <size>`. Built and run only by
// `make notifier`; `iconutil` turns the images into heinzel.icns.
import AppKit

let arguments = CommandLine.arguments
guard arguments.count == 3, let pixels = Int(arguments[2]), pixels > 0 else {
    FileHandle.standardError.write("usage: icon <path> <size>\n".data(using: .utf8)!)
    exit(64)
}
let size = CGFloat(pixels)
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
NSColor(calibratedRed: 0.96, green: 0.93, blue: 0.86, alpha: 1).setFill()
NSBezierPath(
    roundedRect: NSRect(x: 0, y: 0, width: size, height: size), xRadius: size * 0.22, yRadius: size * 0.22
).fill()
let elf = NSAttributedString(string: "🧝", attributes: [.font: NSFont.systemFont(ofSize: size * 0.72)])
let bounds = elf.size()
elf.draw(at: NSPoint(x: (size - bounds.width) / 2, y: (size - bounds.height) / 2))
image.unlockFocus()
guard let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
    let png = bitmap.representation(using: .png, properties: [:])
else { exit(1) }
do {
    try png.write(to: URL(fileURLWithPath: arguments[1]))
} catch {
    exit(1)
}
