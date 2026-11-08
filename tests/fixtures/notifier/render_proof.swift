// The face-rendering proof that tests/e2e/notifier_face_test.py runs. `make notifier` compiles it after the
// production helpers of main.swift (everything before the application delegate), so the timed test compiles nothing.
precondition(identifier("notice-1") == "notice-1")
precondition(identifier("../../other") != "../../other")
precondition(appURL("http://127.0.0.1:18737/heinzel?chat=example&event=notice-1") != nil)
precondition(appURL("https://images.example.test/face.svg") == nil)
precondition(face(hat: "https://images.example.test/face.svg") == nil)
guard let proof = face(hat: "blue") else { fatalError("bundled face did not render") }
let attributes = try FileManager.default.attributesOfItem(atPath: proof.directory.path)
precondition((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o700)
let imageData = try Data(contentsOf: proof.attachment.url)
guard let bitmap = NSBitmapImageRep(data: imageData) else { fatalError("invalid attachment") }
precondition(bitmap.pixelsWide == 192 && bitmap.pixelsHigh == 192)
var bluePixels = 0
for y in 0..<bitmap.pixelsHigh {
    for x in 0..<bitmap.pixelsWide {
        if let colour = bitmap.colorAt(x: x, y: y),
            colour.alphaComponent > 0.95,
            abs(colour.redComponent - 30.0 / 255) < 0.03,
            abs(colour.greenComponent - 136.0 / 255) < 0.03,
            abs(colour.blueComponent - 229.0 / 255) < 0.03
        { bluePixels += 1 }
    }
}
precondition(bluePixels > 100)
try Data(contentsOf: proof.attachment.url).write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
try FileManager.default.removeItem(at: proof.directory)
precondition(!FileManager.default.fileExists(atPath: proof.directory.path))
print("rendered bundled blue face; private directory removed; no notification posted")
