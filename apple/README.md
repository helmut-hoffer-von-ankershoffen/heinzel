# Heinzelmännchen for macOS, iPhone and iPad

This spike uses a SwiftUI shell and WebKit to display the same Heinzel workspace served to Electron. The Mac opens a local foreground `hz` service. iPhone and iPad connect to an existing Heinzel host.

Open `Heinzel.xcodeproj` in Xcode, select the **Heinzel** scheme and run on **My Mac**, an iPhone simulator, or an iPad simulator. macOS 15 and iOS/iPadOS 18 are the deployment targets.

- On macOS, install the matching `hz` CLI first. Quitting the Swift app stops the foreground process it launched.
  When testing a Debug build against a checkout, set `HZ_SPIKE_CLI` to the absolute path of that checkout's `hz` executable after running `make web-build`.
- On iPhone and iPad, use the authenticated sign-in link for the host's private HTTPS origin. See [private web sharing](../docs/PRIVATE_WEB_SHARING.md). Once paired inside the workspace, use the server address to reconnect.
- Run `apple/scripts/package-macos VERSION` to build a local arm64 archive. The managed install and update commands require the official version-matched, attested release assets.

Electron remains the release default during the spike. Trusted external link handoffs, native notification and audio parity, and managed runtime lifecycle still need implementation and acceptance coverage.

Use **Connection → Share workspace link** to share the workspace address. Builds with `HEINZEL_HTTPS_LINKS=YES` share a `https://heinzel.team/open/#server=…` Universal Link. Other builds share a `heinzel://connect?server=…` link. A new device signs in separately using the host's session link.

The Swift shell uses the same title-bar layout and Browser panel as Electron. The Browser panel uses a separate WebKit session. Enable the host’s `sidebar_browser` feature flag to use the Browser panel. The browser uses an authenticated loopback HTTPS proxy with public-only DNS resolution and no direct fallback. The guest enables WebKit Lockdown Mode to disable peer connections. Mac, iPhone simulator, and iPad simulator probes confirmed that public HTTPS works through the proxy, loopback HTTP and WebRTC STUN are blocked, and closing the proxy stops public navigation. Lockdown Mode may limit some websites. See [ADR 0140](../docs/adr/0140-isolate-sidebar-browser-content-in-the-desktop.md).

In the local macOS workspace, system audio uses a private Core Audio tap and macOS audio recording permission. On iOS/iPadOS 27 or later, it uses the system ScreenCaptureKit picker where available. iOS Simulator builds omit system audio because their SDK does not expose ScreenCaptureKit. The microphone uses WebKit’s permission prompt in the workspace.

Electron also offers **Workspace audio**. That source still needs an implementation for the Apple app. The iPhone and iPad ScreenCaptureKit picker filters audio at application scope, so it cannot isolate the workspace from the Browser panel. See [Apple’s capture-filter guidance](https://developer.apple.com/videos/play/wwdc2022/10155/).

For a signed iPhone or iPad build, enable Associated Domains for the app ID and
build with `CODE_SIGN_ENTITLEMENTS=App/UniversalLinks.entitlements HEINZEL_HTTPS_LINKS=YES`.
Set the `APPLE_APPLICATION_IDENTIFIER_PREFIX` repository variable to the prefix
from the app's signed `application-identifier` entitlement. The Docs workflow
then generates `/.well-known/apple-app-site-association` alongside `/open/`.
To stage the same files locally, run `make docs-site` and then
`APPLE_APPLICATION_IDENTIFIER_PREFIX=APPLICATION_IDENTIFIER_PREFIX make docs-stage-apple-association`.

After publication, check that the association URL responds directly with HTTP 200
and `Content-Type: application/json`. Verify the expected app ID and `/open/`
path, then tap a shared link in Messages on a fresh signed installation. The
fallback page reads the workspace address from the URL fragment, which keeps it
out of the page request.
