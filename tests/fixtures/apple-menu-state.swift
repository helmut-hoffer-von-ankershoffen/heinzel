import Foundation

@main struct MenuStateProbe {
    @MainActor private final class AboutReceiver {
        var pending: WorkspaceMenuRequest?
    }

    @MainActor
    private static func aboutFallback() {
        struct EvaluationError: Error {}
        var fallbackCount = 0
        WorkspaceMenuRequest.send(.about) { fallbackCount += 1 }
        precondition(fallbackCount == 1)

        let receiver = AboutReceiver()
        let observer = NotificationCenter.default.addObserver(
            forName: WorkspaceMenuRequest.notification, object: nil, queue: nil
        ) { notification in
            guard let request = notification.object as? WorkspaceMenuRequest else { return }
            MainActor.assumeIsolated {
                precondition(request.action == .about && request.claim())
                precondition(!request.claim())
                receiver.pending = request
            }
        }
        defer { NotificationCenter.default.removeObserver(observer) }

        for (value, error, acknowledged) in [
            (true, nil, true), (false, nil, false), (nil, nil, false), (true, EvaluationError(), false),
        ] as [(Bool?, Error?, Bool)] {
            let before = fallbackCount
            receiver.pending = nil
            WorkspaceMenuRequest.send(.about) { fallbackCount += 1 }
            precondition(fallbackCount == before)
            guard let request = receiver.pending else { preconditionFailure("About request was not claimed") }
            request.complete(value, error: error)
            request.complete(nil, error: EvaluationError())
            precondition(fallbackCount == before + (acknowledged ? 0 : 1))
        }
    }

    static func state(endpoint: Bool = true, choosing: Bool = false, starting: Bool = false,
                      loading: Bool = false, problem: Bool = false, connection: Bool = false,
                      other: Bool = false) -> MacMenuState {
        MacMenuState(hasEndpoint: endpoint, choosingWorkspace: choosing, starting: starting,
                     loading: loading, hasProblem: problem, connectionPresented: connection,
                     otherSheetPresented: other)
    }

    @MainActor static func main() {
        aboutFallback()
        precondition(!MacWorkspaceSwitchState.protectsEditor(link: "", focused: false),
                     "empty startup must allow status-menu connections")
        precondition(MacWorkspaceSwitchState.protectsEditor(link: "", focused: true),
                     "a focused editor must not be displaced")
        precondition(MacWorkspaceSwitchState.protectsEditor(link: "https://draft.example/#sign-in", focused: false),
                     "a draft remains protected after focus leaves the field")
        var active = MacWorkspaceSwitchState(choosingWorkspace: false, connectionPresented: false, hasEndpoint: true)
        active.localStartFailed()
        precondition(!active.choosingWorkspace && active.connectionPresented,
                     "failed local startup keeps the active workspace and exposes the error")
        var setup = MacWorkspaceSwitchState(choosingWorkspace: true, connectionPresented: false, hasEndpoint: true)
        setup.localStartFailed()
        precondition(setup.choosingWorkspace && !setup.connectionPresented,
                     "an existing chooser keeps its inline startup error")
        var empty = MacWorkspaceSwitchState(choosingWorkspace: false, connectionPresented: false, hasEndpoint: false)
        empty.localStartFailed()
        precondition(!empty.connectionPresented, "empty startup cannot present a sheet requiring an endpoint")
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
