/**
 * The maths and drawing of the audio aurora: soft ribbons, a beat bloom and drifting sparks, all driven by the real
 * analyser signal. Everything here is pure or takes a canvas context, so it can be tested without WebAudio.
 */

export const BANDS = 48;
const MAX_MOTES = 72;
export const FRAME_MS = 1000 / 30;
const RIBBON_POINTS = 73;
const MAX_PIXELS = 2_400_000;
const MAX_RATIO = 1.5;

export interface Rgb {
  readonly r: number;
  readonly g: number;
  readonly b: number;
}

export interface Palette {
  readonly low: Rgb;
  readonly mid: Rgb;
  readonly high: Rgb;
  readonly spark: Rgb;
  readonly dark: boolean;
}

export interface Mote {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  age: number;
  life: number;
  phase: number;
  warm: boolean;
}

export interface AuroraState {
  levels: Float32Array;
  raw: Float32Array;
  floor: Float32Array;
  tops: Float32Array[];
  peaks: Float32Array;
  loud: number;
  pulse: number;
  avgBass: number;
  lastBeat: number;
  phase: number;
  motes: Mote[];
  seed: number;
}

export function spectrumLevel(value: number) {
  return Math.sqrt(Math.max(0, Math.min(255, value)) / 255);
}

export function parseColor(value: string, fallback: Rgb): Rgb {
  const text = value.trim().toLowerCase();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/.exec(text);
  if (hex?.[1]) {
    const digits = hex[1].length === 3 ? [...hex[1]].map((digit) => digit + digit).join("") : hex[1];
    return {
      r: Number.parseInt(digits.slice(0, 2), 16),
      g: Number.parseInt(digits.slice(2, 4), 16),
      b: Number.parseInt(digits.slice(4, 6), 16),
    };
  }
  const rgb = /^rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})/.exec(text);
  if (rgb) {
    const clamp = (part: string | undefined) => Math.max(0, Math.min(255, Number(part)));
    return { r: clamp(rgb[1]), g: clamp(rgb[2]), b: clamp(rgb[3]) };
  }
  return fallback;
}

export function rgba(color: Rgb, alpha: number) {
  const a = Math.max(0, Math.min(1, alpha));
  return `rgba(${color.r},${color.g},${color.b},${a.toFixed(3)})`;
}

const FALLBACK: Palette = {
  low: { r: 166, g: 64, b: 40 },
  mid: { r: 31, g: 116, b: 68 },
  high: { r: 138, g: 92, b: 0 },
  spark: { r: 224, g: 180, b: 95 },
  dark: false,
};

/** Reads the theme tokens, so light, dark and plugin themes all colour the aurora. */
export function readPalette(root: HTMLElement = document.documentElement): Palette {
  const style = getComputedStyle(root);
  const token = (name: string, fallback: Rgb) => parseColor(style.getPropertyValue(name), fallback);
  const dark = root.classList.contains("dark");
  return {
    low: token("--color-primary", FALLBACK.low),
    mid: token("--color-ok", FALLBACK.mid),
    high: token("--color-warn", FALLBACK.high),
    spark: dark ? token("--color-warn", FALLBACK.spark) : token("--color-primary", FALLBACK.low),
    dark,
  };
}

/** Log-spaced bin edges: low frequencies get fine bands, high ones share. Always strictly increasing, ending at `bins`. */
export function bandEdges(bins: number, bands = BANDS) {
  const edges = [1];
  for (let index = 1; index <= bands; index += 1) {
    const previous = edges[index - 1] ?? 1;
    const wanted = index === bands ? bins : Math.floor(bins ** (index / bands));
    edges.push(Math.min(bins, Math.max(previous + 1, wanted)));
  }
  edges[bands] = Math.max(bins, (edges[bands - 1] ?? 1) + 1);
  return edges;
}

/** Perceptual level per band from raw byte data (mean and max blended, so one hot bin shows but does not spike). */
export function bandLevels(data: ArrayLike<number>, edges: readonly number[], out: Float32Array) {
  for (let band = 0; band < out.length; band += 1) {
    const from = edges[band] ?? 1;
    const to = Math.min(data.length, Math.max(from + 1, edges[band + 1] ?? from + 1));
    let sum = 0;
    let max = 0;
    for (let index = from; index < to; index += 1) {
      const value = data[index] ?? 0;
      sum += value;
      if (value > max) max = value;
    }
    out[band] = spectrumLevel(0.5 * max + (0.5 * sum) / Math.max(1, to - from));
  }
  return out;
}

