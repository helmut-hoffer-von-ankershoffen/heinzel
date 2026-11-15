import AppIntents
import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif
import WidgetKit

@main
struct HeinzelWidgets: WidgetBundle {
    var body: some Widget {
        WorkspaceWidget()
        #if os(iOS)
        ConnectionHelpWidget()
        #endif
        ActivityWidget()
        PinnedHeinzelWidget()
        HeinzelChooserWidget()
        HeinzelRosterWidget()
        WritePromptWidget()
    }
}

private struct WritePromptWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "HeinzelWritePrompt", provider: ShortcutProvider()) { _ in
            WritePromptWidgetView()
                .widgetURL(NativeRoute.writePrompt.url)
        }
        .configurationDisplayName("Write a Prompt")
        .description("Choose a Heinzel and write a prompt in the app. You review it before sending.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

private struct WritePromptWidgetView: View {
    @Environment(\.widgetFamily) private var family
    @Environment(\.widgetRenderingMode) private var renderingMode
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: "square.and.pencil")
                .font(.system(size: 25, weight: .medium))
                .widgetAccentable()
            Spacer(minLength: 2)
            Text("Write a Prompt")
                .font(.system(.headline, design: .serif))
            Text(family == .systemMedium ? "Choose a Heinzel, write, then send." : "Choose a Heinzel")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Spacer()
                Image(systemName: "arrow.up.right").font(.caption.weight(.semibold))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .foregroundStyle(renderingMode == .fullColor ?
            (colorScheme == .dark ? Color(red: 0.94, green: 0.93, blue: 0.87) : Color(red: 0.15, green: 0.23, blue: 0.20)) : Color.primary)
        .containerBackground(for: .widget) {
            colorScheme == .dark ? Color(red: 0.08, green: 0.14, blue: 0.12) : Color(red: 0.97, green: 0.95, blue: 0.90)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Write a prompt. Opens Heinzel to choose a recipient and review before sending.")
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
        .supportedFamilies(families)
    }

    private var families: [WidgetFamily] {
        #if os(macOS)
        [.systemSmall, .systemMedium]
        #else
        [.systemSmall, .systemMedium, .accessoryCircular, .accessoryInline]
        #endif
    }
}

private struct WorkspaceWidgetView: View {
    @Environment(\.widgetFamily) private var family

    var body: some View {
        #if os(macOS)
        homeWidget.paperWidget()
        #else
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
                .paperWidget()
        }
        #endif
    }

    private var homeWidget: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                CrewMark(width: 50, height: 48)
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

#if os(iOS)
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

#endif

private struct ActivityEntry: TimelineEntry {
    let date: Date
    let activity: ActivityWidgetData?
    var fresh = true
    var loading = false
}

private struct ActivityProvider: TimelineProvider {
    func placeholder(in context: Context) -> ActivityEntry {
        ActivityEntry(date: .now, activity: nil, fresh: false, loading: true)
    }

    func getSnapshot(in context: Context, completion: @escaping (ActivityEntry) -> Void) {
        let date = Date.now
        if context.isPreview {
            let example = ActivityWidgetData(awake: 2, waiting: 1, updatedAt: date, heinzels: previewHeinzels(at: date))
            completion(ActivityEntry(date: date, activity: example))
        } else {
            completion(current(at: date))
        }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<ActivityEntry>) -> Void) {
        let entry = current(at: .now)
        var entries = [entry]
        if let activity = entry.activity, activity.expiresAt > entry.date {
            entries.append(ActivityEntry(date: activity.expiresAt, activity: activity, fresh: false))
        }
        completion(Timeline(entries: entries, policy: .never))
    }

    private func current(at date: Date) -> ActivityEntry {
        let snapshot = ActivityWidgetData.read()
        let fresh = snapshot.map { $0.expiresAt > date } == true
        return ActivityEntry(date: date, activity: snapshot, fresh: fresh)
    }
}

