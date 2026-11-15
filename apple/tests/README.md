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

The circular Lock Screen Heinzel artwork and inline workspace name intentionally retain identity outside the app.
Home-screen launcher labels explain the destination; snapshot widgets prioritize status and always disclose age.
