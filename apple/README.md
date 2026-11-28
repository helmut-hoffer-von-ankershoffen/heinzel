# Heinzelmännchen for macOS, iPhone and iPad

This spike uses a SwiftUI shell and WebKit to display the same Heinzel workspace served to Electron. The Mac attaches to its local `hz` service. iPhone and iPad connect to an existing Heinzel host.

Open `Heinzel.xcodeproj` in Xcode, select the **Heinzel** scheme and run on **My Mac**, an iPhone simulator, or an iPad simulator. macOS 15 and iOS/iPadOS 18 are the deployment targets.
The build also requires Node.js with `stripTypeScriptTypes` support (Node.js 22.13 or newer) to generate the widget artwork from the shared web source.

- On macOS, install the matching `hz` CLI first. Leaving **This Mac**, selecting a remote workspace, and quitting the Swift app all leave the local backend running. Returning to **This Mac** reuses its verified session while it is healthy. Sharing is controlled independently.
  When testing a Debug build against a checkout, set `HZ_SPIKE_CLI` to the absolute path of that checkout's `hz` executable after running `make web-build`.
- On iPhone and iPad, open a known workspace from the start screen. Choose **Add Workspace** to paste its private sign-in URL. A known workspace that needs a fresh sign-in opens the same private-link field. See [private web sharing](../docs/PRIVATE_WEB_SHARING.md).
- Run `apple/scripts/package-macos` to build a local arm64 archive. Its version is Heinzel's version in `pyproject.toml`; a `VERSION` argument must equal it. The managed install and update commands require the official version-matched, attested release assets.

Electron remains the release default during the spike. See [ADR 0147](../docs/adr/0147-use-a-swift-shell-for-apple-workspaces.md) for the acceptance criteria.

Before downgrading to an `hz` version from before Swift desktop support, switch
back with `hz app install --flavor electron` using the current CLI. Older CLI
versions cannot read the Swift install receipt; do not edit or delete it to
force a downgrade. If already downgraded, restore a Swift-capable CLI and switch
back to Electron before downgrading again.

The start screen and Connection sheet show the same workspace list with the server version last reported by each workspace. A new workspace shows **Version unknown**. Remote reachability shows **Unknown** before a probe or while this device is offline. The selected check remains visible independently. Select a row to open it. Heinzel checks the destination before switching, so a failed switch leaves the current workspace and draft open. On iPhone, open the navigation drawer and choose **Workspaces** beside **Settings** in the profile menu. Use the sign-out button beside the active row to return to the picker and keep its entry. Use the minus button to remove a workspace you added. Disconnect a manually added workspace to make its Remove button available. The four indicators show Internet reachability, notification permission, system audio availability and iCloud Keychain access. Open the app version and info button to turn the same surface over. Information shows the app build, location, operating system and device checks. When connected, it also shows the backend version, reported process uptime and observed IP addresses when available. Back restores the directory. Reduce Motion makes this change immediate.

On a Mac signed for the same Apple Developer team as the iPhone app, **Sync Sign-in with iCloud** is on by default in the local workspace’s Connection pane. The Mac publishes its current sign-in through iCloud Keychain when private Tailscale sharing is ready. Select the workspace on your other devices to use its synced sign-in. If an already selected workspace is waiting for sign-in, Heinzel tries once when its iCloud credential arrives. This requires the same Apple Account and iCloud Keychain on those devices.

On iPhone, touch and hold the connected row in Workspaces for **Share Workspace Link** and **Connection Help**. On Mac, use the Share icon in Workspace Connection. Builds with `HEINZEL_HTTPS_LINKS=YES` share a `https://heinzel.team/open/#server=…` Universal Link. Other builds share a `heinzel://connect?server=…` link. A new device signs in separately using the host's session link.

In the local Mac app, turn on private sharing and choose **Copy app link** in the sharing pane. Recipients can open the custom-scheme link in the installed app and paste their private sign-in link.