private struct ActivityWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: ActivityWidgetData.kind, provider: ActivityProvider()) { entry in
            ActivityWidgetView(entry: entry)
                .widgetURL(NativeRoute.workspace.url)
                .paperWidget()
        }
        .configurationDisplayName("Active Heinzels")
        .description("See how many Heinzels are awake or waiting for attention in your workspace.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

private struct ActivityWidgetView: View {
    let entry: ActivityEntry
    @Environment(\.redactionReasons) private var redaction

    var body: some View {
        if entry.loading || !redaction.isEmpty {
            WidgetPlaceholder(title: "Active Heinzels", loading: entry.loading)
        } else {
            activity
        }
    }

    private var activity: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Heinzels").font(.system(.headline, design: .serif)).widgetAccentable()
                Spacer(minLength: 4)
                CrewMark(width: 30, height: 27)
            }
            Spacer(minLength: 2)
            if entry.fresh, let activity = entry.activity {
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
                WidgetRefresh(updatedAt: entry.activity?.updatedAt)
            }
        }
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
    let updatedAt: Date?
    let configured: Bool
    var loading = false

    var nextWake: Date? {
        guard fresh, let timestamp = heinzel?.nextWake else { return nil }
        let wake = Date(timeIntervalSince1970: timestamp)
        return wake > date ? wake : nil
    }

    var destination: URL {
        guard let heinzel, let workspaceScope else { return NativeRoute.chooseHeinzel.url }
        return NativeRoute.chat(workspace: workspaceScope, id: heinzel.id).url
    }
}

private struct PinnedHeinzelProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> PinnedHeinzelEntry {
        PinnedHeinzelEntry(date: .now, heinzel: nil, fresh: false, workspaceScope: nil,
                           updatedAt: nil, configured: false, loading: true)
    }

    func snapshot(for configuration: SelectHeinzelIntent, in context: Context) async -> PinnedHeinzelEntry {
        let date = Date.now
        let current = entry(for: configuration, from: ActivityWidgetData.read(), at: date)
        guard context.isPreview, !current.configured else { return current }
        return PinnedHeinzelEntry(date: date, heinzel: previewHeinzels(at: date).first, fresh: true,
                                  workspaceScope: nil, updatedAt: date, configured: true)
    }

    func timeline(for configuration: SelectHeinzelIntent, in context: Context) async -> Timeline<PinnedHeinzelEntry> {
        let snapshot = ActivityWidgetData.read()
        let date = Date.now
        let current = entry(for: configuration, from: snapshot, at: date)
        if let id = current.heinzel?.id { ActivityWidgetData.requestDetails(for: id) }
        var entries = [current]
        if let expiry = snapshot?.expiresAt, expiry > date, current.heinzel != nil {
            if let wake = current.nextWake, wake < expiry {
                entries.append(entry(for: configuration, from: snapshot, at: wake))
            }
            entries.append(entry(for: configuration, from: snapshot, at: expiry))
        }
        return Timeline(entries: entries, policy: .never)
    }

    private func entry(for configuration: SelectHeinzelIntent, from snapshot: ActivityWidgetData?, at date: Date) -> PinnedHeinzelEntry {
        let scope = snapshot?.workspaceScope
        let item = scope.flatMap { scope in snapshot?.roster.first { scope + "/" + $0.id == configuration.heinzel?.id } }
        return PinnedHeinzelEntry(date: date, heinzel: item, fresh: snapshot.map { $0.expiresAt > date } == true,
                                  workspaceScope: scope, updatedAt: snapshot?.updatedAt,
                                  configured: configuration.heinzel != nil)
    }
}

