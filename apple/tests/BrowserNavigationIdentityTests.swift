import Foundation

@main
struct BrowserNavigationIdentityTests {
    final class Token {}

    static func main() {
        let oldURL = URL(string: "https://old.example/")!
        let newURL = URL(string: "https://new.example/")!
        var identity = NavigationIdentity<Token>()

        let oldAttempt = identity.begin(target: oldURL)
        let old = Token()
        identity.attach(old, to: oldAttempt)
        precondition(identity.start(old, url: oldURL))
        let newAttempt = identity.begin(target: newURL)
        let new = Token()
        identity.attach(new, to: newAttempt)
        precondition(!identity.start(old, url: oldURL), "a delayed start from the replaced page is ignored")
        precondition(!identity.start(Token(), url: newURL), "an unrelated provisional start cannot displace the action navigation")
        precondition(identity.start(new, url: newURL))
        precondition(!identity.finish(old, url: oldURL), "a replaced page cannot finish the current navigation")
        precondition(identity.finish(new, url: newURL))

        let pageAttempt = identity.begin(target: oldURL)
        identity.attach(nil, to: pageAttempt)
        let pageNavigation = Token()
        precondition(identity.start(pageNavigation, url: oldURL), "ordinary page-initiated navigation remains current")
        precondition(identity.finish(pageNavigation, url: oldURL))

        let cachedAttempt = identity.begin(target: oldURL)
        identity.attach(nil, to: cachedAttempt)
        precondition(identity.start(nil, url: oldURL), "back-forward-cache starts can use the current attempt URL")
        precondition(identity.finish(nil, url: oldURL), "the current nil-identity navigation can finish")

        var retired = NavigationIdentity<Token>()
        let retiredAttempt = retired.begin(target: oldURL)
        var retiredToken: Token? = Token()
        retired.attach(retiredToken, to: retiredAttempt)
        precondition(retired.start(retiredToken, url: oldURL))
        let nextAttempt = retired.begin(target: newURL)
        retired.attach(nil, to: nextAttempt)
        retiredToken = nil
        precondition(retired.start(Token(), url: newURL), "released retired identities are pruned and cannot poison later navigation")

        let refusedAttempt = identity.begin(target: newURL)
        identity.cancel(refusedAttempt)
        precondition(!identity.start(nil, url: newURL), "a nil reload result cannot later look like success")
        print("Browser navigation identity passed")
    }
}
