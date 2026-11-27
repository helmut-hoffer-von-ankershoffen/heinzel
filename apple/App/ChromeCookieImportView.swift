#if os(macOS)
import Foundation
import OSLog
import SwiftUI
import WebKit

/// The window behind File > Import Cookies from Browser…: opened only from the app's menu, never by a page.
@MainActor
final class CookieImportModel: ObservableObject {
    enum Step { case start, choose, manage }
    struct Imported: Hashable {
        let site: String
        let name: String
        let domain: String
        let path: String
    }

    static let shared = CookieImportModel()
    private static let log = Logger(subsystem: "team.heinzel.app", category: "cookies")

    @Published var step = Step.start
    @Published var profiles: [ChromeProfile] = []
    @Published var profile = ""
    @Published var sites: [CookieSite] = []
    @Published var chosen: Set<String> = []
    @Published var filter = ""
    @Published var status = ""
    @Published var problem = false
    @Published var busy = false
    @Published var imported: [(site: String, count: Int)] = []
    // What was imported (never a value), so the person can remove it; the store itself forgets it all on quit.
    private var ledger: [Imported] = []

    var visibleSites: [CookieSite] {
        let query = filter.trimmingCharacters(in: .whitespaces).lowercased()
        return query.isEmpty ? sites : sites.filter { $0.site.contains(query) }
    }

    func show(_ next: Step) {
        status = ""
        problem = false
        if next == .manage { Task { await refreshImported() } } else { loadProfiles() }
        step = next == .manage ? .manage : .start
    }

    private func report(_ text: String, problem: Bool = false) {
        status = text
        self.problem = problem
    }

    func loadProfiles() {
        profiles = ChromeCookies.profiles()
        if !profiles.contains(where: { $0.id == profile }) { profile = profiles.first?.id ?? "" }
        if profiles.isEmpty { report("No Google Chrome profile was found on this Mac.", problem: true) }
    }

    func chooseSites() async {
        busy = true
        defer { busy = false }
        let profile = profile
        do {
            let rows = try await Task.detached {
                try ChromeCookies.readRows(database: ChromeCookies.database(for: profile)).rows
            }.value
            sites = ChromeCookies.siteList(rows)
            chosen = Set(sites.filter(\.checked).map(\.site))
            filter = ""
            report("")
            step = .choose
        } catch { report(error.localizedDescription, problem: true) }
    }

    func importChosen() async {
        guard let store = IntegratedBrowser.current?.guestCookieStore else {
            report("Open the built-in browser first, then import.", problem: true)
            return
        }
        busy = true
        defer { busy = false }
        report("Importing… macOS may ask for your Mac login password.")
        let profile = profile
        let sites = chosen
        do {
            // The Keychain prompt and the decryption run off the main thread; the password and key end with it.
            let result = try await Task.detached {
                let table = try ChromeCookies.readRows(database: ChromeCookies.database(for: profile))
                var password = try ChromeCookies.keychainPassword()
                defer { password.resetBytes(in: 0..<password.count) }
                let decrypted = ChromeCookies.decryptCookies(table, password: password, sites: sites, persistent: false)
                return (decrypted.cookies.map { ($0.site, $0.cookie) }, decrypted.skipped)
            }.value
            for (site, cookie) in result.0 {
                await store.setCookie(cookie)
                let entry = Imported(site: site, name: cookie.name, domain: cookie.domain, path: cookie.path)
                ledger.removeAll { $0 == entry }
                ledger.append(entry)
            }
            let names = Set(result.0.map(\.0))
            Self.log.info("\(ChromeCookies.auditLine(profile: profile, persistent: false, imported: result.0.count, skipped: result.1, sites: names), privacy: .public)")
            let skipped = result.1 == 0 ? "" : " \(result.1) could not be imported."
            report("Imported \(result.0.count) cookies from \(names.count) sites.\(skipped)")
        } catch { report(error.localizedDescription, problem: true) }
    }

    private func matches(_ entry: Imported, _ cookie: HTTPCookie) -> Bool {
        entry.name == cookie.name && entry.path == cookie.path && entry.domain == cookie.domain
    }

    func refreshImported() async {
        guard let store = IntegratedBrowser.current?.guestCookieStore else {
            ledger.removeAll()
            imported = []
            return
        }
        let jar = await store.allCookies()
        ledger = ledger.filter { entry in jar.contains { matches(entry, $0) } }
        imported = Dictionary(grouping: ledger, by: \.site).map { ($0.key, $0.value.count) }.sorted { $0.site < $1.site }
        if imported.isEmpty { report("No imported cookies.") }
    }

