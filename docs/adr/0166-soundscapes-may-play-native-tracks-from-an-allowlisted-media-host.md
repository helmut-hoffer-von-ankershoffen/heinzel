---
status: proposed
date: 2026-11-22
implementation: partial
decision-makers: maintainer
amends: 0027, 0039, 0046, 0058, 0092
amends-0027: tracks load from an allowlisted project media host, only from Play onwards
amends-0039: one media-src origin on the page while tracks are in effect
amends-0046: one media-src origin on the page while tracks are in effect
amends-0058: ordered tracks by https URL on an allowlisted host, played by an audio element
amends-0092: with tracks, Play mounts no YouTube frame until the person picks it
related: 0026, 0094, 0095, 0115
---

# 0166: Soundscapes may play native tracks from an allowlisted media host

## Context and Problem Statement

A stream soundscape is one YouTube video in YouTube's player ([ADR 0058](0058-soundscapes-are-data-a-plugin-ships-chosen-like-a-theme.md)).
That needs a visible frame, contacts YouTube, can ask for a sign-in
([ADR 0092](0092-youtube-sign-in-is-an-explicit-player-choice.md)) and has no playback of its own on a locked phone.
The maintainer wants background music for work to play inside hz as well as on YouTube, and proposed hosting it in a
public website repository on GitHub with Cloudflare in front. How does an entry carry audio, and where should that
audio live?

## Decision Drivers

- Nothing loads before Play, nothing tracks the listener, and no plugin or project file chooses a host.
- YouTube keeps working; a phone keeps playing with the screen locked where its browser allows.
- The host permits audio, answers range requests, costs little, and lets a published file be withdrawn.

## Considered Options

How an entry plays:

- **Ordered tracks on a `stream` soundscape, beside or instead of `[stream]`, from hosts listed in code** (chosen)
- **A new kind `audio`**: an entry could not gain tracks without changing its kind, and not stay as it is with the
  feature off.
- **A host from the plugin or a setting**: a plugin-selected CSP exception, which ADR 0058 and ADR 0094 refuse.
- **Proxy or ship the audio in hz**: every track would travel through the person's computer, or exceed ADR 0058's
  8 MB for a soundscape.
- **Mix tracks with Web Audio**: needs CORS and `connect-src`, and loses background playback on iOS.

Where it lives:

- **Cloudflare R2 on `media.heinzel.team`** (chosen, recommended to the maintainer)
- **GitHub Pages of the site's repository behind Cloudflare's proxy** (the option the maintainer proposed):
  the CDN's terms let Cloudflare limit a Free, Pro or Business zone that serves a disproportionate share of audio
  without a paid service such as R2, and committed audio stays in public git history.
- **GitHub Pages alone**: allowed and free, but a soft 100 GB a month, and the same git history.
- **GitHub Releases**: no bandwidth limit, but an asset redirects to a second host and arrives as an
  `application/octet-stream` attachment.

## Decision Outcome

Chosen option: **ordered tracks from hosts listed in code, hosted on R2 behind `media.heinzel.team`**, because it keeps
ADR 0058's trust rule and uses the product Cloudflare's terms name for serving large files.

**The entry and the player**

- A `stream` soundscape has a `[stream]`, 1 to 50 ordered `[[tracks]]`, or both. A track has a `title`, a `url`, a
  `type` (`audio/mpeg` for `.mp3`, `audio/mp4` for `.m4a`) and `duration_seconds`.
- The URL is data; its origin is not. It is HTTPS on an origin of `MEDIA_ORIGINS` in the kernel, with a plain path and
  no credentials, port, query, fragment or escape. The list names a host, never a plugin: any plugin may point at it.
  It is content-neutral code with one project-specific value, the price of not letting data widen the CSP.
- The page gets `media-src` with that origin only while such an entry is in effect, beside `frame-src` when it also
  has a stream. `connect-src` never widens. A redirect to another origin is refused by the CSP.
- Before Play there is no audio element and no request, and the next track is not preloaded. The element sets no
  `crossorigin`: the page's `no-referrer` applies and no `Origin` is sent, which would name a person's private address.
  The price is that a browser may return cookies the host set itself, so the host sets none.
- Tracks play in order and repeat. Next starts inside `ended`, without a render or timer. Media Session offers play,
  pause, previous and next, with hz's own icon as artwork.
- Volume fades, ducking and meeting mute are those of streams. On iPhone and iPad the device owns the volume
  ([Apple](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/Using_HTML5_Audio_Video/Device-SpecificConsiderations/Device-SpecificConsiderations.html)),
  so meetings and voice input mute. The visualizer treats tracks like a frame: it can't tap them.
- With both sources, tracks play and "Play from YouTube instead" is the explicit fallback.
- The flag `native_audio` ([ADR 0115](0115-merge-unfinished-work-dark-behind-named-feature-flags.md)) keeps this dark.
  It is read from user, site and environment settings, never a project's `hz.toml`. Off, an entry with both is the
  YouTube stream, one with only tracks is not offered, and the CSP is unchanged. Only the core's own sound service
  can open a media origin.
