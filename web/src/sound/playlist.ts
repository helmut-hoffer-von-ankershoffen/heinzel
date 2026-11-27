// Native tracks in one plain audio element (ADR 0166). It is deliberately outside React and Web Audio: with the screen
// locked, the next track must start inside the element's own `ended` event (no render, no timer), and iOS suspends
// what is routed through an AudioContext. Media Session brings the lock screen's controls to the same methods.

export type Track = { title: string; url: string; type: string; duration_seconds: number };
export type Status = { index: number; problem: "failed" | "blocked" | null };
export type Hooks = {
  changed: (status: Status) => void;
  /** Playback stopped on its own or from the system's controls. */
  paused: () => void;
  /** The person started playback from the system's controls or by changing the track. */
  resumed: () => void;
};

/** Previous restarts a track that is further in than this. */
const RESTART_SECONDS = 3;
const STEP_MS = 100;
const ACTIONS = ["play", "pause", "nexttrack", "previoustrack"] as const;

export class Playlist {
  private index = 0;
  private problem: Status["problem"] = null;
  /** Whether it should sound: tells a pause of the system's from our own. */
  private wanted = false;
  /** The system paused it, and may resume it by itself (after a call). */
  private away = false;
  private fading: ReturnType<typeof setInterval> | undefined;
  private readonly listening = new AbortController();

  constructor(
    private readonly audio: HTMLAudioElement,
    private readonly album: string,
    private readonly tracks: readonly Track[],
    private readonly hooks: Hooks,
    /** iOS ignores `volume`: there the device's buttons set it, and only muting is ours. */
    private readonly deviceVolume: boolean,
  ) {
    const { signal } = this.listening;
    audio.preload = "none";
    if (!deviceVolume) audio.volume = 0; // the fade decides what is heard
    audio.addEventListener("ended", () => this.ended(), { signal });
    audio.addEventListener("pause", () => this.interrupted(), { signal });
    audio.addEventListener("play", () => this.returned(), { signal });
    audio.addEventListener("playing", () => this.publish("playing"), { signal });
    audio.addEventListener("error", () => this.fail("failed"), { signal });
    document.addEventListener("securitypolicyviolation", (event) => this.violated(event), { signal });
    this.load(0);
    this.controls();
  }

  /** Follow the engine: how loud, how fast it gets there, and whether it plays. */
  level(gain: number, seconds: number, playing: boolean): void {
    clearInterval(this.fading);
    // Only while it plays: iOS may take muted media off the lock screen, and with it the way to press Play.
    if (this.deviceVolume && playing) this.audio.muted = gain === 0;
    if (playing) this.play();
    else this.wanted = false;
    if (this.deviceVolume || (!playing && this.audio.paused)) {
      if (this.deviceVolume) {
        // Not when it is paused already: after the system's pause, halting would forget that it may resume.
        if (!playing && !this.audio.paused) this.halt();
      } else this.audio.volume = 0;
      return;
    }
    const from = this.audio.volume;
    const began = Date.now();
    // By the clock, not by counting steps: a hidden page's timers run about once a second.
    this.fading = setInterval(() => {
      const share = seconds > 0 ? Math.min(1, (Date.now() - began) / (seconds * 1000)) : 1;
      this.audio.volume = Math.min(1, Math.max(0, from + (gain - from) * share));
      if (share < 1) return;
      clearInterval(this.fading);
      if (!playing) this.halt();
    }, STEP_MS);
  }

  next(): void {
    this.load(this.index + 1);
    this.start();
  }

  previous(): void {
    if (this.audio.currentTime > RESTART_SECONDS) this.audio.currentTime = 0;
    else this.load(this.index - 1);
    this.start();
  }

  /** Load the track again where it stopped, and play. */
  retry(): void {
    this.load(this.index, this.audio.currentTime);
    this.start();
  }

  dispose(): void {
    this.listening.abort();
    clearInterval(this.fading);
    this.wanted = false;
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load(); // lets the connection go
    const session = navigator.mediaSession;
    if (!session) return;
    for (const action of ACTIONS) handle(session, action, null);
    session.metadata = null;
    session.playbackState = "none";
  }

  private start(): void {
    if (this.play()) this.hooks.resumed();
  }

  /** Whether it set out to play: not while the page's policy refuses the track, which only a reload changes. */
  private play(): boolean {
    this.away = false;
    if (this.problem === "blocked") {
      this.halt();
      this.hooks.paused();
      return false;
    }
    this.wanted = true;
    if (this.problem === "failed") this.load(this.index, this.audio.currentTime);
    if (!this.audio.paused) return true;
    this.audio.play().catch((error: unknown) => {
      // NotAllowedError: no gesture (after a reload). A source that won't load reports through `error` instead.
      if (!(error instanceof Error) || error.name !== "NotAllowedError" || !this.wanted) return;
      this.halt();
      this.hooks.paused();
    });
    return true;
  }

  private halt(): void {
    this.wanted = false;
    this.away = false;
    this.audio.pause();
    this.publish("paused");
  }

  private ended(): void {
    this.load(this.index + 1);
    if (this.wanted) this.play();
  }

  /** The system paused it: a call, headphones pulled out, another app's sound. */
  private interrupted(): void {
    // Also sent at a track's end and for a pause that Play has already undone.
    if (!this.wanted || !this.audio.paused || this.audio.ended || this.audio.error) return;
    this.halt();
    this.away = true;
    this.hooks.paused();
  }

  /** The system resumed what it had paused: the app follows, so it is heard and the next track follows it. */
  private returned(): void {
    if (this.away && !this.audio.paused) this.start();
  }

  private fail(problem: NonNullable<Status["problem"]>): void {
    if (this.problem === "blocked") return; // the policy's refusal also fires `error`; it is the more useful reason
    this.problem = problem;
    this.halt();
    this.changed();
    this.hooks.paused();
  }

  private violated(event: SecurityPolicyViolationEvent): void {
    const track = this.tracks[this.index];
    if (event.effectiveDirective !== "media-src" || !track) return;
    const { origin } = new URL(track.url);
    if (!event.blockedURI.startsWith(origin)) return;
    // A policy that admits the origin refused a redirect off it, and no reload helps that.
    this.fail(event.originalPolicy?.includes(origin) ? "failed" : "blocked");
  }

  private load(index: number, at = 0): void {
    this.index = (index + this.tracks.length) % this.tracks.length;
    const track = this.tracks[this.index];
    if (!track) return;
    this.problem = null;
    this.audio.src = track.url;
    if (at > 0) this.audio.currentTime = at;
    if (navigator.mediaSession && "MediaMetadata" in window)
      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        album: this.album,
        artist: this.album,
        artwork: [{ src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" }],
      });
    this.changed();
  }

  private changed(): void {
    this.hooks.changed({ index: this.index, problem: this.problem });
  }

  private publish(state: MediaSessionPlaybackState): void {
    if (navigator.mediaSession) navigator.mediaSession.playbackState = state;
  }

  private controls(): void {
    const session = navigator.mediaSession;
    if (!session) return;
    handle(session, "play", () => this.start());
    handle(session, "pause", () => {
      this.halt();
      this.hooks.paused();
    });
    handle(session, "nexttrack", () => this.next());
    handle(session, "previoustrack", () => this.previous());
  }
}

function handle(session: MediaSession, action: MediaSessionAction, handler: MediaSessionActionHandler | null): void {
  try {
    session.setActionHandler(action, handler);
  } catch {
    // an older browser without this action: its own controls still play and pause
  }
}
