# Heinzelmännchen for macOS, iPhone and iPad

This spike uses a SwiftUI shell and WebKit to display the same Heinzel workspace served to Electron. The Mac opens a local foreground `hz` service. iPhone and iPad connect to an existing Heinzel host.

Open `Heinzel.xcodeproj` in Xcode, select the **Heinzel** scheme and run on **My Mac**, an iPhone simulator, or an iPad simulator. macOS 15 and iOS/iPadOS 18 are the deployment targets.

- On macOS, install the matching `hz` CLI first. Quitting the Swift app stops the foreground process it launched.
  When testing a Debug build against a checkout, set `HZ_SPIKE_CLI` to the absolute path of that checkout's `hz` executable after running `make web-build`.
- On iPhone and iPad, use the authenticated sign-in link for the host's private HTTPS origin. See [private web sharing](../docs/PRIVATE_WEB_SHARING.md). Once paired inside the workspace, use the server address to reconnect.
- Run `apple/scripts/package-macos VERSION` to build a local arm64 archive. The managed install and update commands require the official version-matched, attested release assets.

Electron remains the release default during the spike. See [ADR 0147](../docs/adr/0147-use-a-swift-shell-for-apple-workspaces.md) for the acceptance criteria.

The app lists recently used workspaces by name when one is known. A cloud icon identifies entries from iCloud. Use the trash icon to forget an entry on this device. A fresh synced sign-in or a new pasted sign-in can add it again.

On a Mac signed for the same Apple Developer team as the iPhone app, open the local workspace’s Connection pane and enable **Sync Sign-in with iCloud**. The Mac publishes its current sign-in through iCloud Keychain when private Tailscale sharing is ready. Your other devices use that sign-in when you select the workspace. This requires the same Apple Account and iCloud Keychain on those devices. Build the Mac app with an eligible team provisioning profile and `HEINZEL_MAC_ENTITLEMENTS=App/MacCloudSignIn.entitlements HEINZEL_ICLOUD_SIGN_IN=YES` to enable this option.

Use the **Share** icon in Workspace Connection to share the workspace address. Builds with `HEINZEL_HTTPS_LINKS=YES` share a `https://heinzel.team/open/#server=…` Universal Link. Other builds share a `heinzel://connect?server=…` link. A new device signs in separately using the host's session link.

In the local Mac app, turn on private sharing and choose **Copy app link** in the sharing pane. Use a Mac build with `HEINZEL_HTTPS_LINKS=YES` to copy a link for Messages. Recipients need a signed app with the live domain association below. A new device can then paste its private sign-in link in the app.

The Swift shell uses the same title-bar layout and Browser panel as Electron. The Browser panel uses a separate WebKit session. Enable the host’s `sidebar_browser` feature flag to use the Browser panel. The browser uses an authenticated loopback HTTPS proxy with public-only DNS resolution and no direct fallback. The guest enables WebKit Lockdown Mode to disable peer connections. Mac, iPhone simulator, and iPad simulator probes confirmed that public HTTPS works through the proxy, loopback HTTP and WebRTC STUN are blocked, and closing the proxy stops public navigation. Lockdown Mode may limit some websites. See [ADR 0140](../docs/adr/0140-isolate-sidebar-browser-content-in-the-desktop.md).

On iPhone and iPad, add **Workspace** from the **Heinzelmännchen** entry in the widget gallery. The medium Home Screen widget also opens **Connection Help**. On the Lock Screen, add the circular **Workspace** widget or the rectangular **Connection Help** widget. These launchers show artwork and labels. The separate **Active Heinzels** Home Screen widget shows awake and waiting counts from the workspace API. The app refreshes this count during use, and the widget shows when it last refreshed. Stale snapshots give way to an Open to refresh prompt as WidgetKit advances the timeline. In Shortcuts, use **Open Workspace** or **Open Connection Help**. Assign either shortcut to the Action Button on supported iPhones.

In notification settings, choose **Notify me on this device**. Heinzel confirms the workspace and asks for system permission. Notifications come from the connected workspace while the app is open. Tapping an alert opens the notice when its workspace is connected. On iPhone and iPad, keep Heinzel active to receive new notices.

In the local macOS workspace, system audio uses a private Core Audio tap and macOS audio recording permission. On iOS/iPadOS 27 or later, it uses the system ScreenCaptureKit picker where available. iOS Simulator builds omit system audio because their SDK does not expose ScreenCaptureKit. The microphone uses WebKit’s permission prompt in the workspace.

Electron also offers **Workspace audio**, which is still unavailable in Swift. In a macOS 27.0.1 playback probe,
a standard WebKit workspace view and a separate nonpersistent Browser view in Lockdown Mode used the same Core Audio
output process. Each view played a different test tone. Core Audio reported the same process with either view playing
alone and with both playing, whichever started first. A process tap would include both views.
See [Apple’s Core Audio tap documentation](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps).

On iPhone and iPad, the ScreenCaptureKit picker filters audio at app scope, so selecting the workspace cannot exclude
the Browser panel. See [Apple’s capture-filter guidance](https://developer.apple.com/videos/play/wwdc2022/10155/).

For a signed iPhone or iPad build, enable Associated Domains for the app ID and
build with `HEINZEL_IOS_ENTITLEMENTS=App/UniversalLinks.entitlements HEINZEL_HTTPS_LINKS=YES`.
Set the `APPLE_APPLICATION_IDENTIFIER_PREFIX` repository variable to the prefix
from the app's signed `application-identifier` entitlement. The Docs workflow
then generates `/.well-known/apple-app-site-association` alongside `/open/`.
The desktop publish workflow uses the same setting to produce Mac builds that copy
HTTPS app links. For a local Mac package, run
`HEINZEL_HTTPS_LINKS=YES apple/scripts/package-macos VERSION`.
To stage the same files locally, run `make docs-site` and then
`APPLE_APPLICATION_IDENTIFIER_PREFIX=APPLICATION_IDENTIFIER_PREFIX make docs-stage-apple-association`.

Before publishing an HTTPS-enabled Mac package, check that the live association
URL responds directly with HTTP 200 and `Content-Type: application/json`. Verify
the expected app ID and `/open/` path, then tap a shared link in Messages on a
fresh signed installation. The
fallback page reads the workspace address from the URL fragment, which keeps it
out of the page request.
