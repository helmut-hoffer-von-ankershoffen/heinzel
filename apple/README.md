# Heinzelmännchen for macOS, iPhone and iPad

This spike uses a SwiftUI shell and WebKit to display the same Heinzel workspace served to Electron. The Mac opens a local foreground `hz` service. iPhone and iPad connect to an existing Heinzel host.

Open `Heinzel.xcodeproj` in Xcode, select the **Heinzel** scheme and run on **My Mac**, an iPhone simulator, or an iPad simulator. macOS 15 and iOS/iPadOS 18 are the deployment targets.

- On macOS, install the matching `hz` CLI first. Quitting the Swift app stops the foreground process it launched.
- On iPhone and iPad, use the authenticated sign-in link for the host's private HTTPS origin. See [private web sharing](../docs/PRIVATE_WEB_SHARING.md). Once paired inside the workspace, use the server address to reconnect.
- Run `apple/scripts/package-macos VERSION` to build a local arm64 archive. The managed install and update commands require the official version-matched, attested release assets.

Electron remains the release default during the spike. Trusted external link handoffs, native notification and audio parity, and managed runtime lifecycle still need implementation and acceptance coverage.

Use **Connection → Share workspace link** to generate a `heinzel://connect?server=…` link. It carries the HTTPS address only. A new device signs in separately using the host's session link. Signed distribution and the website association are required to verify an HTTPS Universal Link from Messages.

The Swift shell uses the same title-bar layout and Browser panel as Electron. The Browser panel uses a separate WebKit session. To exercise its layout in a debug build, run with `HZ_SPIKE_BROWSER=1` and enable the host’s `sidebar_browser` feature flag. The browser uses an authenticated loopback HTTPS proxy with public-only DNS resolution and no direct fallback. Release enablement depends on WebKit network containment checks under [ADR 0140](../docs/adr/0140-isolate-sidebar-browser-content-in-the-desktop.md).

System audio uses the system ScreenCaptureKit picker on macOS and on iOS/iPadOS 27 or later where the picker is available. iOS Simulator builds omit this feature because their SDK does not expose ScreenCaptureKit. The microphone uses WebKit’s permission prompt in the workspace.
