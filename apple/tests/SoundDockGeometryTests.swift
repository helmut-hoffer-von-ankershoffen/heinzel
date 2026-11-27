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
        print("PASS: dock measurement, stale viewport, bounds and selected aspect")
    }
}