private struct PinnedHeinzelWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: ActivityWidgetData.pinnedKind, intent: SelectHeinzelIntent.self,
                               provider: PinnedHeinzelProvider()) { entry in
            PinnedHeinzelView(entry: entry)
                .widgetURL(entry.destination)
                .paperWidget()
        }
        .configurationDisplayName("Heinzel Status")
        .description("Pick one Heinzel to see its status and key details. Tap to open its chat.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

private struct PinnedHeinzelView: View {
    let entry: PinnedHeinzelEntry
    @Environment(\.widgetFamily) private var family
    @Environment(\.redactionReasons) private var redaction

    var body: some View {
        Group {
            if entry.loading || !redaction.isEmpty {
                WidgetPlaceholder(title: "Heinzel Status", loading: entry.loading)
            } else if let item = entry.heinzel {
                if family == .systemMedium {
                    HStack(spacing: 12) {
                        identity(item, size: 55)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Divider()
                        details(item)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 6) {
                        identity(item, size: 43)
                        details(item)
                    }
                }
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    CrewMark(width: 36, height: 34)
                    Text(entry.configured ? "Heinzel unavailable" : "Choose a Heinzel")
                        .font(.system(.headline, design: .serif))
                    Text(entry.configured ? "Open the picker" : "Touch and hold to edit")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityHint(entry.heinzel == nil ? "Opens the Heinzel picker" : "Opens this Heinzel’s chat")
    }

    private func identity(_ item: WidgetHeinzel, size: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HeinzelPortrait(item: item, size: size)
            Text(item.name)
                .font(.system(.headline, design: .serif))
                .lineLimit(1)
                .widgetAccentable()
        }
        .privacySensitive()
    }

    private func details(_ item: WidgetHeinzel) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if entry.fresh {
                HeinzelStatus(item: item, font: .subheadline.weight(.semibold))
                if let asks = item.asks, family == .systemMedium || asks > 0 || entry.nextWake == nil {
                    Label(asks == 1 ? "1 open ask" : "\(asks) open asks", systemImage: "questionmark.bubble")
                }
                if let wake = entry.nextWake, family == .systemMedium || (item.asks ?? 0) == 0 {
                    if Calendar.current.isDate(wake, inSameDayAs: entry.date) {
                        Label("Scheduled \(wake, style: .time)", systemImage: "clock")
                    } else {
                        Label("Scheduled \(wake.formatted(.dateTime.weekday(.abbreviated).hour().minute()))",
                              systemImage: "clock")
                    }
                } else if family == .systemMedium, let approved = item.lastApproved, approved <= entry.date.timeIntervalSince1970 {
                    Text("Approved \(Date(timeIntervalSince1970: approved), style: .relative) ago")
                }
            } else {
                WidgetRefresh(updatedAt: entry.updatedAt)
            }
        }
        .font(.caption2)
        .privacySensitive()
    }
}

private struct HeinzelStatus: View {
    let item: WidgetHeinzel
    let font: Font

    private var symbol: String {
        if item.state == "retired" { return "archivebox" }
        if item.state == "proposed", item.activity == nil || item.activity == "idle" { return "sparkle" }
        switch item.activity {
        case "working": return "bolt.fill"
        case "waiting_for_you": return "bell.badge"
        case "waiting_on_others": return "hourglass"
        case "proud": return "checkmark.circle"
        case "asleep": return "moon.zzz"
        case "paused": return "pause.circle"
        case "overloaded", "stuck": return "exclamationmark.circle"
        default: return "circle"
        }
    }

    var body: some View {
        Label {
            Text(item.status)
        } icon: {
            Image(systemName: symbol).accessibilityHidden(true)
        }
        .font(font)
    }
}

private struct HeinzelChooserWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "HeinzelChooser", provider: ShortcutProvider()) { _ in
            VStack(alignment: .leading, spacing: 8) {
                CrewMark(width: 54, height: 48)
                Label("Choose a Heinzel", systemImage: "arrow.up.right")
                    .font(.system(.headline, design: .serif))
                    .widgetAccentable()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .widgetURL(NativeRoute.chooseHeinzel.url)
            .paperWidget()
        }
        .configurationDisplayName("Heinzel Picker")
        .description("Choose which Heinzel to chat with each time you open the widget.")
        .supportedFamilies([.systemSmall])
    }
}

private struct HeinzelRosterWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: ActivityWidgetData.rosterKind, provider: ActivityProvider()) { entry in
            HeinzelRosterView(entry: entry)
                .widgetURL(NativeRoute.chooseHeinzel.url)
                .paperWidget()
        }
        .configurationDisplayName("Heinzel Roster")
        .description("See your Heinzels’ current status. Tap a name to open that chat.")
        .supportedFamilies([.systemMedium, .systemLarge])
    }
}

private struct HeinzelRosterView: View {
    let entry: ActivityEntry
    @Environment(\.widgetFamily) private var family
    @Environment(\.redactionReasons) private var redaction

    private var items: [WidgetHeinzel] { entry.activity?.roster ?? [] }

