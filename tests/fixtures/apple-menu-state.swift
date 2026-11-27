import Foundation

@main struct MenuStateProbe {
    static func state(endpoint: Bool = true, choosing: Bool = false, starting: Bool = false,
                      loading: Bool = false, problem: Bool = false, connection: Bool = false,
                      other: Bool = false) -> MacMenuState {
        MacMenuState(hasEndpoint: endpoint, choosingWorkspace: choosing, starting: starting,
                     loading: loading, hasProblem: problem, connectionPresented: connection,
                     otherSheetPresented: other)
    }

    static func main() {
        var intent = MacConnectionIntent()
        let startup = intent.revision
        precondition(state(endpoint: false, starting: true).connectionRoute == .setup)
        intent.requestSetup()
        let stillChoosing = intent.keepSetup(afterStartedAt: startup)
        precondition(stillChoosing)
        precondition(state(choosing: stillChoosing).connectionRoute == .alreadyVisible)
        // Startup also honors a menu request made before its async work starts.
        precondition(intent.keepSetup(afterStartedAt: 0))
        // A later connection initiated from setup must dismiss it normally.
        let userConnection = intent.revision
        precondition(!intent.keepSetup(afterStartedAt: userConnection))
        precondition(state().workspaceAvailable)

        precondition(state().workspaceAvailable)
        for unavailable in [state(endpoint: false), state(choosing: true), state(starting: true),
                            state(loading: true), state(problem: true), state(connection: true),
                            state(other: true)] {
            precondition(!unavailable.workspaceAvailable)
        }
        precondition(state(endpoint: false, starting: true).connectionRoute == .setup)
        precondition(state(endpoint: false).connectionRoute == .alreadyVisible)
        precondition(state(choosing: true).connectionRoute == .alreadyVisible)
        precondition(state(connection: true).connectionRoute == .alreadyVisible)
        precondition(state(problem: true).connectionRoute == .connection)
        precondition(state(starting: true).connectionRoute == .connection)
        precondition(state(other: true).connectionRoute == .unavailable)
        precondition(state(connection: true, other: true).connectionRoute == .unavailable)

        func delivery(_ state: MacMenuState, visible: Bool = true, key: Bool = true,
                      owned: Bool = true, committed: Bool = true, loading: Bool = false) -> Bool {
            MacMenuDelivery.allowed(available: state.workspaceAvailable, visible: visible, keyWindow: key,
                                    ownsOrigin: owned, committed: committed, loading: loading)
        }
        precondition(delivery(state()))
        precondition(!delivery(state(), visible: false))
        precondition(!delivery(state(loading: true)))
        precondition(!delivery(state(problem: true)))
        precondition(!delivery(state(choosing: true)))
        precondition(!delivery(state(), loading: true))
        precondition(!delivery(state(), committed: false))
        precondition(!delivery(state(), key: false))
        precondition(!delivery(state(), owned: false))
    }
}
