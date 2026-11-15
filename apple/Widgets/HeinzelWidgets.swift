import AppIntents
import SwiftUI
import UIKit
import WidgetKit

@main
struct HeinzelWidgets: WidgetBundle {
    var body: some Widget {
        WorkspaceWidget()
        ConnectionHelpWidget()
        ActivityWidget()
        PinnedHeinzelWidget()
        HeinzelChooserWidget()
        HeinzelRosterWidget()
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

private struct ActivityEntry: TimelineEntry {
    let date: Date
    let activity: ActivityWidgetData?
    var fresh = true
}

private struct ActivityProvider: TimelineProvider {
    var keepsRoster = false

    func placeholder(in context: Context) -> ActivityEntry {
        ActivityEntry(date: .now, activity: ActivityWidgetData(awake: 2, waiting: 1, updatedAt: .now))
    }

    func getSnapshot(in context: Context, completion: @escaping (ActivityEntry) -> Void) {
        completion(context.isPreview ? placeholder(in: context) : current(at: .now))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<ActivityEntry>) -> Void) {
        let entry = current(at: .now)
        var entries = [entry]
        if let activity = entry.activity, activity.expiresAt > entry.date {
            entries.append(ActivityEntry(date: activity.expiresAt, activity: keepsRoster ? activity : nil, fresh: false))
        }
        completion(Timeline(entries: entries, policy: .never))
    }

    private func current(at date: Date) -> ActivityEntry {
        let snapshot = ActivityWidgetData.read()
        let fresh = snapshot.map { $0.expiresAt > date } == true
        return ActivityEntry(date: date, activity: fresh || keepsRoster ? snapshot : nil, fresh: fresh)
    }
}

private struct ActivityWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: ActivityWidgetData.kind, provider: ActivityProvider()) { entry in
            ActivityWidgetView(entry: entry)
                .widgetURL(NativeRoute.workspace.url)
                .containerBackground(for: .widget) {
                    Color(red: 0.97, green: 0.95, blue: 0.90)
                }
        }
        .configurationDisplayName("Active Heinzels")
        .description("See how many Heinzels are awake or waiting for attention in your workspace.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

private struct ActivityWidgetView: View {
    let entry: ActivityEntry
    @Environment(\.widgetRenderingMode) private var renderingMode

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Heinzels").font(.system(.headline, design: .serif)).widgetAccentable()
                Spacer(minLength: 4)
                Image("HeinzelCrew")
                    .resizable()
                    .widgetAccentedRenderingMode(.accentedDesaturated)
                    .scaledToFit()
                    .frame(width: 30, height: 27)
                    .accessibilityHidden(true)
            }
            Spacer(minLength: 2)
            if let activity = entry.activity {
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(activity.awake) awake")
                        .font(.system(.title2, design: .serif, weight: .semibold))
                        .widgetAccentable()
                    if activity.waiting > 0 {
                        Label("Needs attention · \(activity.waiting)", systemImage: "bell.badge")
                            .font(.caption2)
                            .lineLimit(1)
                    }
                    Text("Updated \(activity.updatedAt, style: .relative) ago")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .privacySensitive()
            } else {
                Label("Open to refresh", systemImage: "arrow.up.right")
                    .font(.subheadline.weight(.medium))
            }
        }
        .foregroundStyle(renderingMode == .fullColor ? Color(red: 0.15, green: 0.23, blue: 0.20) : Color.primary)
        .minimumScaleFactor(0.7)
    }
}

private struct HeinzelChoice: AppEntity {
    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Heinzel"
    static let defaultQuery = HeinzelChoiceQuery()

    let id: String
    let name: String

    var displayRepresentation: DisplayRepresentation { DisplayRepresentation(title: "\(name)") }
}

private struct HeinzelChoiceQuery: EntityQuery {
    func entities(for identifiers: [String]) async throws -> [HeinzelChoice] {
        (try await suggestedEntities()).filter { identifiers.contains($0.id) }
    }

    func suggestedEntities() async throws -> [HeinzelChoice] {
        guard let snapshot = ActivityWidgetData.read(), let scope = snapshot.workspaceScope else { return [] }
        return snapshot.roster.map { HeinzelChoice(id: scope + "/" + $0.id, name: $0.name) }
    }
}

private struct SelectHeinzelIntent: WidgetConfigurationIntent {
    static let title: LocalizedStringResource = "Choose a Heinzel"

    @Parameter(title: "Heinzel")
    var heinzel: HeinzelChoice?
}

private struct PinnedHeinzelEntry: TimelineEntry {
    let date: Date
    let heinzel: WidgetHeinzel?
    let fresh: Bool
    let workspaceScope: String?
}

