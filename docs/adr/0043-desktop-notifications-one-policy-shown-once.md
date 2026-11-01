---
status: proposed
date: 2026-10-24
implementation: built
decision-makers: maintainer
amends: 0020, 0039
amends-0020: every tick ends by popping up due notices
amends-0039: the notify operations are web endpoints, not REST ones
amended-by: 0046, 0078
related: 0003, 0021, 0024, 0026, 0027, 0028, 0036, 0038
implemented-by: 67
---

# 0043: Desktop notifications: one policy in the core, shown once, by hz's own macOS app

*Since 0051 the module is `heinzel`: read `hz workflow` as `hz heinzel` and `[workflow]` as `[heinzel]`.*

## Context and Problem Statement

Drafts wait for a person to read and approve them in place ([ADR 0038](0038-drafts-are-approved-in-place.md)), contract
runs on the daemon can fail ([ADR 0020](0020-daemon-head.md), [ADR 0021](0021-workflow-contracts.md)), and doctor finds
problems no one looks for. Today a person learns of any of it only by asking. The maintainer asked for desktop
notifications ("we want it to be proactive") and the design below follows live tests on macOS 27:
one policy, events kept on the machine, shown once, by the web app while it is open and natively when it isn't.

## Decision Drivers

- Heads are views (ADR 0003): what deserves attention, and when, is decided once, not per head.
- Nothing is sent home, and the web head is localhost only ([ADR 0027](0027-nothing-is-sent-home.md), ADR 0039).
- Done, not nagged (ADR 0038 §5): no repeats, a rate limit, the profile's working hours
  ([ADR 0024](0024-people-and-org.md)).
- macOS shows a notification only for an app it allowed, and asks only in the person's GUI session: a process the
  daemon starts directly, or the child of a sandboxed process, gets "Notifications are not allowed for this
  application". `osascript display notification` works everywhere but shows as Script Editor, and a click opens
  Script Editor. The project can't sign with an Apple Developer ID.
- No new dependency; text from people's systems is untrusted input to whatever shows it.

## Considered Options

- **A `Notifications` service in the kernel's public API, provided by `system.notify` with the policy and a local
  store; on macOS hz's own small notifier app, built in CI, shipped in the wheel, installed by Python and started
  through LaunchServices; notify-send on Linux; osascript last** (chosen)
- **terminal-notifier** (Homebrew): works once allowed from the person's own terminal, but is a second install, a
  second vendor, and shows as itself, not as hz. Dropped by the maintainer.
- **Compiling the app on each Mac** (`swiftc` in `hz setup`): needs the Xcode Command Line Tools at run time and
  makes every Mac's app different. Building once in CI and shipping it in the wheel needs nothing at run time.
- **Web Push (the Push API with VAPID)**: a browser vendor's push service relays every message over the internet;
  against ADR 0027 and the localhost-only web head.
- **Each head decides on its own**: two policies that drift, and the same draft shown twice.

## Decision Outcome

Chosen option: **one `Notifications` service, one policy in `system.notify`, notices kept locally, shown once; on
macOS by Heinzel Notifier**, because every head reads the same decision, nothing leaves the machine, and macOS shows
the notifications as hz's, with a click that can open the right screen.

**The service and the events.**

- `heinzel.kernel` exports `Notifications`, `Notice`, `NoticeKind` and `Delivery` (public API, ADR 0026). Like
  `ReleaseSource`, `Notifications` is a base class the kernel injects by type; `system.notify` provides it. Modules
  call `emit(kind, title, body, route=, command=, key=, urgent=)`; heads call `due`, `deliver`, `watched` and
  `post_native`.
- Kinds: `draft` (a scheduled render saved a draft to read and approve: until ADR 0038's in-place drafts exist, this
  is "approval waiting"), `failed` (a scheduled contract run failed), `doctor` (the hourly `system.notify:watch` job
  runs doctor through the kernel and reports what isn't up), `release` (a newer release in the release cache, off by
  default), `test` (`hz system notify test`). `workflow` emits the first two from its compiled jobs; a manual
  `hz workflow run` emits nothing, its person is there. A job abandoned for its timeout is reported by doctor's
  `jobs` check, not by `workflow`.
- Every notice names a `route` of the web app (`/contracts/me%3Aweekly`, `/doctor`) and the `command` that shows it in
  a terminal.

