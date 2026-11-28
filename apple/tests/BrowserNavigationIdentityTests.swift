import Foundation

@main
struct BrowserNavigationIdentityTests {
    final class Token {}

    static func rapidReloadKeepsTheNewestToken(at url: URL) {
        var identity = NavigationIdentity<Token>()
        let firstReload = Token()
        let firstAttempt = identity.begin(target: url)
        identity.attach(firstReload, to: firstAttempt)
        precondition(identity.start(firstReload, url: url))

        let secondReload = Token()
        let secondAttempt = identity.begin(target: url)
        identity.attach(secondReload, to: secondAttempt)
        let foreignNavigation = Token()
        precondition(!identity.start(foreignNavigation, url: url), "the same URL does not make a foreign start current")
        precondition(!identity.finish(firstReload, url: url), "the previous reload cannot settle the next one")
        precondition(identity.start(secondReload, url: url))
        precondition(!identity.finish(foreignNavigation, url: url), "only the newest reload token can settle the attempt")
        precondition(identity.finish(secondReload, url: url))
    }

    static func main() {
        let oldURL = URL(string: "https://old.example/")!
        let newURL = URL(string: "https://new.example/")!
        rapidReloadKeepsTheNewestToken(at: newURL)
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
        let cachedNavigation = Token()
        identity.attach(cachedNavigation, to: cachedAttempt)
        precondition(identity.start(nil, url: oldURL), "back-forward-cache starts can use the attached history attempt URL")
        precondition(identity.finish(nil, url: oldURL), "the current nil-identity history navigation can finish")

        let sameDocumentAttempt = identity.begin(target: newURL)
        let sameDocumentNavigation = Token()
        identity.attach(sameDocumentNavigation, to: sameDocumentAttempt)
        precondition(!identity.finishSameDocument(url: newURL, attempt: sameDocumentAttempt, isLoading: true),
                     "the URL notification while WebKit is loading does not settle history")
        precondition(!identity.finishSameDocument(url: oldURL, attempt: sameDocumentAttempt, isLoading: false),
                     "a stale same-document history URL cannot finish the current attempt")
        precondition(identity.finishSameDocument(url: newURL, attempt: sameDocumentAttempt, isLoading: false),
                     "the later isLoading-only change settles the same history attempt")

        let staleSameDocumentAttempt = identity.begin(target: newURL)
        _ = identity.begin(target: newURL)
        precondition(!identity.finishSameDocument(url: newURL, attempt: staleSameDocumentAttempt, isLoading: false),
                     "a late KVO callback cannot settle a newer attempt at the same URL")

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
