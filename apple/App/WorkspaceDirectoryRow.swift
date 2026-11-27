import SwiftUI

struct WorkspaceDirectoryRow: View {
    enum Action { case disconnect, remove }

    enum Session {
        case other, connected, needsRecovery

        init(origin: URL?, retained: URL?, confirmed: URL?) {
            guard let origin, retained == origin else { self = .other; return }
            self = confirmed == origin ? .connected : .needsRecovery
        }
    }

    let name: String
    let detail: String
    let version: String?
    let symbol: String
    let session: Session
    private var current: Bool { session == .connected }
    var address: String? = nil
    var available = true
    var localBackend = false
    let userAdded: Bool
    let busy: Bool
    var recoveryControl: (paused: Bool, toggle: () -> Void)? = nil
    let open: () -> Void
    let perform: (Action) -> Void
    @Environment(\.dynamicTypeSize) private var typeSize

    var action: Action? {
        if session != .other { return .disconnect }
        return userAdded ? .remove : nil
    }

    private var nameLabel: some View {
        Text(name).font(.headline)
    }

    private var versionLabel: some View {
        Text(version.map { "v" + $0 } ?? "Version unknown")
            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
    }

    private var accessibleName: String {
        guard let address else { return name }
        return name + ", " + address
    }

    private var openButton: some View {
        Button(action: open) {
            HStack(spacing: 8) {
                Image(systemName: current ? "checkmark.circle.fill" : symbol)
                    .frame(width: 22).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    ViewThatFits(in: .horizontal) {
                        if !typeSize.isAccessibilitySize {
                            HStack(alignment: .firstTextBaseline, spacing: 6) {
                                nameLabel
                                versionLabel
                            }
                            .fixedSize(horizontal: true, vertical: false)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            nameLabel
                            versionLabel
                        }
                    }
                    Text((current ? "Current · " : "") + detail).font(.subheadline).foregroundStyle(.secondary)
                    if let address {
                        Text(verbatim: address).font(.caption.monospaced()).foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibleName)
        .accessibilityValue((current ? "Current workspace. " : "") + detail + ". " +
                            (version.map { "Last reported server version " + $0 } ?? "Server version unknown"))
        .accessibilityAddTraits(current ? .isSelected : [])
    }

    private func actionButton(_ action: Action) -> some View {
        let disconnect = action == .disconnect
        let label = (disconnect ? "Disconnect from " : "Remove ") + accessibleName
        return Button { perform(action) } label: {
            Image(systemName: disconnect ? "rectangle.portrait.and.arrow.right" : "minus.circle")
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .accessibilityHint(disconnect && localBackend ? "Leaves the local backend running." : "")
        .help(disconnect && localBackend ? "Leave workspace. The local backend keeps running." : label)
        .disabled(action == .remove && busy)
    }

    var body: some View {
        HStack(spacing: 8) {
            openButton.disabled(!current && !available)
            if let recoveryControl {
                let label = (recoveryControl.paused ? "Resume reconnecting to " : "Pause reconnecting to ") + accessibleName
                Button(action: recoveryControl.toggle) {
                    Image(systemName: recoveryControl.paused ? "play" : "pause")
                        .frame(width: 44, height: 44).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(label)
                .help(label)
            } else if let action { actionButton(action) }
        }
        .padding(.leading, 12).padding(.trailing, 8).padding(.vertical, 4)
    }
}