**The policy** (settings `[system.notify]`, user scope only: a project's `hz.toml` can't decide what interrupts you).

- `enabled` and one switch per kind (`draft`, `failed`, `doctor` on; `release` off). A kind that is off records
  nothing.
- `repeat_hours` (24): a notice with the same `key` within it is not recorded again. Keys name what makes two notices
  the same: the draft's hash, the failure's first line, doctor's status with the failing checks.
- `per_hour` (6): more notices recorded in an hour go to the list only, with the reason; and at most this many pop up
  in any hour, so notices held overnight don't all pop up when working hours start.
- `working_hours` (on): a notice that isn't urgent, recorded outside the profile's `[org] working_hours` (Monday to
  Friday, in `[org] timezone`), pops up when they next start instead of being lost. Only doctor's DOWN and `test` are
  urgent.
- `quiet` (off): everything goes to the list only, urgent notices too.
- `native` (on): whether the daemon pops up native notifications.

**The store.** One JSON file in `state_dir/notify/` (0700, files 0600), changed under an exclusive `flock` and replaced
atomically, because the web head and the daemon's tick are separate processes; at most 200 notices, none older than 30
days, pruned on every write. A damaged file is no notices and is replaced on the next one. Nothing is sent anywhere.

**Shown once.** A notice pops up when its `due` time came, nobody showed it, and it is less than 12 hours overdue.
Whoever shows it marks it (`deliver`); a head that shows notices says so every few seconds (`watched`), and then the
daemon holds back for 30 seconds.

**The daemon** (amends ADR 0020). Every tick ends with `post_native`: once an hour it first runs doctor through the
kernel and records what isn't up (and a newer release, if wanted); then, unless a head showed notices within the last
30 seconds, it claims the due ones under the store's lock (so no head shows them meanwhile), hands them to the native
notifier and hands back the ones it refused, which stay due for the next tick; the refusal is recorded for doctor.
Doctor's look runs here rather than as a job: it only records notices, and an hourly receipt would say nothing more.

**Heinzel Notifier**, the macOS path.

- `src/heinzel/modules/system/notify/macos/` holds a small Swift app (`main.swift`: an accessory `NSApplication` with
  `UNUserNotificationCenter`), its icon (`heinzel.svg` and `heinzel-swarm.svg`, an original drawing that `icon.swift`
  renders with AppKit) and `Info.plist` (`dev.heinzel.notifier`, `LSUIElement`). `make notifier` (`scripts/notifier.py`)
  compiles it for arm64 and x86_64 (macOS 13+), joins them with `lipo`, builds the icon with `iconutil`, stamps
  `CFBundleVersion` with a digest of the sources and signs it ad hoc (`codesign --sign -`). CI and the release build it
  on a macOS runner and pass it to the Linux build job, whose wheel and sdist ship it (`HeinzelNotifier.app`, not in
  git); the build checks that the wheel has it, and the release's SLSA provenance covers the wheel. A wheel built
  without it (a plugin's, a fork's) still works: macOS then uses osascript.
- At run time Python copies the bundled app to `~/Applications/Heinzel Notifier.app` when it is missing or its files'
  digest differs, staging beside it and swapping, and checks the copy's digest. Installed by Python from a wheel it
  carries no quarantine flag, so Gatekeeper doesn't stop it; doctor warns if it has one and names `xattr -d
  com.apple.quarantine`. Nothing is compiled on a person's Mac.
- hz starts it with `open -n -g -W --stdout <file> <app> --args ...` (LaunchServices puts it in the person's GUI
  session, from launchd and SSH too), and reads its one-word answer from the file: `posted`, `denied`, `waiting`, or
  the authorization with `--status`. macOS asks whether to allow its notifications, with the Heinzel icon, the first
  time the app asks for authorization. Only `hz system notify install`, with a person at the screen, lets it ask (and
  waits up to 90 seconds); the daemon checks `--status` first and, while macOS hasn't asked, posts nothing and tells
  doctor to run `install`, so an unanswered question never holds ticks or starts app after app. The app itself gives
  up after 60 seconds.
- Text arrives as arguments only, bounded in Python and again in Swift (80 and 240 characters); the app runs nothing.
  A notification's link opens only when it is `http://127.0.0.1:<port>/...`, checked in Swift too. Until the web app
  has a stable address (below), notifications carry no link and their text names the command.
