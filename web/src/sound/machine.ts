// The sound's rules as pure functions (ADR 0058): how loud it plays given what the person does, and how loud each layer
// of a layered soundscape is given the signals. No audio and no timers here, so every rule is a plain test.

/** Typing in the chat or recording voice plays sound at this share of its volume. */
export const DUCK = 0.25;
/** Typing ducks until this long after the last key. */
export const DUCK_RELEASE_MS = 1500;
/** A duck, and its release, ramps this fast; every other change fades over the soundscape's `fade_seconds`. */
export const DUCK_SECONDS = 0.3;

export type Inputs = {
  /** Whether the person pressed Play (and not Pause since). */
  playing: boolean;
  /** 0 to 100, `[system.sound] volume`. */
  volume: number;
  typing: boolean;
  /** Voice input records: always ducks. */
  voice: boolean;
  duckWhileTyping: boolean;
  /** Whether a meeting is in progress: null when unknown (no calendar), which never mutes. */
  meeting: boolean | null;
  muteInMeetings: boolean;
};

export type Why = "paused" | "meeting" | "ducked" | "playing";

/** Why the sound is as loud as it is: paused, muted for a meeting, ducked, or playing. */
export function why(inputs: Inputs): Why {
  if (!inputs.playing) return "paused";
  if (inputs.muteInMeetings && inputs.meeting === true) return "meeting";
  if (inputs.voice || (inputs.duckWhileTyping && inputs.typing)) return "ducked";
  return "playing";
}

/** The gain to fade to, 0 to 1. */
export function targetGain(inputs: Inputs): number {
  const volume = Math.min(100, Math.max(0, inputs.volume)) / 100;
  switch (why(inputs)) {
    case "paused":
    case "meeting":
      return 0;
    case "ducked":
      return volume * DUCK;
    case "playing":
      return volume;
  }
}

/** How long the change from `before` to `after` takes: a duck and its release are quick, the rest fades. */
export function rampSeconds(before: Why, after: Why, fadeSeconds: number): number {
  return before === "ducked" || after === "ducked" ? DUCK_SECONDS : fadeSeconds;
}

export type Curve = number[][] | Record<string, number> | null | undefined;
export type Layer = { gain: number; signal: string; curve?: Curve };
export type Signals = Record<string, string | number>;

/** A layer's gain now: its `gain`, shaped by the signal it follows; a signal nobody publishes leaves it at `gain`. */
export function layerGain(layer: Layer, signals: Signals): number {
  if (layer.signal === "none" || !layer.curve) return layer.gain;
  const value = signals[layer.signal];
  if (value === undefined) return layer.gain;
  if (Array.isArray(layer.curve))
    return typeof value === "number" ? layer.gain * along(layer.curve, value) : layer.gain;
  return typeof value === "string" ? layer.gain * (layer.curve[value] ?? 0) : layer.gain;
}

/** The curve's y at `x`, piecewise linear between its points, flat before the first and after the last. */
export function along(points: number[][], x: number): number {
  const [first, last] = [points[0], points[points.length - 1]];
  if (!first || !last) return 1;
  if (x <= (first[0] ?? 0)) return first[1] ?? 0;
  if (x >= (last[0] ?? 1)) return last[1] ?? 0;
  for (let index = 1; index < points.length; index++) {
    const [x0 = 0, y0 = 0] = points[index - 1] ?? [];
    const [x1 = 1, y1 = 0] = points[index] ?? [];
    if (x <= x1) return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0 || 1);
  }
  return last[1] ?? 0;
}

/** Typing ducks the sound until `release` ms after the last key; `onChange` hears each start and end. */
export class Ducker {
  private timer: ReturnType<typeof setTimeout> | undefined;
  active = false;

  constructor(
    private readonly onChange: (active: boolean) => void,
    private readonly release = DUCK_RELEASE_MS,
  ) {}

  key(): void {
    if (!this.active) {
      this.active = true;
      this.onChange(true);
    }
    clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.active = false;
      this.onChange(false);
    }, this.release);
  }

  stop(): void {
    clearTimeout(this.timer);
    if (this.active) {
      this.active = false;
      this.onChange(false);
    }
  }
}
