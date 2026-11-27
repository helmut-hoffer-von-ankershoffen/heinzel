import SwiftUI

enum InformationControl: Hashable { case version, back }

extension EnvironmentValues {
    @Entry var workspaceInformationVisible: Bool = false
}

/// Keep the heading visible when the directory outgrows the space between the brand and device checks.
struct WorkspaceStartLayout<Brand: View, Heading: View, Rows: View, Footer: View>: View {
    @ViewBuilder var brand: () -> Brand
    @ViewBuilder var heading: () -> Heading
    @ViewBuilder var rows: () -> Rows
    @ViewBuilder var footer: () -> Footer
    @State private var contentHeight: CGFloat?

    var body: some View {
        VStack(spacing: 0) {
            brand()
                .frame(maxWidth: 440).padding(.horizontal, 16).padding(.vertical, 8)
            VStack(alignment: .leading, spacing: 6) {
                heading().fixedSize(horizontal: false, vertical: true)
                ScrollView {
                    rows()
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { contentHeight = $0 }
                }
                .frame(maxHeight: contentHeight)
                .scrollBounceBehavior(.basedOnSize)
            }
            .frame(maxWidth: 440).padding(.horizontal, 16)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
            footer().frame(maxWidth: .infinity, alignment: .center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct WorkspaceInformationButton: View {
    let version: String?
    let open: () -> Void
    let keyboard: FocusState<InformationControl?>.Binding
    let accessibility: AccessibilityFocusState<InformationControl?>.Binding

    var body: some View {
        Button(action: open) {
            HStack(alignment: .center, spacing: 6) {
                Text(version.map { "v" + $0 } ?? "Version unknown").monospacedDigit()
                Image(systemName: "info.circle")
            }
            .font(.footnote)
            .frame(minWidth: 44, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Information")
        .accessibilityValue(version.map { "App version " + $0 } ?? "App version unknown")
        .help("Information")
        .focused(keyboard, equals: .version)
        .accessibilityFocused(accessibility, equals: .version)
    }
}

/// Keep the directory mounted so Back preserves its scroll position, selection and sheet height.
struct WorkspaceInformationSurface<Directory: View, Details: View>: View {
    let version: String?
    let background: Color
    @ViewBuilder var directory: (WorkspaceInformationButton) -> Directory
    @ViewBuilder var details: () -> Details
    @State private var showsInformation = false
    @FocusState private var keyboard: InformationControl?
    @AccessibilityFocusState private var accessibility: InformationControl?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        directory(WorkspaceInformationButton(version: version, open: { turn(true) },
                                             keyboard: $keyboard, accessibility: $accessibility))
            .modifier(InformationFaceRotation(degrees: showsInformation ? -180 : 0))
            .disabled(showsInformation)
            .allowsHitTesting(!showsInformation)
            .accessibilityHidden(showsInformation)
            .overlay {
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(spacing: 8) {
                            Button { turn(false) } label: {
                                Image(systemName: "chevron.backward")
                                    .frame(width: 44, height: 44).contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Back to workspaces")
                            .help("Back to workspaces")
                            .keyboardShortcut(showsInformation ? .cancelAction : nil)
                            .focused($keyboard, equals: .back)
                            .accessibilityFocused($accessibility, equals: .back)
                            Text("Information").font(.title2.bold()).accessibilityAddTraits(.isHeader)
                        }
                        details().environment(\.workspaceInformationVisible, showsInformation)
                    }
                    .frame(maxWidth: 450, alignment: .leading)
                    .padding(20)
                    .frame(maxWidth: .infinity)
                }
                .background(background)
                .modifier(InformationFaceRotation(degrees: showsInformation ? 0 : 180))
                .disabled(!showsInformation)
                .allowsHitTesting(showsInformation)
                .accessibilityHidden(!showsInformation)
                .accessibilityAction(.escape) { turn(false) }
            }
    }

    private func turn(_ show: Bool) {
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.24), completionCriteria: .removed) {
            showsInformation = show
        } completion: {
            guard showsInformation == show else { return }
            keyboard = show ? .back : .version
            accessibility = show ? .back : .version
        }
    }
}

private struct InformationFaceRotation: AnimatableModifier {
    nonisolated var degrees: Double
    nonisolated var animatableData: Double {
        get { degrees }
        set { degrees = newValue }
    }
    func body(content: Content) -> some View {
        content.rotation3DEffect(.degrees(degrees), axis: (x: 0, y: 1, z: 0), perspective: 0.45)
            .opacity(abs(degrees) < 90 ? 1 : 0)
    }
}
