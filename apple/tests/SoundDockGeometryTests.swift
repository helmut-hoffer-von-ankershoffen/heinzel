import Foundation

@main struct SoundDockGeometryTests {
    static func main() {
        let payload: [String: NSNumber] = ["width": 256, "viewportWidth": 800]
        precondition(SoundDockGeometry.requestedWidth(payload, viewport: 800) == 256)
        precondition(SoundDockGeometry.requestedWidth(payload, viewport: 390) == nil)
        for bad in [Double.nan, .infinity, -1, 0, 801] {
            precondition(SoundDockGeometry.requestedWidth(["width": NSNumber(value: bad), "viewportWidth": 800], viewport: 800) == nil)
        }
        precondition(SoundDockGeometry.requestedWidth(["width": true, "viewportWidth": 800], viewport: 800) == nil)
        precondition(SoundDockGeometry.width(requested: 256, available: 800) == 256)
        precondition(SoundDockGeometry.width(requested: 900, available: 800) == 720)
        precondition(SoundDockGeometry.width(requested: 50, available: 800) == 200)
        precondition(SoundDockGeometry.width(requested: 256, available: 210) == 210)
        precondition(SoundDockGeometry.width(requested: nil, available: 180) == 180)
        precondition(SoundDockGeometry.aspect(CGSize(width: 1080, height: 1920)) == 0.5625)
        precondition(SoundDockGeometry.aspect(CGSize(width: 0, height: 0)) == 16 / 9)
        precondition(SoundDockGeometry.aspect(CGSize(width: 1920, height: 1080)) == 16 / 9)
        let portrait = SoundDockGeometry.videoSize(width: 448, height: 600, aspect: 9 / 16)
        precondition(portrait.width == 168.75 && portrait.height == 300)
        let wide = SoundDockGeometry.videoSize(width: 448, height: 600, aspect: 16 / 9)
        precondition(wide.width == 448 && wide.height == 252)
        let resized = SoundDockGeometry.videoSize(width: 224, height: 400, aspect: 9 / 16)
        precondition(resized.width == 112.5 && resized.height == 200)
        print("PASS: dock measurement, stale viewport, bounds and selected aspect")
    }
}
