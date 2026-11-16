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
