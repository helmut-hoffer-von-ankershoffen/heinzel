// A short, local-only cue for genuinely new notices; independent of the background soundscape.
const PREFERENCE = "hz.notification-sound";
const PLAYED = "hz.notification-sound.played";
const MAX_PLAYED = 200;

let context: AudioContext | null = null;
const seenThisTab = new Set<string>();

function audioContextConstructor(): typeof AudioContext | undefined {
  return typeof window !== "undefined" ? window.AudioContext : undefined;
}

function readPlayed(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(PLAYED) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function writePlayed(ids: string[]): void {
  try {
    localStorage.setItem(PLAYED, JSON.stringify(ids.slice(-MAX_PLAYED)));
  } catch {
    // Private browsing or a full storage quota must not break notifications.
  }
}

export function notificationSoundAvailable(): boolean {
  return audioContextConstructor() !== undefined;
}

export function notificationSoundEnabled(): boolean {
  try {
    return localStorage.getItem(PREFERENCE) === "on";
  } catch {
    return false;
  }
}

/** Resume audio only in response to a deliberate user gesture; the caller may persist ON after this succeeds. */
export async function primeNotificationSound(): Promise<boolean> {
  const AudioContextConstructor = audioContextConstructor();
  if (!AudioContextConstructor) return false;
  try {
    context ??= new AudioContextConstructor();
    if (context.state !== "running") await context.resume();
    return context.state === "running";
  } catch {
    return false;
  }
}

export async function setNotificationSoundEnabled(enabled: boolean): Promise<boolean> {
  if (enabled && !(await primeNotificationSound())) return false;
  try {
    localStorage.setItem(PREFERENCE, enabled ? "on" : "off");
    return true;
  } catch {
    return false;
  }
}

function playDing(): boolean {
  if (context?.state !== "running") return false;
  try {
    const now = context.currentTime;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(880, now);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.12, now + 0.018);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.24);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(now);
    oscillator.stop(now + 0.25);
    return true;
  } catch {
    return false;
  }
}

async function claimNotice(id: string): Promise<boolean> {
  const claim = () => {
    const played = readPlayed();
    if (played.includes(id)) return false;
    played.push(id);
    writePlayed(played);
    return true;
  };
  // Web Locks serialize simultaneous events from open tabs. Older browsers still get a persistent seen list.
  if (navigator.locks) {
    return navigator.locks.request(`hz-notification-sound:${id}`, { mode: "exclusive" }, claim);
  }
  return claim();
}

/** A list refresh is not a new notice; call only for the stream's `notice` event. */
export async function dingForNotice(id: string): Promise<boolean> {
  if (seenThisTab.has(id)) return false;
  seenThisTab.add(id);
  if (!notificationSoundEnabled() || document.visibilityState !== "visible") return false;
  try {
    if (!(await claimNotice(id))) return false;
  } catch {
    return false;
  }
  // Consume the ID even when browser autoplay policy suspended audio: reconnects must never replay an old notice.
  return playDing();
}

/** The stream's changed event refreshes history; only its due notice event can produce a sound. */
export function dingForStreamEvent(
  event: { event: "changed"; latest: string | null } | { event: "notice"; notice: { id: string } },
): Promise<boolean> {
  return event.event === "notice" ? dingForNotice(event.notice.id) : Promise.resolve(false);
}
