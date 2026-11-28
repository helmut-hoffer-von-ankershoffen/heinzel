import Foundation

/// Exercises the same range parser used by the workspace scheme handler.
@MainActor
func testApprovedAlbumFilmRanges() throws {
    func check(_ condition: Bool) throws {
        if !condition { throw NSError(domain: "ApprovedAlbumFilmRangeTests", code: 1) }
    }
    try check(AlbumLibrary.byteRange(nil, count: 100) == 0..<100)
    try check(AlbumLibrary.byteRange("bytes=10-19", count: 100) == 10..<20)
    try check(AlbumLibrary.byteRange("bytes=90-", count: 100) == 90..<100)
    try check(AlbumLibrary.byteRange("bytes=-10", count: 100) == 90..<100)
    try check(AlbumLibrary.byteRange("bytes=90-999", count: 100) == 90..<100)
    for value in ["bytes=100-", "bytes=2-1", "bytes=0-1,3-4", "bytes=-0", "bytes=+1-2", "path=/tmp/film"] {
        try check(AlbumLibrary.byteRange(value, count: 100) == nil)
    }
}