- Public API, additive: `SoundTrack`, `Soundscape.tracks`, `SoundAssets.media_origins()` (none by default) and
  `heinzel.kernel.MEDIA_ORIGINS`.

**Hosting, checked on this record's date**

Recommendation: **R2, Cloudflare's object storage, on `media.heinzel.team`**. Pages behind Cloudflare would work
technically, but Cloudflare may limit a site that serves mostly audio that way, and committed files stay in public
history. R2 costs nothing at this scale: two hours of music is 0.17 GB at 192 kbit/s, and the free tier is 10 GB. An
eight-hour day of listening transfers 0.69 GB.

| | Pages behind Cloudflare | Releases | R2 custom domain |
| --- | --- | --- | --- |
| Size | [site 1 GB][pages], [file 100 MiB][large] | [file under 2 GiB][releases] | [object about 5 TiB][limits] |
| Bandwidth | [soft 100 GB a month][pages] at GitHub, about 144 listening days, before cache hits | [no limit][releases], subject to [fair use][aup] | [free egress][price] |
| Terms | [audio through the CDN may be limited][cdn] | [distributing binaries is its purpose][large] | [Developer Platform, named for large files][cdn] |
| Range requests | yes: `206` observed on Pages, [and from Cloudflare's cache][range] | `206` observed after the redirect | [yes][range] |
| CORS | `*` observed on Pages | none observed | [set per bucket][cors]; this player needs none |
| Cost | free | free | [free to 10 GB and 10 million reads a month][price], then $0.015 per GB-month and $0.36 per million reads; [needs a subscription checkout][start] |
| Masters or encodes in public git history | yes, if committed; removal is a history rewrite | no | no |

- **Certificate.** Cloudflare's free certificate, [Universal SSL][ssl], covers `heinzel.team` and one level
  below. A TLS handshake observed for `media.heinzel.team` got a `*.heinzel.team` certificate; a second-level name
  failed. Use the first level; the second needs paid Advanced Certificate Manager.
- **DNS today.** A proxied wildcard answers every name below `heinzel.team` and redirects to the apex, so the media
  hostname is not provisioned yet. It needs its own record and an exception from that redirect, which hz's CSP
  refuses, as the WebKit browser test showed.
- **Trackers.** The zone sends [Network Error Logging][nel] headers today, which report visitors' failed requests to
  Cloudflare. Switch that off before launch; set no cookies and add no analytics script or bot cookie.
- **Files.** Upload delivery encodes only, never masters, under content-versioned names with a long cache lifetime.
  MP3 is [cached by default][cache]; M4A needs a cache rule. Turn `r2.dev` off. Deleting a file also needs a cache
  purge, and cannot recall copies: whatever plays in a browser can be downloaded.

**Left to the maintainer**

Before the flag is removed: approve R2 and its checkout, the hostname and the DNS change; make "no cookies on that
host or its parent domain, no Network Error Logging" true there and complete the privacy notice; confirm the right to
publish and publish the encodes; listen on a locked physical iPhone, through a call too. This decision creates no
infrastructure and no content.

### Consequences

- Good: music without a frame or YouTube, in order, with the system's media controls.
- Bad: the project's host, through Cloudflare, receives the IP address, browser or app version, language, tracks and
  byte ranges with their times (so roughly what was listened to, and when) of whoever presses Play. This is the first
  media hz fetches from the project itself. The picker names the host; SECURITY.md and the privacy notice say what it
  receives.
- Bad: another media host needs a core change and review.
- Bad: a flag can't hide schema. A core older than this rejects a file with `tracks`, so such a plugin needs a floor on
  its core version.
- Bad: background playback depends on the platform. WebKit still lists next track on a locked home-screen app
  ([261858](https://bugs.webkit.org/show_bug.cgi?id=261858)) and playing after reopening one
  ([295518](https://bugs.webkit.org/show_bug.cgi?id=295518)) as open. Emulation proves neither. A native iPhone shell
  also needs its own background-audio mode.
- Bad: a locked phone may not refresh the calendar, so meeting mute can follow late there.

### Confirmation

Model, integration and component tests fail when a URL leaves the allowlist, the CSP widens with the flag off or for a
project's file, or anything loads before Play. The browser tests play a generated tone through the real CSP in
Chromium and WebKit, at phone size too; lock-screen playback is checked by hand only.

## More Information

Revisit the host if the [CDN terms][cdn] or [R2 pricing][price] change. The header observations were read-only
requests to public GitHub and heinzel.team addresses on this record's date.

[pages]: https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
[large]: https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github
[releases]: https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
[aup]: https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies
[cdn]: https://www.cloudflare.com/service-specific-terms-application-services/
[price]: https://developers.cloudflare.com/r2/pricing/
[limits]: https://developers.cloudflare.com/r2/platform/limits/
[start]: https://developers.cloudflare.com/r2/get-started/
[cors]: https://developers.cloudflare.com/r2/buckets/cors/
[range]: https://developers.cloudflare.com/cache/reference/range-requests/
[cache]: https://developers.cloudflare.com/cache/concepts/default-cache-behavior/
[ssl]: https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/
[nel]: https://developers.cloudflare.com/network-error-logging/
