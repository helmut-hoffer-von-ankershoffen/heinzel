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
        SoundscapeWidget()
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
                CrewMark(width: 44, height: 44)
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

private struct ActivityProvider: TimelineProvider, Sendable {
    func placeholder(in _: Context) -> ActivityEntry {
        ActivityEntry(date: .now, activity: nil, fresh: false, loading: true)
    }

    func getSnapshot(in context: Context, completion: @escaping (ActivityEntry) -> Void) {
        let date = Date.now
        if context.isPreview {
            let example = ActivityWidgetData(awake: 2, waiting: 1, updatedAt: date, heinzels: previewHeinzels(at: date))
            completion(ActivityEntry(date: date, activity: example))
        } else if WidgetCredentialStore.enrollmentEnabled {
            completion(ActivityEntry(date: date, activity: nil, fresh: false))
        } else {
            completion(current(at: date))
        }
    }

    func getTimeline(in context: Context, completion: @escaping @Sendable (Timeline<ActivityEntry>) -> Void) {
        let isPreview = context.isPreview
        Task { @MainActor in
            let access: WidgetTimelineAccess = isPreview ? .hidden : await WidgetTimelineRefresh.shared.refreshIfConfigured()
            completion(timeline(access: access))
        }
    }

    @MainActor private func timeline(access: WidgetTimelineAccess) -> Timeline<ActivityEntry> {
        let observed = WidgetTimelineRefresh.shared.snapshot(access: access)
        let date = Date.now
        let snapshot = access.mayDisplay(at: date) ? observed : nil
        let entry = ActivityEntry(date: date, activity: snapshot, fresh: snapshot.map { $0.expiresAt > date } == true)
        var entries = [entry]
        if let activity = entry.activity, activity.expiresAt > entry.date {
            let expired = access.permitExpiry == nil ? activity : nil
            entries.append(ActivityEntry(date: activity.expiresAt, activity: expired, fresh: false))
        }
        if let expiry = access.permitExpiry, expiry > date, !entries.contains(where: { $0.date == expiry }) {
            entries.append(ActivityEntry(date: expiry, activity: nil, fresh: false))
            entries.sort { $0.date < $1.date }
        }
        let policy: TimelineReloadPolicy = access.refreshEnabled ? .after(date.addingTimeInterval(WidgetTimelineRefresh.retryInterval)) : .never
        return Timeline(entries: entries, policy: policy)
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

private struct PinnedHeinzelEntry: TimelineEntry, Sendable {
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

private struct PinnedTimelinePlan: Sendable {
    let entries: [PinnedHeinzelEntry]
    let refreshAt: Date?
}

private struct PinnedHeinzelProvider: AppIntentTimelineProvider, Sendable {
    func placeholder(in _: Context) -> PinnedHeinzelEntry {
        PinnedHeinzelEntry(date: .now, heinzel: nil, fresh: false, workspaceScope: nil,
                           updatedAt: nil, configured: false, loading: true)
    }

    func snapshot(for configuration: SelectHeinzelIntent, in context: Context) async -> PinnedHeinzelEntry {
        let date = Date.now
        if context.isPreview {
            return PinnedHeinzelEntry(date: date, heinzel: previewHeinzels(at: date).first, fresh: true,
                                      workspaceScope: nil, updatedAt: date, configured: true)
        }
        let snapshot = WidgetCredentialStore.enrollmentEnabled ? nil : ActivityWidgetData.read()
        return entry(for: configuration, from: snapshot, at: date)
    }

    func timeline(for configuration: SelectHeinzelIntent, in context: Context) async -> Timeline<PinnedHeinzelEntry> {
        let access: WidgetTimelineAccess = context.isPreview ? .hidden : await WidgetTimelineRefresh.shared.refreshIfConfigured()
        let identifier = configuration.heinzel?.id
        let configured = configuration.heinzel != nil
        let plan = await MainActor.run { timelinePlan(identifier: identifier, configured: configured, access: access) }
        let policy: TimelineReloadPolicy = plan.refreshAt.map { .after($0) } ?? .never
        return Timeline(entries: plan.entries, policy: policy)
    }

    @MainActor private func timelinePlan(identifier: String?, configured: Bool,
                                         access: WidgetTimelineAccess) -> PinnedTimelinePlan {
        let observed = WidgetTimelineRefresh.shared.snapshot(access: access)
        let date = Date.now
        let snapshot = access.mayDisplay(at: date) ? observed : nil
        let current = entry(identifier: identifier, configured: configured, from: snapshot, at: date)
        if let scope = snapshot?.workspaceScope, let identifier,
           identifier.hasPrefix(scope + "/") {
            ActivityWidgetData.requestDetails(for: String(identifier.dropFirst(scope.count + 1)), scope: scope)
        }
        var entries = [current]
        if let expiry = current.updatedAt?.addingTimeInterval(ActivityWidgetData.freshness), expiry > date, current.heinzel != nil {
            if let wake = current.nextWake, wake < expiry, access.mayDisplay(at: wake) {
                entries.append(entry(identifier: identifier, configured: configured, from: snapshot, at: wake))
            }
            entries.append(entry(identifier: identifier, configured: configured, from: access.permitExpiry == nil ? snapshot : nil, at: expiry))
        }
        if let expiry = access.permitExpiry, expiry > date, !entries.contains(where: { $0.date == expiry }) {
            entries.append(entry(identifier: identifier, configured: configured, from: nil, at: expiry))
            entries.sort { $0.date < $1.date }
        }
        return PinnedTimelinePlan(entries: entries,
                                  refreshAt: access.refreshEnabled ? date.addingTimeInterval(WidgetTimelineRefresh.retryInterval) : nil)
    }

    private func entry(for configuration: SelectHeinzelIntent, from snapshot: ActivityWidgetData?, at date: Date) -> PinnedHeinzelEntry {
        entry(identifier: configuration.heinzel?.id, configured: configuration.heinzel != nil, from: snapshot, at: date)
    }

    private func entry(identifier: String?, configured: Bool, from snapshot: ActivityWidgetData?, at date: Date) -> PinnedHeinzelEntry {
        let scope = snapshot?.workspaceScope
        var selectedID: String?
        let item = scope.flatMap { scope -> WidgetHeinzel? in
            guard let identifier, identifier.hasPrefix(scope + "/") else { return nil }
            let id = String(identifier.dropFirst(scope.count + 1))
            selectedID = id
            if let rich = snapshot?.roster.first(where: { $0.id == id }) { return rich }
            guard let generation = snapshot?.generation, let entry = ActivityWidgetData.find(scope: scope, generation: generation, id: id) else { return nil }
            return entry.widgetHeinzel
        }
        let updatedAt = selectedID.flatMap { id in snapshot?.observationTime(for: id) }
        return PinnedHeinzelEntry(date: date, heinzel: item, fresh: updatedAt.map { $0.addingTimeInterval(ActivityWidgetData.freshness) > date } == true,
                                  workspaceScope: scope, updatedAt: updatedAt,
                                  configured: configured)
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
            }
            .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if let updatedAt = entry.updatedAt {
                SnapshotTime(updatedAt: updatedAt, fresh: entry.fresh)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
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
    private var families: [WidgetFamily] {
        #if os(macOS)
        // systemExtraLarge is supported from macOS 14; this extension targets macOS 15.
        return [.systemMedium, .systemLarge, .systemExtraLarge]
        #else
        return [.systemMedium, .systemLarge]
        #endif
    }

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: ActivityWidgetData.rosterKind, provider: ActivityProvider()) { entry in
            HeinzelRosterView(entry: entry)
                .widgetURL(NativeRoute.chooseHeinzel.url)
                .paperWidget()
        }
        .configurationDisplayName("Heinzel Roster")
        .description("See your Heinzels’ last reported statuses and snapshot age. Tap a name to open that chat.")
        .supportedFamilies(families)
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

    private var preferredLimit: Int {
        if family == .systemLarge { return 6 }
        return family == .systemSmall ? 1 : 3
    }

    var body: some View {
        Group {
            if entry.loading || !redaction.isEmpty {
                Text(entry.loading ? "Loading workspace" : "Open to view Heinzels")
                    .font(.headline)
                    .unredacted()
            } else {
                #if os(macOS)
                if family == .systemExtraLarge {
                    ViewThatFits(in: .vertical) {
                        wideRoster(rowsPerColumn: 6)
                        wideRoster(rowsPerColumn: 4)
                        roster(limit: 1)
                    }
                } else {
                    regularRoster
                }
                #else
                regularRoster
                #endif
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Heinzels")
    }

    private var regularRoster: some View {
        ViewThatFits(in: .vertical) {
            roster(limit: preferredLimit)
            roster(limit: family == .systemLarge ? 4 : 2)
            roster(limit: 1)
        }
    }

    #if os(macOS)
    private func wideRoster(rowsPerColumn: Int) -> some View {
        Group {
            if let snapshot = entry.activity, !items.isEmpty {
                let display = WidgetRosterColumns(items: items, totalCount: snapshot.rosterCount,
                                                  rowsPerColumn: rowsPerColumn)
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .top, spacing: 16) {
                        ForEach(display.columns.indices, id: \.self) { column in
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(display.columns[column]) { item in
                                    row(item, scope: snapshot.workspaceScope)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    HStack(alignment: .bottom, spacing: 8) {
                        if display.remaining > 0 {
                            Link(destination: NativeRoute.chooseHeinzel.url) {
                                Text("+\(display.remaining)").font(.caption.weight(.semibold))
                            }
                            .accessibilityLabel("View all \(max(items.count, snapshot.rosterCount ?? 0)) Heinzels")
                        }
                        SnapshotTime(updatedAt: snapshot.updatedAt, fresh: entry.fresh)
                    }
                }
                .privacySensitive()
            } else {
                roster(limit: 1)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
    #endif

    private func roster(limit: Int) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let snapshot = entry.activity, !items.isEmpty {
                let visible = Array(items.prefix(limit))
                let overflow = WidgetRosterOverflow(deliveredCount: items.count, totalCount: snapshot.rosterCount,
                                                     visibleCount: visible.count)
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(visible) { item in
                        row(item, scope: snapshot.workspaceScope)
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                HStack(alignment: .bottom, spacing: 8) {
                    if overflow.remaining > 0 {
                        Link(destination: NativeRoute.chooseHeinzel.url) {
                            Text("+\(overflow.remaining)")
                                .font(.caption.weight(.semibold))
                        }
                        .accessibilityLabel("View all \(overflow.total) Heinzels")
                    }
                    Spacer(minLength: 0)
                    SnapshotTime(updatedAt: snapshot.updatedAt, fresh: entry.fresh)
                }
                .privacySensitive()
            } else if entry.fresh, let snapshot = entry.activity, snapshot.heinzels != nil {
                Text("No Heinzels yet").font(.subheadline)
                Spacer(minLength: 0)
                SnapshotTime(updatedAt: snapshot.updatedAt, fresh: true)
            } else {
                WidgetRefresh(updatedAt: entry.activity?.updatedAt)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
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
            .font(.caption2)
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.trailing)
            .lineLimit(2)
            .frame(maxWidth: .infinity, alignment: .trailing)
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
            Spacer(minLength: 0)
            if let updatedAt {
                SnapshotTime(updatedAt: updatedAt, fresh: false)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
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

private struct SoundscapeEntry: TimelineEntry {
    let date: Date
    let state: SoundscapeWidgetState?
    var showingInfo = false
}

private struct SoundscapeProvider: TimelineProvider {
    func placeholder(in _: Context) -> SoundscapeEntry {
        SoundscapeEntry(date: .now, state: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (SoundscapeEntry) -> Void) {
        completion(current(medium: context.family == .systemMedium))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<SoundscapeEntry>) -> Void) {
        let entry = current(medium: context.family == .systemMedium)
        completion(Timeline(entries: [entry], policy: .after(entry.date.addingTimeInterval(60))))
    }

    private func current(medium: Bool) -> SoundscapeEntry {
        // Capture the face once per fresh entry, rather than reading defaults during rendering.
        SoundscapeEntry(date: .now, state: SoundscapeWidgetState.read(),
                        showingInfo: SoundscapeWidgetFace.isShowingInfo(medium: medium))
    }
}

private struct SoundscapeWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "HeinzelSoundscape", provider: SoundscapeProvider()) { entry in
            SoundscapeWidgetContainer(entry: entry).paperWidget()
        }
        .contentMarginsDisabled()
        .configurationDisplayName("Soundscape")
        .description("Control Soundscape, choose tracks, watch video and see saved track availability.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

private struct SoundscapeWidgetContainer: View {
    let entry: SoundscapeEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        SoundscapeWidgetView(state: entry.state, family: family, showingInfo: entry.showingInfo)
    }
}

private struct SoundscapeWidgetView: View {
    let state: SoundscapeWidgetState?
    let family: WidgetFamily
    let showingInfo: Bool

    private var medium: Bool { family == .systemMedium }
    private var repeatMode: String {
        guard let mode = state?.repeatMode, ["Off", "Album", "Track"].contains(mode) else { return "—" }
        return mode
    }

    private var title: String {
        state.flatMap { $0.title.isEmpty ? nil : $0.title } ?? "Soundscape"
    }

    private var repeatActive: Bool { repeatMode == "Album" || repeatMode == "Track" }

    var body: some View {
        Group {
            if showingInfo { information } else { player }
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private var player: some View {
        HStack(spacing: medium ? 10 : 0) {
            if medium {
                artworkControl
                    .frame(maxWidth: 104, maxHeight: .infinity)
            }
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 4) {
                    if !medium { artworkControl.frame(width: 44, height: 44) }
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Dreaming of Electric Snow")
                            .font(.caption.weight(.semibold))
                            .lineLimit(2)
                        Text("By Latent Heinzels")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if medium { faceControl(showInfo: true) }
                }
                HStack(spacing: 0) {
                    Link(destination: SoundscapeLink.picker) {
                        HStack(spacing: 3) {
                            if state?.selectedID != nil, medium, title.hasSuffix(" (Extended Version)") {
                                VStack(alignment: .leading, spacing: 0) {
                                    Text(String(title.dropLast(" (Extended Version)".count)))
                                        .font(.caption.weight(.semibold)).lineLimit(1)
                                    Text("Extended Version").font(.caption2).lineLimit(1)
                                }
                            } else {
                                Text(state?.selectedID == nil ? "Choose track" : title)
                                    .font(.caption.weight(.semibold)).lineLimit(1)
                            }
                            Image(systemName: "chevron.down").font(.caption2)
                        }
                        .frame(maxWidth: .infinity, minHeight: medium ? 30 : 24, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .accessibilityLabel(state?.selectedID == nil ? "Choose track" : "Choose track: \(title)")
                    if !medium { faceControl(showInfo: true) }
                }
                progressControl
                transport
            }
        }
        .padding(.horizontal, medium ? 8 : 6)
        .padding(.vertical, 4)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private func faceControl(showInfo: Bool) -> some View {
        Button(intent: SetSoundscapeFaceIntent(medium: medium, showInfo: showInfo)) {
            Image(systemName: showInfo ? "info.circle" : "xmark")
                .font(.system(size: 14))
                .frame(width: showInfo && !medium ? 28 : 44, height: showInfo && !medium ? 26 : 44)
                .contentShape(Rectangle())
        }
        .accessibilityLabel(showInfo ? "Track information" : "Back to player")
    }

    private var durationLabel: String {
        guard let duration = state?.duration, duration.isFinite, duration > 0 else {
            return "Duration unknown"
        }
        return time(duration)
    }

    private var transferActive: Bool {
        validCount && !fullySaved && (state?.phase == "downloading" || state?.phase == "paused")
    }

    private var information: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .top, spacing: 0) {
                VStack(alignment: .leading, spacing: 2) {
                    Link(destination: SoundscapeLink.artist) {
                        Text("By Latent Heinzels").underline()
                            .font(.system(size: 10))
                            .frame(maxWidth: .infinity, minHeight: 24, alignment: .leading)
                            .contentShape(Rectangle())
                    }
                    .accessibilityLabel("Latent Heinzels band page")
                    Text(title).font(.caption.weight(.semibold)).lineLimit(2)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                faceControl(showInfo: false)
            }
            Text("Dreaming of Electric Snow").font(.system(size: 10)).lineLimit(2)
            HStack(spacing: 4) {
                Text(durationLabel).font(.caption2).monospacedDigit()
                if let id = state?.selectedID, state?.canWatch == true {
                    Button(intent: WatchSoundscapeIntent(trackID: id)) {
                        Label("Watch", systemImage: "video").font(.caption2)
                    }
                    .accessibilityLabel("Watch video: \(title)")
                }
            }
            Spacer(minLength: 0)
            HStack(spacing: 0) {
                downloadSummary
                    .frame(maxWidth: .infinity, alignment: .leading)
                if transferActive {
                    if state?.phase == "paused" {
                        Button(intent: SoundscapeTransferIntent(action: .resume)) {
                            transferIcon("play.fill")
                        }
                        .accessibilityLabel("Resume download")
                    } else {
                        Button(intent: SoundscapeTransferIntent(action: .pause)) {
                            transferIcon("pause.fill")
                        }
                        .accessibilityLabel("Pause download")
                    }
                    Button(intent: SoundscapeTransferIntent(action: .cancel)) {
                        transferIcon("stop.fill")
                    }
                    .accessibilityLabel("Cancel download")
                }
            }
            .frame(minHeight: 44)
        }
        .padding(8)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private func transferIcon(_ symbol: String) -> some View {
        Image(systemName: symbol).font(.system(size: 14))
            .frame(width: medium ? 44 : 32, height: 44)
            .contentShape(Rectangle())
    }

    @ViewBuilder private var downloadSummary: some View {
        if fullySaved {
            Label("Offline", systemImage: "checkmark.circle")
                .font(.system(size: 10))
                .accessibilityLabel("Album fully saved offline")
        } else if validCount, let state {
            VStack(alignment: .leading, spacing: 1) {
                Text(downloadStatus)
                Text("\(state.verified)/\(state.total)").monospacedDigit()
                if state.phase == "cancelled" {
                    Text("Saved files kept")
                }
            }
                .font(.system(size: 10))
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Offline download: \(state.verified) of \(state.total) files verified, \(downloadStatus)\(state.phase == "cancelled" ? ", saved files kept" : "")")
        } else if state?.phase == "cancelled" {
            Text("Cancelled · Saved files kept. File count unknown.")
                .font(.system(size: 10))
        } else {
            Text("Download status unknown").font(.system(size: 10))
        }
    }

    @ViewBuilder private var artwork: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 4).fill(.primary.opacity(0.08))
            if let url = state?.artworkURL {
                #if os(macOS)
                if let image = NSImage(contentsOf: url) {
                    Image(nsImage: image).resizable().scaledToFit()
                } else { Image(systemName: "music.note") }
                #else
                if let image = UIImage(contentsOfFile: url.path) {
                    Image(uiImage: image).resizable().scaledToFit()
                } else { Image(systemName: "music.note") }
                #endif
            } else { Image(systemName: "music.note") }
        }
        .clipped()
        .accessibilityHidden(true)
    }

    @ViewBuilder private var artworkControl: some View {
        if let id = state?.selectedID {
            Link(destination: SoundscapeLink.track(id)) { artwork }
                .accessibilityLabel("Open player: \(title)")
        } else {
            Link(destination: SoundscapeLink.picker) { artwork }
                .accessibilityLabel("Choose a track from Dreaming of Electric Snow")
        }
    }

    private var validCount: Bool {
        guard let state else { return false }
        return state.phase != "unavailable" && state.total > 0 && state.verified >= 0 && state.verified <= state.total
    }

    private var fullySaved: Bool {
        validCount && state?.verified == state?.total && state?.phase == "ready"
    }

    private var downloadFraction: Double? {
        guard let state, state.phase == "downloading", state.verified < state.total,
              let fraction = state.downloadFraction, fraction.isFinite, (0...1).contains(fraction) else { return nil }
        return fraction
    }

    @ViewBuilder private var elapsed: some View {
        if let state, let position = state.position, position.isFinite, position >= 0, state.selectedID != nil {
            if state.advancing, let origin = state.playbackOrigin, let duration = state.duration, duration > 0 {
                Text(timerInterval: origin...origin.addingTimeInterval(duration), countsDown: false)
                    .frame(maxWidth: 45, alignment: .leading)
            } else {
                Text(time(position))
            }
        } else {
            Text("—")
        }
    }

    private func time(_ seconds: Double) -> String {
        let bounded = Int(min(max(seconds, 0), 2_678_400))
        return "\(bounded / 60):\(String(format: "%02d", bounded % 60))"
    }

    private func positionBar(seconds: Double, duration: Double) -> some View {
        let fraction = min(max(seconds / duration, 0), 1)
        return GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.accentColor).frame(width: geometry.size.width * fraction)
            }
        }
        .accessibilityLabel("Track playback progress")
        .accessibilityValue(Text(fraction, format: .percent.precision(.fractionLength(0))))
    }

    @ViewBuilder private var playbackProgress: some View {
        if let state, state.selectedID != nil, let position = state.position, let duration = state.duration, duration > 0 {
            Group {
                if state.advancing, let origin = state.playbackOrigin {
                    TimelineView(.periodic(from: origin, by: 5)) { context in
                        positionBar(seconds: context.date.timeIntervalSince(origin), duration: duration)
                    }
                } else {
                    positionBar(seconds: position, duration: duration)
                }
            }
            .frame(height: 4)
        }
    }

    private var timeline: some View {
        VStack(spacing: 2) {
            ZStack(alignment: .bottomLeading) {
                Capsule().fill(.primary.opacity(0.12))
                    .frame(height: 4)
                    .overlay { playbackProgress }
                    .frame(maxHeight: .infinity, alignment: .top)
                if let downloadFraction {
                    GeometryReader { geometry in
                        Capsule().fill(.yellow)
                            .frame(width: geometry.size.width * downloadFraction, height: 2)
                            .frame(maxHeight: .infinity, alignment: .bottom)
                    }
                    .padding(.horizontal, 3)
                }
            }
            .frame(height: 6)
            HStack(spacing: 2) {
                elapsed
                Spacer(minLength: 0)
                if let downloadFraction {
                    Text(downloadFraction, format: .percent.precision(.fractionLength(0)))
                        .accessibilityLabel("Downloading album")
                        .accessibilityValue(Text(downloadFraction, format: .percent))
                }
                Spacer(minLength: 0)
                Text(state?.duration.map(time) ?? "—")
            }
            .font(.caption2)
            .monospacedDigit()
        }
        .frame(maxWidth: .infinity, minHeight: 27)
        .contentShape(Rectangle())
    }

    @ViewBuilder private var progressControl: some View {
        if let id = state?.selectedID {
            Link(destination: SoundscapeLink.track(id)) { timeline }
                .accessibilityLabel("Open track position: \(title)")
                .accessibilityHint("Opens the player to adjust playback position")
                .accessibilityValue(progressDescription)
        } else {
            Link(destination: SoundscapeLink.picker) { timeline }
                .accessibilityLabel("Choose a track")
        }
    }

    private var progressDescription: String {
        let progress = state?.position.map { "\(time($0)) of \(durationLabel)" } ?? "Playback time unavailable"
        guard let downloadFraction else { return progress }
        return "\(progress), downloading album \(downloadFraction.formatted(.percent))"
    }

    private var downloadStatus: String {
        switch state?.phase {
        case "downloading": "Downloading"
        case "paused": "Paused"
        case "cancelled": "Cancelled"
        case "failed": "Failed"
        case "ready": "Incomplete"
        default: "Download status unknown"
        }
    }

    private var transport: some View {
        HStack(spacing: 0) {
            Button(intent: PreviousSoundscapeIntent()) {
                Image(systemName: "backward.end.fill").font(.system(size: 14))
                    .frame(minWidth: 0, maxWidth: .infinity)
                    .frame(height: 44)
                    .contentShape(Rectangle())
            }
            .disabled(state?.canPrevious != true)
            .accessibilityLabel("Previous track")
            Button(intent: ToggleSoundscapeIntent()) {
                Image(systemName: state?.playing == true ? "pause.fill" : "play.fill")
                    .font(.system(size: 23, weight: .semibold))
                    .frame(height: 44)
                    .frame(minWidth: 0, maxWidth: .infinity)
                    .background(.primary.opacity(0.08), in: Capsule())
                    .frame(height: 44)
                    .contentShape(Rectangle())
            }
            .disabled(state?.playing != true && state?.canPlaySaved != true)
            .accessibilityLabel(state?.playing == true ? "Pause Soundscape" : "Play Soundscape")
            Button(intent: NextSoundscapeIntent()) {
                Image(systemName: "forward.end.fill").font(.system(size: 14))
                    .frame(minWidth: 0, maxWidth: .infinity)
                    .frame(height: 44)
                    .contentShape(Rectangle())
            }
            .disabled(state?.canNext != true)
            .accessibilityLabel("Next track")
            repeatControl
        }
        .disabled(state == nil)
    }

    private var repeatControl: some View {
        Button(intent: RepeatSoundscapeIntent()) {
            Image(systemName: repeatMode == "Track" ? "repeat.1" : "repeat")
                .foregroundStyle(repeatActive ? Color.accentColor : Color.primary)
                .font(.system(size: 14, weight: repeatActive ? .bold : .regular))
                .frame(minWidth: 0, maxWidth: .infinity)
                .frame(height: 44)
                .contentShape(Rectangle())
        }
        .disabled(state?.canPlaySaved != true && state?.playing != true)
        .accessibilityLabel("Repeat")
        .accessibilityValue(repeatMode == "—" ? "Unavailable" : repeatMode)
        .accessibilityHint("Cycles Off, Album, Track")
    }
}
