# Widget model checks

From the repository root on macOS with Xcode, compile and run the isolated widget model probe:

```sh
sm gate run -- env DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swiftc -parse-as-library -swift-version 6 apple/Shared/NativeRoute.swift apple/Shared/ActivityCatalog.swift apple/Shared/ActivityWidgetData.swift apple/tests/WidgetStatusTests.swift -o apple/.build/widget-model-tests
sm gate run -- apple/.build/widget-model-tests
```

Create `apple/.build` first if it does not exist. The probe never accesses shared widget defaults.
Build the iOS Simulator and arm64 macOS schemes separately to check each platform's widget extension.

Visual acceptance still requires installed widgets: small and medium launchers, small and medium pinned status,
medium and large roster, and iOS circular, inline and rectangular accessories. Check light, dark, tinted and
privacy-redacted states, the largest supported text sizes, long names, fresh and expired snapshots, and each
chat/picker/workspace link. A synthetic SwiftUI render is useful layout evidence but cannot prove widget links,
VoiceOver, iOS Dynamic Type or the system's tinted rendering.

The standalone fixture endpoints are immutable, fictional configuration constants. Their values are protocol
test inputs, including the intentionally rejected cleartext origin and cross-origin redirect; they do not select
a production workspace. Keep them stable across the Swift and Python contract fixtures.

The two WebKit fixture navigation boundaries are explicit. `WidgetWebViewRotationTests` never loads content and
cancels all navigation; its only observations are view replacement and nonpersistent data-store reuse.
`WidgetWebKitEnrollmentTests` allows only its supplied HTTPS loopback port, without URL credentials, and checks
both navigation actions and final response origins. Its existing certificate fingerprint and trust checks remain
required. The compiled enrollment fixture's `--check-navigation-policy` mode tests the URL predicate without
creating a WebView, launching an application or contacting a server. It does not establish WebKit callback,
certificate, signed-app or real enrollment acceptance; those still require the separate signed fixture.

The circular Lock Screen Heinzel artwork and inline workspace name intentionally retain identity outside the app.
Home-screen launcher labels explain the destination; snapshot widgets prioritize status and always disclose age.

## Synthetic local-first album

On macOS 15+ with Xcode command-line tools, Python 3 and local `ffmpeg` (libx264/AAC),
from the repository root, use a new, nonexistent scratch directory:

```sh
python3 apple/tests/run_soundscape_album.py --scratch "${TMPDIR:-/tmp}/soundscape-proof-$(uuidgen)"
```

This command generates two tiny playable videos and PNG posters, compiles the current
injected library/player with the CLI assertions, and runs muted probes. It selects
`dreaming-of-electric-snow` using fictional `Example Track 1` / `Example Track 2` titles.
All generated media, source copies and SHA-256 pins, compile/probe logs and isolated
HOME/cache directories stay in scratch. Each invocation uses a unique defaults suite;
the shared-container lookup is shimmed to nil and widget publishing stays in memory.
The HTTP host binds only to loopback, supports byte ranges and delays the second
track to exercise partial download controls.

Coverage includes initial streaming Play and widget-style pause, durable cancellation,
pre-manifest pause/cancel restoration, rapid download pause/resume, cold ready restoration,
local decode, transport/repeat, Watch and persisted selection. Cold playback runs in a
fresh process after the listener shuts down, using an ephemeral URLSession with no cache.
`PASS` is written only after every probe exits successfully; failures retain their logs.
This is a synthetic API proof, not installed app, widget extension or visual acceptance.

### Repeat at playback boundaries

Run the native two-track fixture on a test Mac with Xcode and ffmpeg. Supply a new scratch directory for each run. The report records the selected track, player position and observed end event for Off, Album and Track, first while streaming from loopback and then in fresh processes playing saved files with network access denied.

```sh
sm gate run -- python3 apple/tests/run_soundscape_repeat.py --execute --scratch "$repeat_run"
sm gate run -- python3 apple/tests/run_soundscape_repeat.py --iphoneos-build-only --scratch "$iphone_build"
```

The second command compiles the probe for arm64 iPhoneOS. Exercise the same transitions with the signed iPhone app and both widget sizes for device acceptance.