The Swift shell uses the same title-bar layout and Browser panel as Electron. The Browser panel uses a separate WebKit session. Enable the host’s `sidebar_browser` feature flag to use the Browser panel. The browser uses an authenticated loopback HTTPS proxy with public-only DNS resolution and no direct fallback. The guest enables WebKit Lockdown Mode to disable peer connections. Mac, iPhone simulator, and iPad simulator probes confirmed that public HTTPS works through the proxy, loopback HTTP and WebRTC STUN are blocked, and closing the proxy stops public navigation. Lockdown Mode may limit some websites. See [ADR 0140](../docs/adr/0140-isolate-sidebar-browser-content-in-the-desktop.md).

Add **Workspace**, **Active Heinzels**, **Heinzel Status**, **Heinzel Picker**, or **Heinzel Roster** from the **Heinzelmännchen** entry in the widget gallery. Edit the Status widget to pin a Heinzel. Its status always includes the snapshot age; open asks and future scheduled wakes appear only while the snapshot is fresh. Launcher widgets name the action rather than repeating app branding. Tap a roster entry to open its chat. Activity and roster widgets put Heinzels needing attention first, show the snapshot age, and label expired statuses as last known. Tap a row to open its chat and refresh through the app. On the iPhone and iPad Lock Screen, add **Workspace** or **Connection Help**. In Shortcuts, use **Open Workspace**, **Open Connection Help**, or **Ask Heinzel**, including with the Action Button on supported iPhones. **Ask Heinzel** opens Write a Prompt, where you choose a Heinzel, enter your question, and tap **Send Prompt**.

On Mac, the app starts and signs in to its local workspace automatically. To open a remote workspace, choose **Heinzelmännchen → Workspaces…** or open the profile menu and select **Workspaces…** below Settings, then select the workspace. The activity widgets show the local workspace while the app is running. Shared widget data requires an app and extension signed by the same Apple Developer team. For a signed local package, run `HEINZEL_MAC_SIGNING_TEAM=TEAM_ID apple/scripts/package-macos VERSION`. The teamless package supports the workspace and help launchers.

For an iCloud-enabled Mac development package, also set `HEINZEL_ICLOUD_SIGN_IN=YES`, `HEINZEL_MAC_PROFILE` to the installed app profile specifier, and `HEINZEL_MAC_WIDGET_PROFILE` to the separate installed widget profile specifier. The build uses an Apple Development signing identity and requires profiles for the target Mac and corresponding bundle identifiers. Before promoting the package, the script checks both extracted products against
their signing team, profile, certificate, bundle identifier and required entitlements.

### Managed Mac release signing

The release administrator configures the GitHub environment `macos-developer-id` with required reviewers and deployment restricted to the protected `main` branch. The normal Release tag workflow calls Publish from `main`. Only the protected-main Release tag workflow can enter signing. GitHub evaluates environment restrictions against the caller’s ref. [Deployment rules](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) · [Reusable workflow context](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)

Keep the Developer ID Application P12, its password and the Apple notary P8 exclusively in that environment as `HEINZEL_MAC_CERTIFICATE_B64`, `HEINZEL_MAC_CERTIFICATE_PASSWORD` and `HEINZEL_MAC_NOTARY_KEY_B64`. Set environment variables `HEINZEL_MAC_SIGNING_TEAM`, `HEINZEL_MAC_NOTARY_KEY_ID` and `HEINZEL_MAC_NOTARY_ISSUER`. Core forwards the three named secrets to the protected job. [Environment secrets in reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)

