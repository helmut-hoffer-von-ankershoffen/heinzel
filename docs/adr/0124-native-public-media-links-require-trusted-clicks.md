---
status: proposed
date: 2026-11-04
implementation: built
implemented-by: 319
related: 0123, 0092
---

# 0124: Native public media links require trusted clicks

## Context and Problem Statement

The native window refuses all new windows and cross-origin navigation. Public screenshot and soundscape anchors
therefore cannot open their declared media. Enabling popups or arbitrary external URLs would widen the shell's
security boundary beyond the media preview.

## Considered Options

- **Hand off validated public media after a trusted link click** (chosen).
- Allow new windows: introduces privileged document and navigation surfaces that the preview does not need.
- Leave external anchors inert: the full-size and soundscape actions would not work in the native app.

## Decision Outcome

Chosen option: **a narrow public-media IPC handoff**, because the operating system browser can display the media
while the app retains its owned-origin navigation and unconditional new-window refusal.

The isolated preload listens only for trusted primary clicks, including keyboard activation, on marked public-media
anchors while transient user activation is active. The page receives no callable external-opening API. Scripted
clicks cannot invoke the handoff. Only the isolated preload invokes one fixed IPC channel with the selected URL.

The main process independently requires a live shell-owned window and its current main frame on the current owned
service origin. It accepts bounded canonical HTTPS URLs without credentials, ports, fragments or unrelated query
parameters. Destinations are immutable raw.githubusercontent.com PNG/JPG/WebP paths and exact www.youtube.com watch
links with one checked video identity. Local destinations, other hosts, unsafe schemes and escaped paths fail closed.

Only accepted URLs reach the OS browser opener, without application credentials or referrer data. Opening failures
produce a visible message beside the link. No player or external request starts merely by opening the gallery.
Top-level navigation, redirects and window creation keep their existing refusal rules.

### Consequences

This supports public media without a concrete plugin dependency or a general-purpose renderer-to-shell command.
The allowlist deliberately excludes arbitrary websites and media providers. The browser opened by the OS may use
its own signed-in state; the native app sends no session bearer. An opener success means a handoff was accepted,
not that a remote site loaded or media played.

### Confirmation

Unit tests reject invalid destinations, foreign windows, child frames, stale documents and inactive service state.
A real isolated Electron fixture stubs the OS opener and proves deliberate screenshot and YouTube clicks reach it,
scripted clicks do not, and existing cross-origin navigation, redirects and popups remain denied. Web tests verify
warnings remain visible with useful screenshots and native handoff failures produce an actionable message.
