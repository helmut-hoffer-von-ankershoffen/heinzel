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
    var body: some View {
        WidgetLauncher(title: "Write a prompt", detail: "Choose a Heinzel, then write and review.",
                       symbol: "square.and.pencil")
            .paperWidget()
            .accessibilityHint("Opens the app. Nothing is sent until you review and send your prompt.")
    }
}

/// Launchers explain the action instead of repeating the app's name and logo.
private struct WidgetLauncher: View {
    let title: LocalizedStringKey
    let detail: LocalizedStringKey
    let symbol: String

    var body: some View {
        ViewThatFits(in: .vertical) {
            content(showDetail: true)
            content(showDetail: false)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private func content(showDetail: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: symbol)
                .font(.title2)
                .widgetAccentable()
                .accessibilityHidden(true)
            Text(title).font(.headline).widgetAccentable()
            if showDetail {
                Text(detail).font(.subheadline)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }
}

private struct ShortcutEntry: TimelineEntry {
    let date: Date
}

private struct ShortcutProvider: TimelineProvider {
    func placeholder(in _: Context) -> ShortcutEntry { ShortcutEntry(date: .now) }

    func getSnapshot(in _: Context, completion: @escaping (ShortcutEntry) -> Void) {
        completion(ShortcutEntry(date: .now))
    }

    func getTimeline(in _: Context, completion: @escaping (Timeline<ShortcutEntry>) -> Void) {
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

    @ViewBuilder
    private var homeWidget: some View {
        if family == .systemMedium {
            VStack(spacing: 8) {
                action("Open workspace", symbol: workspaceSymbol, route: .workspace)
                action("Connection help", symbol: helpSymbol, route: .connectionHelp)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        } else {
            WidgetLauncher(title: "Open workspace", detail: "Your Heinzels and conversations", symbol: workspaceSymbol)
        }
    }

    private func action(_ name: LocalizedStringKey, symbol: String, route: NativeRoute) -> some View {
        Link(destination: route.url) {
            HStack(spacing: 10) {
                Image(systemName: symbol).accessibilityHidden(true)
                Text(name)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.caption).accessibilityHidden(true)
            }
            .font(.headline)
            .lineLimit(2)
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .padding(.horizontal, 12)
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
    func placeholder(in _: Context) -> ActivityEntry {
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

    func getTimeline(in _: Context, completion: @escaping (Timeline<ActivityEntry>) -> Void) {
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
        .description("See Heinzels needing attention first, with statuses and the snapshot age.")
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

    @ViewBuilder
    private var activity: some View {
        if let snapshot = entry.activity, !snapshot.roster.isEmpty {
            HeinzelRosterView(entry: entry)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                if entry.fresh, let snapshot = entry.activity {
                    Text("\(snapshot.awake) awake")
                        .font(.title2.weight(.semibold))
                        .widgetAccentable()
                    if snapshot.waiting > 0 {
                        Label("\(snapshot.waiting) need attention", systemImage: "bell.badge")
                            .font(.subheadline)
                    }
                    Spacer(minLength: 0)
                    SnapshotTime(updatedAt: snapshot.updatedAt, fresh: true)
                } else {
                    WidgetRefresh(updatedAt: entry.activity?.updatedAt)
                }
            }
            .privacySensitive()
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        }
    }
}

private struct HeinzelChoice: AppEntity {
    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Heinzel"
    static let defaultQuery = HeinzelChoiceQuery()

    let id: String
    let name: String

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(name)", subtitle: "\(String(id.suffix(29)))")
    }
}

private struct HeinzelChoiceQuery: EntityStringQuery {
    func entities(for identifiers: [String]) async throws -> [HeinzelChoice] {
        guard let snapshot = ActivityWidgetData.read(), let scope = snapshot.workspaceScope, let generation = snapshot.generation else { return [] }
        return identifiers.prefix(50).compactMap { identifier in
            let prefix = scope + "/"
            guard identifier.hasPrefix(prefix), let item = ActivityWidgetData.find(scope: scope, generation: generation, id: String(identifier.dropFirst(prefix.count))) else { return nil }
            return HeinzelChoice(id: identifier, name: item.name)
        }
    }

    func suggestedEntities() async throws -> [HeinzelChoice] {
        guard let snapshot = ActivityWidgetData.read(), let scope = snapshot.workspaceScope, let generation = snapshot.generation else { return [] }
        return ActivityWidgetData.search(scope: scope, generation: generation).map { HeinzelChoice(id: scope + "/" + $0.id, name: $0.name) }
    }

    func entities(matching string: String) async throws -> [HeinzelChoice] {
        guard let snapshot = ActivityWidgetData.read(), let scope = snapshot.workspaceScope, let generation = snapshot.generation else { return [] }
        return ActivityWidgetData.search(scope: scope, generation: generation, query: string).map { HeinzelChoice(id: scope + "/" + $0.id, name: $0.name) }
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
    func placeholder(in _: Context) -> PinnedHeinzelEntry {
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

    func timeline(for configuration: SelectHeinzelIntent, in _: Context) async -> Timeline<PinnedHeinzelEntry> {
        let snapshot = ActivityWidgetData.read()
        let date = Date.now
        let current = entry(for: configuration, from: snapshot, at: date)
        if let scope = snapshot?.workspaceScope, let identifier = configuration.heinzel?.id,
           identifier.hasPrefix(scope + "/") {
            ActivityWidgetData.requestDetails(for: String(identifier.dropFirst(scope.count + 1)), scope: scope)
        }
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
        let item = scope.flatMap { scope -> WidgetHeinzel? in
            guard let identifier = configuration.heinzel?.id, identifier.hasPrefix(scope + "/") else { return nil }
            let id = String(identifier.dropFirst(scope.count + 1))
            if let rich = snapshot?.roster.first(where: { $0.id == id }) { return rich }
            guard let generation = snapshot?.generation, let entry = ActivityWidgetData.find(scope: scope, generation: generation, id: id) else { return nil }
            return entry.widgetHeinzel
        }
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
                WidgetPlaceholder(title: "Heinzel status", loading: entry.loading)
            } else if let item = entry.heinzel {
                ViewThatFits(in: .vertical) {
                    status(item, detailLimit: 2)
                    status(item, detailLimit: 1)
                    status(item, detailLimit: 0)
                }
                .privacySensitive()
            } else {
                WidgetLauncher(title: entry.configured ? "Heinzel unavailable" : "Choose a Heinzel",
                               detail: entry.configured ? "Open the picker" : "Edit this widget to pin a Heinzel",
                               symbol: "person.crop.circle.badge.questionmark")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityHint(entry.workspaceScope == nil || entry.heinzel == nil
                           ? "Opens the Heinzel picker" : "Opens this Heinzel’s chat")
    }

    private func status(_ item: WidgetHeinzel, detailLimit: Int) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                HeinzelPortrait(item: item, size: family == .systemMedium ? 40 : 30)
                Text(item.name).font(.headline).lineLimit(2).widgetAccentable()
            }
            HeinzelStatus(item: item, font: .subheadline.weight(.medium))
                .lineLimit(2)
                .accessibilityLabel(entry.fresh ? item.status : "Last known: " + item.status)
            if detailLimit > 0, entry.fresh {
                details(item, limit: detailLimit)
            }
            if let updatedAt = entry.updatedAt {
                SnapshotTime(updatedAt: updatedAt, fresh: entry.fresh)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private func details(_ item: WidgetHeinzel, limit: Int) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if let asks = item.asks, asks > 0 {
                Label(asks == 1 ? "1 open ask" : "\(asks) open asks", systemImage: "questionmark.bubble")
            }
            if let wake = entry.nextWake, (family == .systemMedium && limit > 1) || (item.asks ?? 0) == 0 {
                if Calendar.current.isDate(wake, inSameDayAs: entry.date) {
                    Label("Scheduled \(wake, style: .time)", systemImage: "clock")
                } else {
                    Label("Scheduled \(wake.formatted(.dateTime.weekday(.abbreviated).hour().minute()))",
                          systemImage: "clock")
                }
            } else if family == .systemMedium, limit > 1, let approved = item.lastApproved, approved <= entry.date.timeIntervalSince1970 {
                Text("Approved \(Date(timeIntervalSince1970: approved), style: .relative) ago")
            }
        }
        .font(.subheadline)
        .lineLimit(1)
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
            WidgetLauncher(title: "Choose a Heinzel", detail: "Open a conversation", symbol: "bubble.left.and.bubble.right")
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
        .description("See your Heinzels’ last reported statuses and snapshot age. Tap a name to open that chat.")
        .supportedFamilies([.systemMedium, .systemLarge])
    }
}

private struct HeinzelRosterView: View {
    let entry: ActivityEntry
    @Environment(\.widgetFamily) private var family
    @Environment(\.redactionReasons) private var redaction

    // Keep the server's order within each priority, so snapshots do not shuffle peers.
    private var items: [WidgetHeinzel] {
        (entry.activity?.roster ?? []).enumerated().sorted {
            if $0.element.widgetPriority != $1.element.widgetPriority {
                return $0.element.widgetPriority < $1.element.widgetPriority
            }
            return $0.offset < $1.offset
        }.map(\.element)
    }

    var body: some View {
        Group {
            if entry.loading || !redaction.isEmpty {
                Text(entry.loading ? "Loading workspace" : "Open to view Heinzels")
                    .font(.headline)
                    .unredacted()
            } else {
                ViewThatFits(in: .vertical) {
                    roster(limit: family == .systemLarge ? 6 : (family == .systemSmall ? 1 : 3))
                    roster(limit: family == .systemLarge ? 4 : 2)
                    roster(limit: 1)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Heinzels")
    }

    private func roster(limit: Int) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let snapshot = entry.activity, !items.isEmpty {
                ForEach(items.prefix(limit)) { item in
                    row(item, scope: snapshot.workspaceScope)
                }
                HStack(alignment: .bottom, spacing: 8) {
                    SnapshotTime(updatedAt: snapshot.updatedAt, fresh: entry.fresh)
                    Spacer(minLength: 0)
                    if items.count > limit {
                        Link(destination: NativeRoute.chooseHeinzel.url) {
                            Text("+\(items.count - limit)")
                                .font(.caption.weight(.semibold))
                        }
                        .accessibilityLabel("View all \(items.count) Heinzels")
                    }
                }
                .privacySensitive()
            } else if entry.fresh, entry.activity?.heinzels != nil {
                Text("No Heinzels yet").font(.subheadline)
            } else {
                WidgetRefresh(updatedAt: entry.activity?.updatedAt)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private func row(_ item: WidgetHeinzel, scope: String?) -> some View {
        Link(destination: scope.map { NativeRoute.chat(workspace: $0, id: item.id).url }
             ?? NativeRoute.chooseHeinzel.url) {
            HStack(spacing: 10) {
                HeinzelPortrait(item: item, size: family == .systemSmall ? 30 : 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name).font(.headline).lineLimit(1)
                    HeinzelStatus(item: item, font: .subheadline)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .contentShape(Rectangle())
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(item.name), \(entry.fresh ? item.status : "last known: " + item.status)")
            .accessibilityHint(scope == nil ? "Opens the Heinzel picker" : "Opens this Heinzel’s chat")
        }
        .privacySensitive()
    }
}

private struct SnapshotTime: View {
    let updatedAt: Date
    let fresh: Bool

    var body: some View {
        Text("\(fresh ? "Updated" : "Last known") \(updatedAt, style: .relative) ago")
            .font(.caption)
            .lineLimit(2)
            .accessibilityHint(fresh ? "Snapshot from the workspace" : "Open the app to refresh these statuses")
    }
}

private struct WidgetPlaceholder: View {
    let title: LocalizedStringKey
    let loading: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
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
