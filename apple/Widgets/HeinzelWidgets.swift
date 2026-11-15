import SwiftUI
import WidgetKit

@main
struct HeinzelWidgets: WidgetBundle {
    var body: some Widget {
        WorkspaceWidget()
        ConnectionHelpWidget()
    }
}

private struct ShortcutEntry: TimelineEntry {
    let date: Date
}

private struct ShortcutProvider: TimelineProvider {
    func placeholder(in context: Context) -> ShortcutEntry { ShortcutEntry(date: .now) }

    func getSnapshot(in context: Context, completion: @escaping (ShortcutEntry) -> Void) {
        completion(ShortcutEntry(date: .now))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<ShortcutEntry>) -> Void) {
        // These widgets are launchers. Their artwork never depends on a workspace connection.
        completion(Timeline(entries: [ShortcutEntry(date: .now)], policy: .never))
    }
}

private let workspaceSymbol = "point.3.connected.trianglepath.dotted"
private let helpSymbol = "questionmark.circle"

private struct WorkspaceWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "HeinzelWorkspace", provider: ShortcutProvider()) { _ in
            WorkspaceWidgetView()
                .widgetURL(NativeRoute.workspace.url)
        }
        .configurationDisplayName("Workspace")
        .description("Open your Heinzel workspace from your Home Screen or Lock Screen.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryInline])
    }
}

private struct WorkspaceWidgetView: View {
    @Environment(\.widgetFamily) private var family
    @Environment(\.widgetRenderingMode) private var renderingMode

    var body: some View {
        switch family {
        case .accessoryCircular:
            ZStack {
                AccessoryWidgetBackground()
                Image(systemName: workspaceSymbol)
                    .font(.system(size: 23, weight: .semibold))
                    .widgetAccentable()
            }
            .accessibilityLabel("Open Heinzel workspace")
            .containerBackground(for: .widget) { Color.clear }
        case .accessoryInline:
            Label("Heinzel workspace", systemImage: workspaceSymbol)
                .containerBackground(for: .widget) { Color.clear }
        default:
            homeWidget
                .containerBackground(for: .widget) {
                    LinearGradient(colors: [Color(red: 0.99, green: 0.98, blue: 0.95),
                                            Color(red: 0.92, green: 0.88, blue: 0.80)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing)
                }
        }
    }

    private var homeWidget: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Image("HeinzelCrew")
                    .resizable()
                    .widgetAccentedRenderingMode(.accentedDesaturated)
                    .scaledToFit()
                    .frame(width: 50, height: 48)
                    .accessibilityHidden(true)
                Text("Heinzel")
                    .font(.system(.title2, design: .serif, weight: .bold))
                    .widgetAccentable()
                if family == .systemSmall {
                    Label("Open workspace", systemImage: "arrow.up.right")
                        .font(.caption.weight(.medium))
                }
            }
            .lineLimit(1)
            .minimumScaleFactor(0.75)
            if family == .systemMedium {
                Spacer(minLength: 0)
                VStack(spacing: 8) {
                    action("Workspace", symbol: workspaceSymbol, route: .workspace)
                    action("Connection Help", symbol: helpSymbol, route: .connectionHelp)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .foregroundStyle(renderingMode == .fullColor ? Color(red: 0.15, green: 0.23, blue: 0.20) : Color.primary)
    }

    private func action(_ name: LocalizedStringKey, symbol: String, route: NativeRoute) -> some View {
        Link(destination: route.url) {
            Label(name, systemImage: symbol)
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)
                .minimumScaleFactor(0.75)
                .frame(maxWidth: .infinity, minHeight: 38, alignment: .leading)
                .padding(.horizontal, 10)
                .background(.primary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        }
    }
}

private struct ConnectionHelpWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "HeinzelConnectionHelp", provider: ShortcutProvider()) { _ in
            VStack(alignment: .leading, spacing: 3) {
                Label("Heinzel", systemImage: helpSymbol)
                    .font(.headline)
                    .widgetAccentable()
                Text("Connection Help")
                    .font(.caption)
            }
            .lineLimit(1)
            .minimumScaleFactor(0.75)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .combine)
            .widgetURL(NativeRoute.connectionHelp.url)
            .containerBackground(for: .widget) { Color.clear }
        }
        .configurationDisplayName("Connection Help")
        .description("Open Heinzel’s connection checks and help from your Lock Screen.")
        .supportedFamilies([.accessoryRectangular])
    }
}