    func remove(site: String?) async {
        guard let store = IntegratedBrowser.current?.guestCookieStore else { return }
        let chosen = ledger.filter { site == nil || $0.site == site }
        for cookie in await store.allCookies() where chosen.contains(where: { matches($0, cookie) }) {
            await store.deleteCookie(cookie)
        }
        ledger.removeAll { chosen.contains($0) }
        Self.log.info("""
            {"event":"native.cookies.remove","removed":\(chosen.count),"sites":\(Set(chosen.map(\.site)).count)}
            """)
        await refreshImported()
    }
}

struct ChromeCookieImportView: View {
    @ObservedObject var model = CookieImportModel.shared
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            switch model.step {
            case .start: start
            case .choose: choose
            case .manage: manage
            }
            if !model.status.isEmpty {
                Text(model.status).foregroundStyle(model.problem ? Color.red : Color.secondary).font(.callout)
            }
        }
        .padding(22)
        .frame(minWidth: 440, minHeight: 360, alignment: .topLeading)
        .onAppear { if model.step != .manage { model.loadProfiles() } }
    }

    private var start: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Import cookies from your browser").font(.title2.weight(.semibold))
            Picker("From", selection: $model.profile) {
                ForEach(model.profiles) { Text($0.label).tag($0.id) }
            }
            // The native browser keeps its cookies in memory: whatever is imported ends when the app quits.
            Toggle("Keep me signed in between sessions", isOn: .constant(false)).disabled(true)
            Text("On this Mac the built-in browser forgets its cookies when you quit.").font(.caption).foregroundStyle(.secondary)
            Text("Nothing leaves this computer. macOS may ask for your Mac login password to unlock Google Chrome's cookies.")
                .foregroundStyle(.secondary)
            Spacer()
            HStack {
                Button("Manage imported cookies") { model.show(.manage) }.buttonStyle(.link)
                Spacer()
                Button("Cancel") { dismissWindow(id: "cookie-import") }.keyboardShortcut(.cancelAction)
                Button("Import") { Task { await model.chooseSites() } }
                    .keyboardShortcut(.defaultAction).disabled(model.profile.isEmpty || model.busy)
            }
        }
    }

    private var choose: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Choose sites to import").font(.title2.weight(.semibold))
            TextField("Filter sites", text: $model.filter).textFieldStyle(.roundedBorder)
            HStack {
                Toggle("Select all", isOn: Binding(
                    get: { !model.sites.isEmpty && model.chosen.count == model.sites.count },
                    set: { model.chosen = $0 ? Set(model.sites.map(\.site)) : [] }
                ))
                Spacer()
                Text("\(model.chosen.count) of \(model.sites.count) selected").foregroundStyle(.secondary)
            }
            List(model.visibleSites) { site in
                HStack {
                    Toggle(site.site, isOn: Binding(
                        get: { model.chosen.contains(site.site) },
                        set: { if $0 { model.chosen.insert(site.site) } else { model.chosen.remove(site.site) } }
                    ))
                    Spacer()
                    if let category = site.category {
                        Text(category.label).font(.caption).padding(.horizontal, 7).padding(.vertical, 2)
                            .background(Capsule().fill(Color.orange.opacity(0.15)))
                    }
                    Text("\(site.count)").foregroundStyle(.secondary).monospacedDigit()
                }
            }
            HStack {
                Spacer()
                Button("Back") { model.show(.start) }.disabled(model.busy)
                Button("Import") { Task { await model.importChosen() } }
                    .keyboardShortcut(.defaultAction).disabled(model.chosen.isEmpty || model.busy)
            }
        }
    }

    private var manage: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Manage imported cookies").font(.title2.weight(.semibold))
            List(model.imported, id: \.site) { entry in
                HStack {
                    Text(entry.site)
                    Spacer()
                    Text(entry.count == 1 ? "1 cookie" : "\(entry.count) cookies").foregroundStyle(.secondary)
                    Button("Remove") { Task { await model.remove(site: entry.site) } }
                        .accessibilityLabel("Remove cookies of \(entry.site)")
                }
            }
            HStack {
                Button("Remove all", role: .destructive) { Task { await model.remove(site: nil) } }
                    .disabled(model.imported.isEmpty)
                Spacer()
                Button("Done") { dismissWindow(id: "cookie-import") }.keyboardShortcut(.defaultAction)
            }
        }
    }
}
#endif
