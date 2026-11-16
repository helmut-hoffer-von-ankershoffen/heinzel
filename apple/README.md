# Heinzelmännchen for macOS, iPhone and iPad

This spike uses a SwiftUI shell and WebKit to display the same Heinzel workspace served to Electron. The Mac opens a local foreground `hz` service. iPhone and iPad connect to an existing Heinzel host.

Open `Heinzel.xcodeproj` in Xcode, select the **Heinzel** scheme and run on **My Mac**, an iPhone simulator, or an iPad simulator. macOS 15 and iOS/iPadOS 18 are the deployment targets.
The build also requires Node.js with `stripTypeScriptTypes` support (Node.js 22.13 or newer) to generate the widget artwork from the shared web source.

- On macOS, install the matching `hz` CLI first. Quitting the Swift app stops the foreground process it launched.
  When testing a Debug build against a checkout, set `HZ_SPIKE_CLI` to the absolute path of that checkout's `hz` executable after running `make web-build`.
- On iPhone and iPad, use the authenticated sign-in link for the host's private HTTPS origin. See [private web sharing](../docs/PRIVATE_WEB_SHARING.md). Once paired inside the workspace, use the server address to reconnect.
- Run `apple/scripts/package-macos VERSION` to build a local arm64 archive. The managed install and update commands require the official version-matched, attested release assets.

Electron remains the release default during the spike. See [ADR 0147](../docs/adr/0147-use-a-swift-shell-for-apple-workspaces.md) for the acceptance criteria.

Before downgrading to an `hz` version from before Swift desktop support, switch
back with `hz app install --flavor electron` using the current CLI. Older CLI
versions cannot read the Swift install receipt; do not edit or delete it to
force a downgrade. If already downgraded, restore a Swift-capable CLI and switch
back to Electron before downgrading again.

The app lists recently used workspaces by name when one is known. A cloud icon identifies entries from iCloud. Use the trash icon to forget an entry on this device. A fresh synced sign-in or a new pasted sign-in can add it again.

On a Mac signed for the same Apple Developer team as the iPhone app, open the local workspace’s Connection pane and enable **Sync Sign-in with iCloud**. The Mac publishes its current sign-in through iCloud Keychain when private Tailscale sharing is ready. Select the workspace on your other devices to use its synced sign-in. If an already selected workspace is waiting for sign-in, Heinzel tries once when its iCloud credential arrives. This requires the same Apple Account and iCloud Keychain on those devices. Build the Mac app with an eligible team provisioning profile and `HEINZEL_MAC_ENTITLEMENTS=App/MacCloudSignIn.entitlements HEINZEL_ICLOUD_SIGN_IN=YES` to enable this option.

Use the **Share** icon in Workspace Connection to share the workspace address. Builds with `HEINZEL_HTTPS_LINKS=YES` share a `https://heinzel.team/open/#server=…` Universal Link. Other builds share a `heinzel://connect?server=…` link. A new device signs in separately using the host's session link.

In the local Mac app, turn on private sharing and choose **Copy app link** in the sharing pane. Recipients can open the custom-scheme link in the installed app and paste their private sign-in link.

The Swift shell uses the same title-bar layout and Browser panel as Electron. The Browser panel uses a separate WebKit session. Enable the host’s `sidebar_browser` feature flag to use the Browser panel. The browser uses an authenticated loopback HTTPS proxy with public-only DNS resolution and no direct fallback. The guest enables WebKit Lockdown Mode to disable peer connections. Mac, iPhone simulator, and iPad simulator probes confirmed that public HTTPS works through the proxy, loopback HTTP and WebRTC STUN are blocked, and closing the proxy stops public navigation. Lockdown Mode may limit some websites. See [ADR 0140](../docs/adr/0140-isolate-sidebar-browser-content-in-the-desktop.md).

Add **Workspace**, **Active Heinzels**, **Heinzel Status**, **Heinzel Picker**, or **Heinzel Roster** from the **Heinzelmännchen** entry in the widget gallery. Edit the Status widget to pin a Heinzel. Its status always includes the snapshot age; open asks and future scheduled wakes appear only while the snapshot is fresh. Launcher widgets name the action rather than repeating app branding. Tap a roster entry to open its chat. Activity and roster widgets put Heinzels needing attention first, show the snapshot age, and label expired statuses as last known. Tap a row to open its chat and refresh through the app. On the iPhone and iPad Lock Screen, add **Workspace** or **Connection Help**. In Shortcuts, use **Open Workspace** or **Open Connection Help**, including with the Action Button on supported iPhones.