private struct PinnedHeinzelProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> PinnedHeinzelEntry {
        PinnedHeinzelEntry(date: .now, heinzel: nil, fresh: false, workspaceScope: nil)
    }

    func snapshot(for configuration: SelectHeinzelIntent, in context: Context) async -> PinnedHeinzelEntry {
        entry(for: configuration, from: ActivityWidgetData.read(), at: .now)
    }

    func timeline(for configuration: SelectHeinzelIntent, in context: Context) async -> Timeline<PinnedHeinzelEntry> {
        let snapshot = ActivityWidgetData.read()
        let date = Date.now
        let current = entry(for: configuration, from: snapshot, at: date)
        if let id = current.heinzel?.id { ActivityWidgetData.requestDetails(for: id) }
        var entries = [current]
        if let expiry = snapshot?.expiresAt, expiry > date {
            entries.append(entry(for: configuration, from: snapshot, at: expiry))
        }
        return Timeline(entries: entries, policy: .never)
    }

    private func entry(for configuration: SelectHeinzelIntent, from snapshot: ActivityWidgetData?, at date: Date) -> PinnedHeinzelEntry {
        let scope = snapshot?.workspaceScope
        let item = scope.flatMap { scope in snapshot?.roster.first { scope + "/" + $0.id == configuration.heinzel?.id } }
        return PinnedHeinzelEntry(date: date, heinzel: item, fresh: snapshot.map { $0.expiresAt > date } == true,
                                  workspaceScope: scope)
    }
}

private struct PinnedHeinzelWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: ActivityWidgetData.pinnedKind, intent: SelectHeinzelIntent.self,
                               provider: PinnedHeinzelProvider()) { entry in
            VStack(alignment: .leading, spacing: 6) {
                if let item = entry.heinzel {
                    HStack(spacing: 8) {
                        HeinzelPortrait(item: item, size: 34)
                        Text(item.name).font(.system(.headline, design: .serif)).lineLimit(1)
                    }
                    if entry.fresh {
                        Text(item.status).font(.subheadline.weight(.semibold))
                        if let asks = item.asks {
                            Label("Open asks · \(asks)", systemImage: "questionmark.bubble")
                                .font(.caption2)
                        }
                        if let next = item.nextWake {
                            Text("Next wake \(Date(timeIntervalSince1970: next), style: .relative)").font(.caption2)
                        }
                        if let approved = item.lastApproved {
                            Text("Approved \(Date(timeIntervalSince1970: approved), style: .relative) ago").font(.caption2)
                        }
                    } else {
                        Text("Open to refresh").font(.footnote)
                    }
                } else {
                    Label("Choose a Heinzel", systemImage: "person.2")
                        .font(.system(.headline, design: .serif))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .privacySensitive()
            .widgetURL(entry.heinzel.flatMap { item in entry.workspaceScope.map { NativeRoute.chat(workspace: $0, id: item.id).url } }
                       ?? NativeRoute.chooseHeinzel.url)
            .containerBackground(for: .widget) { widgetPaper }
        }
        .configurationDisplayName("Heinzel Status")
        .description("Pick one Heinzel for its status, open asks, and next wake. Tap to open its chat.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

private struct HeinzelChooserWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "HeinzelChooser", provider: ShortcutProvider()) { _ in
            VStack(alignment: .leading, spacing: 8) {
                Image("HeinzelCrew").resizable().scaledToFit().frame(height: 48).accessibilityHidden(true)
                Text("Choose a Heinzel").font(.system(.headline, design: .serif))
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .widgetURL(NativeRoute.chooseHeinzel.url)
            .containerBackground(for: .widget) { widgetPaper }
        }
        .configurationDisplayName("Heinzel Picker")
        .description("Choose which Heinzel to chat with each time you open the widget.")
        .supportedFamilies([.systemSmall])
    }
}

private struct HeinzelRosterWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: ActivityWidgetData.rosterKind, provider: ActivityProvider(keepsRoster: true)) { entry in
            HeinzelRosterView(items: entry.activity?.roster ?? [], workspaceScope: entry.activity?.workspaceScope, fresh: entry.fresh)
                .widgetURL(NativeRoute.chooseHeinzel.url)
                .containerBackground(for: .widget) { widgetPaper }
        }
        .configurationDisplayName("Heinzel Roster")
        .description("See your Heinzels’ current status. Tap a name to open that chat.")
        .supportedFamilies([.systemMedium, .systemLarge])
    }
}

private struct HeinzelRosterView: View {
    let items: [WidgetHeinzel]
    let workspaceScope: String?
    let fresh: Bool
    @Environment(\.widgetFamily) private var family

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("Your Heinzels").font(.system(.headline, design: .serif))
            if items.isEmpty || !fresh { Text("Open to refresh").font(.caption2) }
            ForEach(items.prefix(family == .systemLarge ? 6 : 3)) { item in
                Link(destination: workspaceScope.map { NativeRoute.chat(workspace: $0, id: item.id).url }
                     ?? NativeRoute.chooseHeinzel.url) {
                    HStack(spacing: 7) {
                        HeinzelPortrait(item: item, size: 23)
                        Text(item.name).font(.subheadline.weight(.medium)).lineLimit(1)
                        Spacer(minLength: 2)
                        if fresh { Text(item.status).font(.caption2).lineLimit(1).foregroundStyle(.secondary) }
                    }
                }
                .privacySensitive()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }
}

private struct HeinzelPortrait: View {
    let item: WidgetHeinzel
    let size: CGFloat

    var body: some View {
        Group {
            if let data = item.portrait, let image = UIImage(data: data) {
                Image(uiImage: image).resizable().widgetAccentedRenderingMode(.accentedDesaturated).scaledToFit()
            } else {
                Image(systemName: "person.crop.circle").resizable().scaledToFit().foregroundStyle(.secondary)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

private let widgetPaper = Color(red: 0.97, green: 0.95, blue: 0.90)