    var body: some View {
        Group {
            if entry.loading || !redaction.isEmpty {
                WidgetPlaceholder(title: "Your Heinzelmännchen", loading: entry.loading)
            } else {
                ViewThatFits(in: .vertical) {
                    roster(limit: family == .systemLarge ? 6 : 3)
                    roster(limit: family == .systemLarge ? 4 : 2)
                    roster(limit: 1)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private func roster(limit: Int) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Your Heinzelmännchen").font(.system(.headline, design: .serif)).widgetAccentable()
                Spacer(minLength: 4)
                if items.count > limit {
                    Link(destination: NativeRoute.chooseHeinzel.url) {
                        Label("View all \(items.count)", systemImage: "arrow.up.right")
                            .font(.caption2.weight(.semibold))
                            .padding(.vertical, 4)
                    }
                    .privacySensitive()
                }
            }

            if let snapshot = entry.activity, !items.isEmpty {
                if !entry.fresh {
                    Text("Updated \(snapshot.updatedAt, style: .relative) ago")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                ForEach(items.prefix(limit)) { item in
                    row(item, scope: snapshot.workspaceScope)
                }
            } else if entry.fresh, entry.activity?.heinzels != nil {
                Text("No Heinzels yet").font(.caption).foregroundStyle(.secondary)
            } else {
                WidgetRefresh(updatedAt: entry.activity?.updatedAt)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private func row(_ item: WidgetHeinzel, scope: String?) -> some View {
        Link(destination: scope.map { NativeRoute.chat(workspace: $0, id: item.id).url }
             ?? NativeRoute.chooseHeinzel.url) {
            HStack(spacing: 8) {
                HeinzelPortrait(item: item, size: 28)
                VStack(alignment: .leading, spacing: 0) {
                    Text(item.name).font(.subheadline.weight(.semibold)).lineLimit(1)
                    if entry.fresh {
                        HeinzelStatus(item: item, font: .caption2)
                            .lineLimit(1)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .frame(maxWidth: .infinity, minHeight: 28, alignment: .leading)
            .contentShape(Rectangle())
            .accessibilityElement(children: .combine)
        }
        .privacySensitive()
    }
}

private struct WidgetPlaceholder: View {
    let title: LocalizedStringKey
    let loading: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            CrewMark(width: 36, height: 34)
            Text(title)
                .font(.system(.headline, design: .serif))
                .widgetAccentable()
            Text(loading ? "Loading workspace" : "Open to view")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        // Only static labels and artwork are visible when WidgetKit redacts content.
        .unredacted()
    }
}

private struct WidgetRefresh: View {
    let updatedAt: Date?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Label("Open to refresh", systemImage: "arrow.up.right")
                .font(.caption.weight(.semibold))
            if let updatedAt {
                Text("Updated \(updatedAt, style: .relative) ago")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct CrewMark: View {
    let width: CGFloat
    let height: CGFloat

    var body: some View {
        Image("HeinzelCrew")
            .resizable()
            .widgetAccentedRenderingMode(.accentedDesaturated)
            .scaledToFit()
            .frame(width: width, height: height)
            .accessibilityHidden(true)
    }
}

private struct HeinzelPortrait: View {
    let item: WidgetHeinzel
    let size: CGFloat

    var body: some View {
        Group {
            if let image = item.portraitImage {
                image.resizable().widgetAccentedRenderingMode(.accentedDesaturated).scaledToFit()
            } else {
                Image(systemName: "person.crop.circle").resizable().scaledToFit().foregroundStyle(.secondary)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

private struct PaperWidget: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.widgetRenderingMode) private var renderingMode

    func body(content: Content) -> some View {
        let dark = colorScheme == .dark
        let paper = dark ? Color(red: 0.10, green: 0.15, blue: 0.13) : Color(red: 0.97, green: 0.95, blue: 0.90)
        let ink = dark ? Color(red: 0.96, green: 0.94, blue: 0.88) : Color(red: 0.15, green: 0.23, blue: 0.20)
        return content
            .foregroundStyle(renderingMode == .fullColor ? ink : Color.primary)
            .containerBackground(for: .widget) { paper }
    }
}

private extension View {
    func paperWidget() -> some View { modifier(PaperWidget()) }
}

private func previewHeinzels(at date: Date) -> [WidgetHeinzel] {
    [
        WidgetHeinzel(id: "mika", name: "Mika", state: "active", activity: "working", asks: 2,
                      lastApproved: nil, nextWake: date.addingTimeInterval(3600).timeIntervalSince1970, portrait: nil),
        WidgetHeinzel(id: "nora", name: "Nora", state: "active", activity: "waiting_for_you", asks: 1,
                      lastApproved: nil, nextWake: nil, portrait: nil),
        WidgetHeinzel(id: "sam", name: "Sam", state: "active", activity: "proud", asks: 0,
                      lastApproved: nil, nextWake: nil, portrait: nil),
    ]
}
