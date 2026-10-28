---
status: proposed
date: 2026-10-28
amends: 0058
---

# 0092: YouTube sign-in is an explicit player choice

## Context and Problem Statement

YouTube can require sign-in to confirm a viewer is not a bot. Signing in on youtube.com does not reliably unblock
the privacy-enhanced player on youtube-nocookie.com. Browser cookie restrictions can also keep an embedded player
from using a session that works in a browser tab. The current player has no recovery controls for either case.

## Considered Options

- **Keep the private player by default and offer the regular YouTube player explicitly** (chosen).
- Always use the regular player: changes the privacy default for everyone.
- Only link to YouTube: provides playback, but gives up embedded playback where the regular player works.

## Decision Outcome

Chosen option: **an explicit regular-player choice**, because it offers embedded sign-in without changing the default.

- Keep youtube-nocookie.com until the viewer chooses “Use YouTube sign-in”. Remember the choice in this browser;
  offer “Use private player” to reverse it. No frame loads before Play, including with a remembered choice.
- Admit both fixed YouTube frame origins in the page CSP only while a YouTube stream is in effect. No plugin or
  configuration can supply an alternative origin. Commands and events use only the currently mounted frame's origin.
- Let the regular player's sandbox request storage access after a user gesture. The browser still decides access;
  the app cannot read or copy YouTube cookies, nor promise sign-in will overcome a managed browser policy.
- Offer playback help, a retry that recreates the player, and “Play on YouTube”. Opening the latter stops the local
  player to avoid duplicate audio. Playback there is controlled by YouTube.
- Expand help on player errors, blocked autoplay, or 15 seconds without confirmed playback. A ready frame alone
  does not confirm playback. Do not infer or display the contents of the cross-origin player.

### Consequences

- Good: viewers can try their existing YouTube session and have an exit from the embedded sign-in loop.
- Bad: choosing the regular player allows YouTube to associate playback with the viewer's account, when browser
  policy permits. Corporate restrictions may still require playback on YouTube itself.

### Confirmation

`web/src/sound/stream.test.ts`, `web/src/sound/StreamPlayer.test.tsx`, `tests/integration/sound_test.py` and the
mobile browser sweep verify the origin checks, private default, explicit choice, recovery and restricted CSP.

## More Information

- [YouTube embedding and privacy-enhanced mode](https://support.google.com/youtube/answer/171780?hl=en).
- [Storage access in sandboxed frames](https://developer.mozilla.org/en-US/docs/Web/API/Storage_Access_API/Using).
