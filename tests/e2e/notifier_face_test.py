"""Render the production native face without posting, requesting permission or starting a service.

The required serial macOS jobs build the notifier first. A fresh Swift SDK module cache can itself exceed this
harness's ten-second compiler budget, so this native compiler check belongs with those builds, not Python shards.
"""

import plistlib
import shutil
import subprocess
import sys

import pytest

from heinzel.modules.system.notify._macos import SOURCES

pytestmark = pytest.mark.e2e

HARNESS = r"""
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
"""


@pytest.mark.macos_specific
@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit rendering is macOS-only")
@pytest.mark.timeout(15)
def test_bundled_face_renders_in_an_owned_private_directory_without_posting(tmp_path):
    compiler = shutil.which("swiftc")
    assert compiler is not None
    prefix, separator, _delegate = (SOURCES / "main.swift").read_text().partition("final class Notifier:")
    assert separator, "The harness must compile the production helper functions before the application delegate"
    source = tmp_path / "render.swift"
    source.write_text(prefix + HARNESS)
    app = tmp_path / "RenderingProof.app/Contents"
    executable = app / "MacOS/render"
    executable.parent.mkdir(parents=True)
    resources = app / "Resources"
    resources.mkdir()
    shutil.copyfile(SOURCES / "heinzel.svg", resources / "face.svg")
    with (app / "Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleExecutable": "render", "CFBundlePackageType": "APPL"}, handle)
    subprocess.run(
        [compiler, str(source), "-o", str(executable)], check=True, capture_output=True, text=True, timeout=10
    )
    rendered = subprocess.run(
        [str(executable), str(tmp_path / "blue-face.png")],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert rendered.stdout.strip() == ("rendered bundled blue face; private directory removed; no notification posted")