- osascript is the last resort, for an installation without the app: it runs a fixed `on run argv` script, never a
  string with the text in it. On Linux it is `notify-send` (`--` before the text, the body HTML-escaped for its
  markup) when there is a desktop session; headless, nothing, and doctor says so.

**Install and doctor.** `hz system notify install` (CLI only) installs the app, explains macOS's question and pops up a
test now; the first notification installs it too. A step in `hz setup` comes with the web app's pull request. Doctor's `system.notify` checks name the path in use; the app's state (current,
missing, outdated), its quarantine flag and macOS's authorization (`--status`; denied is degraded, with System
Settings > Notifications > Heinzel Notifier); the last refused pop-up until one works; and, on macOS, the two usual
reasons a notification lands in Notification Center without a banner: sharing or mirroring the screen (System
Settings > Notifications > Allow notifications when mirroring or sharing the display) and the app's alert style.
Doctor never installs anything; the first notification or setup does.

**Operations.** `hz system notify list | ack | test` are operations of the CLI, MCP and web heads (`test` records an urgent notice
for the open web app or the next tick; `install`, CLI only, pops one up at once); like `drafts`, not of
the unauthenticated REST head, because notices name contracts and failures (amends ADR 0039's list of what the web
head exposes).

### Consequences

- Good: one place decides what interrupts a person; every head and every module asks it by type.
- Good: on macOS notifications show as hz's, with its icon, from the daemon too; nothing to install by hand.
- Good: nothing leaves the machine, no new credential, protocol or runtime dependency.
- Bad: the native path has a tick's latency (five minutes by default).
- Bad: the release needs a macOS runner, and the wheel grows by the app (about 1.2 MB, most of it the icon).
- Bad: the app is signed ad hoc, not notarised: installed from a wheel it runs, but copied by hand from elsewhere
  it would be quarantined, which doctor names.
- Bad: under screen sharing or a Focus macOS shows no banner at all; "working days" are Monday to Friday until the
  profile has them.

### Confirmation

- `tests/unit/modules/notify_policy_test.py`: working hours and their next start, quiet mode, the rate limit,
  repeats, switching kinds off.
- `tests/unit/modules/notify_service_test.py`: recording privately (0700/0600), repeats, a bounded, pruned store that
  survives damage, shown once, the daemon holding back while a head watches, a refused pop-up staying due and reported
  by doctor, doctor's app checks without installing, the `watch` job, the kernel injecting `NotifyService` as
  `Notifications`.
- `tests/unit/modules/notify_macos_test.py`: the app is copied, verified, replaced when it differs, posted through
  `open` with arguments only; the build script and the runtime agree on its version; the Swift source opens only
  `http://127.0.0.1:<port>`.
- `tests/unit/modules/notify_native_test.py`: which notifier each platform gets, and that hostile text reaches
  osascript and notify-send as arguments only, sanitised and bounded. `install`
  is in the service tests.
- `tests/integration/notify_test.py`: the operations through the CLI; they are web endpoints behind the token and not
  REST endpoints.
- `tests/unit/modules/daemon_service_test.py`: every tick ends with `post_native`, and a failing notifier doesn't fail
  it. `tests/unit/modules/heinzel_service_test.py`: scheduled renders report drafts and failures, manual runs don't.
- CI's build job and the release: the wheel ships `HeinzelNotifier.app/Contents/MacOS/notifier`.
- Import-linter: `system.notify` is independent of the other `system` submodules; modules reach
  `heinzel.kernel._notifications` only through the public API.

## More Information

- Next, in its own pull request and record: `hz setup` gets a notifications step; the web app shows notices while it is open (a server-sent-events stream
  under `/api` read with `fetch` and the Bearer header, the browser's Notifications API from a Settings toggle, an
  in-app list feeding the Home's "Today" cards) and becomes an installable progressive web app with a fixed default
  port and per-device tokens, so a notification's click opens it at the notice's route.
- Revisit when the profile gains working days, or when the project can notarise the app.

**An honest test.** `hz system notify test` reports each way a notice can reach you (`channels`:
the open web app, the native notifier, the daemon), each with its state, the reason and what fixes it, and `pops_up`;
it exits 1 when nothing will pop the test up. The web app adds this browser's permission and offers one fix per blocked
way: allow in the browser, `hz system notify settings` (opens System Settings > Notifications on macOS; CLI and web
head only), or the install command typed into its terminal after a confirmation.