/** Frame-rate independent easing with a fast attack and a slower release. */
export function ease(current: number, target: number, dt: number, attack: number, release: number) {
  const rate = target > current ? attack : release;
  return current + (target - current) * (1 - Math.exp(-rate * dt));
}

/** Peak hold: a peak follows the level up at once and falls at `fall` per second. */
export function holdPeak(peak: number, level: number, dt: number, fall: number) {
  return Math.max(level, peak - fall * dt);
}

export function energy(levels: ArrayLike<number>, from: number, to: number) {
  let sum = 0;
  const end = Math.min(levels.length, to);
  for (let index = from; index < end; index += 1) sum += levels[index] ?? 0;
  return end > from ? sum / (end - from) : 0;
}

/** A beat is bass energy clearly above its running average, at most once per 180 ms. */
export function detectBeat(bass: number, average: number, lastBeat: number, now: number) {
  return bass > 0.22 && bass > average * 1.3 + 0.04 && now - lastBeat > 180;
}

/** Linear sample of the band levels at x in 0..1. */
export function sampleLevels(levels: ArrayLike<number>, x: number) {
  const position = Math.max(0, Math.min(1, x)) * (levels.length - 1);
  const index = Math.floor(position);
  const next = Math.min(levels.length - 1, index + 1);
  const fraction = position - index;
  return (levels[index] ?? 0) * (1 - fraction) + (levels[next] ?? 0) * fraction;
}

/** Vertical offset (in -1..1) of one ribbon at x in 0..1: two travelling waves shaped by the local spectrum. */
export function ribbonOffset(x: number, phase: number, ribbon: number, local: number) {
  const a = Math.sin(x * Math.PI * (2 + ribbon) + phase * (0.7 + 0.25 * ribbon) + ribbon * 2.1);
  const b = Math.sin(x * Math.PI * (5 + 2 * ribbon) - phase * (1.1 + 0.2 * ribbon) + ribbon);
  return (0.65 * a + 0.35 * b) * (0.25 + 0.75 * local);
}

/** Canvas pixel size for a viewport: the ratio is capped and the total pixel count bounded. */
export function canvasSize(width: number, height: number, devicePixelRatio: number) {
  let ratio = Math.max(1, Math.min(MAX_RATIO, devicePixelRatio || 1));
  const area = Math.max(1, width * height);
  if (area * ratio * ratio > MAX_PIXELS) ratio = Math.max(0.25, Math.sqrt(MAX_PIXELS / area));
  return { ratio, width: Math.max(1, Math.round(width * ratio)), height: Math.max(1, Math.round(height * ratio)) };
}

export function createState(seed = 1): AuroraState {
  return {
    levels: new Float32Array(BANDS),
    raw: new Float32Array(BANDS),
    floor: new Float32Array(BANDS),
    tops: [0, 1, 2].map(() => new Float32Array(RIBBON_POINTS)),
    peaks: new Float32Array(BANDS),
    loud: 0,
    pulse: 0,
    avgBass: 0,
    lastBeat: -Infinity,
    phase: 0,
    motes: [],
    seed,
  };
}

