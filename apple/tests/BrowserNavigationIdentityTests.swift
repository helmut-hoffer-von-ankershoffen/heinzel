import Foundation

@main
struct BrowserNavigationIdentityTests {
    static func main() {
        let oldURL = URL(string: "https://old.example/")!
        let newURL = URL(string: "https://new.example/")!
        var identity = NavigationIdentity<String>()

        let oldAttempt = identity.begin(target: oldURL)
        identity.attach("old", to: oldAttempt)
        precondition(identity.start("old", url: oldURL))
        let newAttempt = identity.begin(target: newURL)
        identity.attach("new", to: newAttempt)
        precondition(!identity.start("old", url: oldURL), "a delayed start from the replaced page is ignored")
        precondition(identity.start("new", url: newURL))
        precondition(!identity.finish("old", url: oldURL), "a replaced page cannot finish the current navigation")
        precondition(identity.finish("new", url: newURL))

        let pageAttempt = identity.begin(target: oldURL)
        identity.attach(nil, to: pageAttempt)
        precondition(identity.start("page-navigation", url: oldURL), "ordinary page-initiated navigation remains current")
        precondition(identity.finish("page-navigation", url: oldURL))

        let cachedAttempt = identity.begin(target: oldURL)
        identity.attach(nil, to: cachedAttempt)
        precondition(identity.start(nil, url: oldURL), "back-forward-cache starts can use the current attempt URL")
        precondition(identity.finish(nil, url: oldURL), "the current nil-identity navigation can finish")

        let refusedAttempt = identity.begin(target: newURL)
        identity.cancel(refusedAttempt)
        precondition(!identity.start(nil, url: newURL), "a nil reload result cannot later look like success")
        print("Browser navigation identity passed")
    }
}