On Mac, the app starts and signs in to its local workspace automatically. To open a remote workspace, choose **Heinzel → Connection → Switch Workspace**. The activity widgets show the local workspace while the app is running. Shared widget data requires an app and extension signed by the same Apple Developer team. For a signed local package, run `HEINZEL_MAC_SIGNING_TEAM=TEAM_ID apple/scripts/package-macos VERSION`. The teamless package supports the workspace and help launchers. For an iCloud-enabled Mac build with activity widgets, use `HEINZEL_MAC_ENTITLEMENTS=App/MacCloudSignIn.entitlements HEINZEL_MAC_WIDGET_ENTITLEMENTS=Widgets/MacWidgetGroup.entitlements HEINZEL_ICLOUD_SIGN_IN=YES` and your team ID.

In notification settings, choose **Notify me on this device**. Heinzel confirms the workspace and asks for system permission. Notifications come from the connected workspace while the app is open. Tapping an alert opens the notice when its workspace is connected. On iPhone and iPad, keep Heinzel active to receive new notices.

In the local macOS workspace, system audio uses a private Core Audio tap and macOS audio recording permission. On iOS/iPadOS 27 or later, it uses the system ScreenCaptureKit picker where available. iOS Simulator builds omit system audio because their SDK does not expose ScreenCaptureKit. The microphone uses WebKit’s permission prompt in the workspace.

Electron also offers **Workspace audio**, which is still unavailable in Swift. In a macOS 27.0.1 playback probe,
a standard WebKit workspace view and a separate nonpersistent Browser view in Lockdown Mode used the same Core Audio
output process. Each view played a different test tone. Core Audio reported the same process with either view playing
alone and with both playing, whichever started first. A process tap would include both views.
See [Apple’s Core Audio tap documentation](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps).

On iPhone and iPad, the iOS system picker controls what is shared; it does not grant unconditional access to every app.
The device user chooses what to share. Test on a physical device by starting another app's audio, choosing the
intended content in the picker, checking that the visualizer reacts, and then stopping sharing. Apple's
[iOS capture sample](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-on-ios)
requires the `screen-capture` background mode to continue a full-display stream when its app is backgrounded; this
app does not currently declare that mode, so uninterrupted background capture is not promised.

For a signed iPhone or iPad build, enable Associated Domains for the app ID and
build with `HEINZEL_IOS_ENTITLEMENTS=App/UniversalLinks.entitlements HEINZEL_HTTPS_LINKS=YES`.
Set the `APPLE_APPLICATION_IDENTIFIER_PREFIX` repository variable to the prefix
from the app's signed `application-identifier` entitlement. The Docs workflow
then generates `/.well-known/apple-app-site-association` alongside `/open/`.
Mac packages currently use custom-scheme links. The site association prefix alone
does not grant the Mac app the signed Associated Domains entitlement, so Mac
packaging refuses `HEINZEL_HTTPS_LINKS=YES` until that signing path is supported.
To stage the same files locally, run `make docs-site` and then
`APPLE_APPLICATION_IDENTIFIER_PREFIX=APPLICATION_IDENTIFIER_PREFIX make docs-stage-apple-association`.

Before publishing an HTTPS-enabled Mac package, check that the live association
URL responds directly with HTTP 200 and `Content-Type: application/json`. Verify
the expected app ID and `/open/` path, then tap a shared link in Messages on a
fresh signed installation. The
fallback page reads the workspace address from the URL fragment, which keeps it
out of the page request.

The **Write a Prompt** Home Screen or desktop widget opens a native composer. Choose a Heinzel, write the prompt,
then select **Send Prompt**. A widget cannot contain a text editor. Sending uses the signed-in workspace and
the existing durable chat API; the regular web interface is unchanged. A confirmed receipt opens the exact chat
to follow the response. Unconfirmed sends keep a private copy on this device and retry with the same request ID,
so a lost acknowledgement does not duplicate the prompt. **Discard** removes that retry copy but does not cancel
work the host may already have accepted. Prompt text is never saved in shared widget storage or widget links.