function random(state: AuroraState) {
  // mulberry32: deterministic, so tests and screenshots are reproducible.
  state.seed = (state.seed + 0x6d2b79f5) | 0;
  let t = Math.imul(state.seed ^ (state.seed >>> 15), 1 | state.seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

export function spawnMote(state: AuroraState, width: number, height: number, strength: number) {
  if (state.motes.length >= MAX_MOTES) return;
  state.motes.push({
    x: random(state) * width,
    y: height * (0.55 + random(state) * 0.45),
    vx: (random(state) - 0.5) * 14,
    vy: -(18 + random(state) * 34) * (0.6 + strength),
    size: 1.6 + random(state) * 2.6,
    age: 0,
    life: 2.4 + random(state) * 2.6,
    phase: random(state) * Math.PI * 2,
    warm: random(state) > 0.35,
  });
}

/** Advances the motes and drops the finished ones; returns the count still alive. */
export function stepMotes(state: AuroraState, dt: number) {
  let kept = 0;
  for (const mote of state.motes) {
    mote.age += dt;
    if (mote.age >= mote.life) continue;
    mote.x += (mote.vx + Math.sin(mote.age * 2.2 + mote.phase) * 9) * dt;
    mote.y += mote.vy * dt;
    state.motes[kept] = mote;
    kept += 1;
  }
  state.motes.length = kept;
  return kept;
}

export function moteAlpha(mote: Mote) {
  const t = mote.age / mote.life;
  const fade = t < 0.15 ? t / 0.15 : (1 - t) / 0.85;
  return Math.max(0, fade) * (0.7 + 0.3 * Math.sin(mote.age * 9 + mote.phase));
}

/**
 * Subtract a slowly tracked noise floor (room and microphone hiss) from band levels, so a quiet room stays dark and
 * only sound above it moves the aurora. The floor drops fast to a quieter reading and rises very slowly.
 */
export function subtractFloor(levels: Float32Array, floor: Float32Array, dt: number) {
  for (let band = 0; band < levels.length; band += 1) {
    const level = levels[band] ?? 0;
    const current = floor[band] ?? 0;
    const next = level < current ? ease(current, level, dt, 6, 6) : ease(current, level, dt, 0.08, 0.08);
    floor[band] = next;
    levels[band] = Math.max(0, (level - next * 1.15) / Math.max(0.2, 1 - next));
  }
  return levels;
}

export interface StepOptions {
  readonly reduced: boolean;
  /** Microphone input: take out the room's noise floor. */
  readonly floor?: boolean;
  readonly width: number;
  readonly height: number;
}

/** One analysis step: eases levels, holds peaks, finds beats and feeds the motes. Returns true on a beat. */
export function stepState(
  state: AuroraState,
  data: ArrayLike<number>,
  edges: readonly number[],
  dt: number,
  now: number,
  options: StepOptions,
) {
  const raw = bandLevels(data, edges, state.raw);
  if (options.floor) subtractFloor(raw, state.floor, dt);
  for (let band = 0; band < BANDS; band += 1) {
    const level = ease(
      state.levels[band] ?? 0,
      raw[band] ?? 0,
      dt,
      options.reduced ? 3 : 14,
      options.reduced ? 2 : 3.2,
    );
    state.levels[band] = level;
    state.peaks[band] = holdPeak(state.peaks[band] ?? 0, level, dt, 0.35);
  }
  const bass = energy(raw, 0, 10);
  const treble = energy(state.levels, 28, BANDS);
  state.loud = ease(state.loud, energy(raw, 0, BANDS), dt, 8, 2.5);
  state.pulse = Math.max(0, state.pulse - dt * 2.6);
  const beat = !options.reduced && detectBeat(bass, state.avgBass, state.lastBeat, now);
  state.avgBass = ease(state.avgBass, bass, dt, 1.6, 1.6);
  if (beat) {
    state.lastBeat = now;
    state.pulse = Math.min(1, 0.5 + bass);
    const count = 3 + Math.round(bass * 7);
    for (let index = 0; index < count; index += 1) spawnMote(state, options.width, options.height, bass);
  } else if (!options.reduced && treble > 0.12 && random(state) < dt * treble * 7) {
    spawnMote(state, options.width, options.height, treble);
  }
  if (!options.reduced) state.phase += dt * (0.5 + state.loud * 1.6);
  stepMotes(state, dt);
  return beat;
}

/** True once nothing is moving any more, so the loop can sleep until sound returns. */
export function isSettled(state: AuroraState) {
  return state.motes.length === 0 && state.loud < 0.004 && state.pulse < 0.01;
}

function drawRibbon(
  ctx: CanvasRenderingContext2D,
  state: AuroraState,
  palette: Palette,
  width: number,
  height: number,
  ribbon: number,
  color: Rgb,
  from: number,
  to: number,
) {
  const steps = Math.max(16, Math.min(RIBBON_POINTS - 1, Math.round(width / 18)));
  const baseline = height * (0.3 + ribbon * 0.2);
  const band = energy(state.levels, from, to);
  const swing = height * (0.05 + 0.2 * band);
  const curtain = height * (0.1 + 0.34 * band);
  const gain = Math.min(1, state.loud * 3.2) * (palette.dark ? 0.85 : 1.15);
  if (gain < 0.02) return;
  const top = state.tops[ribbon] ?? new Float32Array(RIBBON_POINTS);
  const span = to - from;
  let lowest = Infinity;
  for (let step = 0; step <= steps; step += 1) {
    const x = step / steps;
    const local = sampleLevels(state.levels, (from + x * span) / (BANDS - 1));
    const y = baseline + ribbonOffset(x, state.phase, ribbon, local) * swing - local * height * 0.06;
    top[step] = y;
    if (y < lowest) lowest = y;
  }
  const fill = ctx.createLinearGradient(0, lowest, 0, lowest + swing + curtain);
  fill.addColorStop(0, rgba(color, 0.3 * gain));
  fill.addColorStop(0.45, rgba(color, 0.1 * gain));
  fill.addColorStop(1, rgba(color, 0));
  ctx.beginPath();
  for (let step = 0; step <= steps; step += 1) {
    const x = (step / steps) * width;
    if (step === 0) ctx.moveTo(x, top[step] ?? baseline);
    else ctx.lineTo(x, top[step] ?? baseline);
  }
  for (let step = steps; step >= 0; step -= 1) {
    const local = sampleLevels(state.levels, (from + (step / steps) * span) / (BANDS - 1));
    ctx.lineTo((step / steps) * width, (top[step] ?? baseline) + curtain * (0.4 + local));
  }
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.beginPath();
  for (let step = 0; step <= steps; step += 1) {
    const x = (step / steps) * width;
    if (step === 0) ctx.moveTo(x, top[step] ?? baseline);
    else ctx.lineTo(x, top[step] ?? baseline);
  }
  ctx.lineJoin = "round";
  ctx.strokeStyle = rgba(color, 0.07 * gain);
  ctx.lineWidth = 9;
  ctx.stroke();
  ctx.strokeStyle = rgba(color, 0.3 * gain);
  ctx.lineWidth = 1.4;
  ctx.stroke();
}

function drawBloom(ctx: CanvasRenderingContext2D, state: AuroraState, palette: Palette, width: number, height: number) {
  const bass = energy(state.levels, 0, 10);
  const strength = Math.min(1, bass * 1.4) * 0.22 + state.pulse * 0.3;
  if (strength < 0.01) return;
  const radius = Math.max(width, height) * (0.32 + 0.22 * state.pulse + 0.12 * bass);
  const glow = ctx.createRadialGradient(width / 2, height, 0, width / 2, height, radius);
  glow.addColorStop(0, rgba(palette.low, strength));
  glow.addColorStop(0.5, rgba(palette.spark, strength * 0.35));
  glow.addColorStop(1, rgba(palette.low, 0));
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, width, height);
}

function drawMote(ctx: CanvasRenderingContext2D, mote: Mote, palette: Palette) {
  const alpha = moteAlpha(mote);
  if (alpha <= 0.01) return;
  const color = mote.warm ? palette.spark : palette.high;
  // Two soft discs stand in for a radial glow: no gradient object per spark.
  ctx.fillStyle = rgba(color, 0.12 * alpha);
  ctx.beginPath();
  ctx.arc(mote.x, mote.y, mote.size * 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = rgba(color, 0.25 * alpha);
  ctx.beginPath();
  ctx.arc(mote.x, mote.y, mote.size * 2.4, 0, Math.PI * 2);
  ctx.fill();
  // A four-point star: the Heinzels' little lantern sparks.
  const arm = mote.size * 2.6;
  ctx.strokeStyle = rgba(color, 0.8 * alpha);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(mote.x - arm, mote.y);
  ctx.lineTo(mote.x + arm, mote.y);
  ctx.moveTo(mote.x, mote.y - arm);
  ctx.lineTo(mote.x, mote.y + arm);
  ctx.stroke();
}

/** Draws one frame over the whole `width` x `height` area (the context is already scaled to CSS pixels). */
export function drawAurora(
  ctx: CanvasRenderingContext2D,
  state: AuroraState,
  palette: Palette,
  width: number,
  height: number,
) {
  ctx.clearRect(0, 0, width, height);
  ctx.globalCompositeOperation = palette.dark ? "lighter" : "source-over";
  drawBloom(ctx, state, palette, width, height);
  drawRibbon(ctx, state, palette, width, height, 0, palette.low, 0, 12);
  drawRibbon(ctx, state, palette, width, height, 1, palette.mid, 10, 30);
  drawRibbon(ctx, state, palette, width, height, 2, palette.high, 26, BANDS - 1);
  for (const mote of state.motes) drawMote(ctx, mote, palette);
  ctx.globalCompositeOperation = "source-over";
}
