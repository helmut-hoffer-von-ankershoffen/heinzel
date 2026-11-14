---
status: proposed
date: 2026-11-15
implementation: partial
decision-makers: maintainer
amends: 0135, 0140
amends-0135: add Swift as a managed Mac flavor and promote it after acceptance
amends-0140: support the Browser panel in the Apple WebKit shell
related: 0003, 0129
---

# 0147: Use a Swift shell for Apple workspaces

## Context and Problem Statement

Electron provides the desktop workspace, including native window controls, an integrated Browser panel and audio
capture. The maintainer wants that workspace on Mac, iPhone and iPad, with compact mobile chrome, paper-style
startup screens, and links that can open the app from Messages. The Mac must be able to run its own Heinzel service.
The mobile app connects to a Heinzel host selected by the user.

## Considered Options

- **One SwiftUI and WebKit app in the core repository** (chosen). Each platform uses the same web workspace and
  operations, with native code for services supplied by the operating system.
- **Electron on Mac and mobile browser access**. This provides the existing workspace on mobile, but lacks the
  mobile app's native routing, window integration and capture controls.
- **A separate Apple repository**. This would require coordinated revisions to the workspace assets, shell bridge,
  install tooling and release artifacts on either side of the same interface.

## Decision Outcome

The Swift shell lives in the same repository as the workspace it displays. macOS starts an owned foreground `hz`
process. iPhone and iPad connect to an existing private HTTPS workspace. All three use the same web application
and authenticated host operations.

- Keep the Electron title-bar layout and workspace commands on Mac. Give the mobile workspace the available display
  area with safe areas and compact controls. Use the same paper-style artwork for native startup on all three platforms.
- Reuse the Browser panel's isolation policy. Swift uses a separate nonpersistent WebKit guest behind an authenticated
  loopback proxy that resolves public HTTPS destinations. The workspace keeps its own authenticated WebKit session.
- A shared HTTPS link names the workspace origin in its fragment. The public domain publishes the signed app's
  association file for the launch route. An explicit confirmation gates switching workspaces, and a new device pairs
  through a separate private sign-in link. The HTTPS launch page can hand the address to the registered `heinzel:`
  scheme. Builds with HTTPS links enabled share the public launch URL. See the [Apple build instructions](../../apple/README.md).
- Ask for microphone access when the workspace enables capture. Mac system audio uses a private Core Audio tap.
  The mobile shell uses the system capture picker where its SDK and device support it. The simulator does not qualify
  mobile system audio.
- Expose `--flavor=electron` and `--flavor=swift` for the managed Mac application. Keep Swift opt-in until the managed
  install/update, workspace features and supported native services pass acceptance. Set Swift as the default after that.

### Consequences

The web workspace and host operations stay in one codebase. The Apple shell has its own packaging, OS permissions and
signed-device acceptance. Messages links also depend on the deployed domain association matching the app's signed identity.

### Confirmation

Run managed installer tests for flavor selection, verified artifacts and replacement. Verify native Browser isolation,
Mac title-bar interactions, trusted external link handoffs, native notices and the managed runtime lifecycle in the
Apple app. Compare the usable workspace area on iPhone and iPad with Safari on the same device and orientation.
Require at least the same usable area. Test a shared link from Messages on signed iPhone and iPad installations against
the deployed association, and exercise microphone and system audio on physical devices.