For a release managed by the chain, its immutable pre-merge receipt supplies the candidate run ID. For a release without that receipt, the release administrator records its independently approved first-attempt CI run ID in the repository variable `HEINZEL_MAC_CANDIDATE_RUN` before release. The unprivileged admission verifies the unique merged release PR, matching candidate and squash trees and parents, the complete original CI run and tested checkout, and completion before merge. Reused archives must come from that approved run and match their GitHub ZIP digests. Rebuilds still require the candidate receipt. Before approving the environment, match the candidate to the tag and the successful promotion or fallback builds. Retain the signer job summary with the notary submission ID, archive SHA-256 and extracted host/widget checks. After publication, compare the attested archive hash and exercise the installed widget against the same app, including saved Watch with networking disconnected.

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
app declares that mode only for its iOS app target, alongside `audio` for background playback.
The native stream can continue while the app is backgrounded;
the visualizer draws when visible. Background survival and signal recovery still require physical-device
acceptance. Microphone capture still requires its existing permission. Before uploading an archive,
verify the resolved app plist contains exactly `UIBackgroundModes = [screen-capture, audio]` and the widget declares no
background modes; preserve enrollment, signed groups and the exact build/source checks.
Both Mac and iOS app/widget source plists map `HeinzelSourceRevision` to `$(HEINZEL_SOURCE_REVISION)`
so paired artifacts can be checked against the same source commit.

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

### TestFlight upload

Every Heinzel release ships the iOS app at the same version: Heinzel 0.9.54 is iOS 0.9.54. The release tooling
archives the release tag with `MARKETING_VERSION` set to the version in `pyproject.toml`,
`HEINZEL_SOURCE_REVISION` set to the tagged commit, and `CURRENT_PROJECT_VERSION` set to one above the highest build
App Store Connect holds for the app. Because `ExportOptions-testflight.plist` sets
`manageAppVersionAndBuildNumber` to false, the uploaded build keeps the numbers of the verified archive.

To build by hand, build the signed iOS archive with the intended build number and set
`HEINZEL_SOURCE_REVISION` to the full source commit. The app and widget extension
must both contain those exact values. Use an App Store export options plist
with `destination` set to `upload` and `manageAppVersionAndBuildNumber` set to
Boolean false.

Run the checked uploader from the repository root:
```sh
python3 apple/scripts/widget_configuration.py upload ARCHIVE EXPORT_OPTIONS EXPORT_PATH \
  --build BUILD --revision SOURCE_COMMIT
```

The command verifies app-only background capture modes and the archive's signatures, enrollment configuration, signed
groups, build numbers, and source revisions before it invokes Xcode's upload.
Xcode may re-sign during export. Use its
`archive ARCHIVE --build BUILD --revision SOURCE_COMMIT` mode for verification
without uploading. Device and App Store acceptance are separate checks.

## TestFlight without an Apple ID session

`apple/scripts/testflight` talks to App Store Connect with an API key (ASC_KEY_ID, ASC_ISSUER_ID and
ASC_KEY_PATH from `~/.appstoreconnect/heinzel-mini.env`, override with `ASC_ENV_FILE`; the key is never printed).

- `testflight upload X.xcarchive` runs `xcodebuild -exportArchive` with `distribution/ExportOptions-testflight.plist`
  (`destination=upload`) and the `-authenticationKey*` flags, so no Xcode sign-in is needed.
- `testflight status [BUILD]` lists processing state and group membership of "Heinzel Internal Auto".
- That internal group has automatic distribution ON (set once in the web UI; Apple refuses it over the API,
  `hasAccessToAllBuilds` cannot be updated), so every processed build reaches the testers by itself.
- `testflight assign [BUILD]` is therefore optional: it adds a processed build the group does not hold yet.
- `testflight beta BUILD` prints the build's internal beta state (`IN_BETA_TESTING` once testers can install it).
- `testflight notes BUILD FILE` sets the build's "What to Test" text (en-US) from FILE; the release tooling uses the
  GitHub release notes of the tag.
- Export compliance needs no step: both Info.plists declare `ITSAppUsesNonExemptEncryption` false.
- `testflight setup` only reports the group's `hasAccessToAllBuilds` state.
