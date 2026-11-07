// The one audio analysis every visualization style reads: 32 log-spaced bands (40 Hz to 12 kHz), eased with a fast
// attack and a slower release, peak hold with a slow fall, bass/mid/treble means, loudness, beats and quiet.
export const VIZ_BANDS = 32;

export const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** Bin ranges [from, to) of the 32 bands for an analyser of `fftSize` at `sampleRate`. Strictly increasing. */
export function bandRanges(sampleRate: number, fftSize: number, bins = fftSize / 2) {
  const binHz = sampleRate / fftSize;
  const ranges: [number, number][] = [];
  let previous = Math.max(1, Math.round(40 / binHz));
  for (let band = 1; band <= VIZ_BANDS; band += 1) {
    const frequency = 40 * (12000 / 40) ** (band / VIZ_BANDS);
    const next = Math.min(bins, Math.max(previous + 1, Math.round(frequency / binHz)));
    ranges.push([Math.min(previous, bins - 1), Math.max(next, Math.min(previous, bins - 1) + 1)]);
    previous = next;
  }
  return ranges;
}

export class Analysis {
  readonly bands = new Float32Array(VIZ_BANDS);
  readonly smooth = new Float32Array(VIZ_BANDS);
  readonly peaks = new Float32Array(VIZ_BANDS);
  bass = 0;
  mid = 0;
  treble = 0;
  level = 0;
  beat = 0;
  beatEvent = false;
  quiet = true;
  private lastBeat = -1e9;
  private quietSince = 0;
  private readonly history = new Float32Array(43);
  private cursor = 0;

  constructor(private readonly ranges: readonly (readonly [number, number])[]) {}

  /** Feed one frame of byte frequency data (and the time-domain bytes for loudness); `dt` in seconds, `now` in ms. */
  step(freq: ArrayLike<number>, time: ArrayLike<number> | null, now: number, dt: number) {
    let instant = 0;
    for (let band = 0; band < VIZ_BANDS; band += 1) {
      const [from, to] = this.ranges[band] ?? [1, 2];
      let max = 0;
      for (let bin = from; bin < to; bin += 1) max = Math.max(max, freq[bin] ?? 0);
      const value = clamp((max / 255) ** 1.35 * (0.85 + (band / VIZ_BANDS) * 0.75), 0, 1);
      this.bands[band] = value;
      const eased = this.smooth[band] ?? 0;
      const next = value > eased ? eased + (value - eased) * 0.55 : eased + (value - eased) * Math.min(1, dt * 4.5);
      this.smooth[band] = next;
      this.peaks[band] = Math.max(next, (this.peaks[band] ?? 0) - dt * 0.45);
      if (band < 6) instant += value;
    }
    instant /= 6;
    this.bass = this.mean(0, 8);
    this.mid = this.mean(8, 20);
    this.treble = this.mean(20, VIZ_BANDS);
    if (time) {
      let sum = 0;
      let count = 0;
      for (let index = 0; index < time.length; index += 4) {
        const sample = ((time[index] ?? 128) - 128) / 128;
        sum += sample * sample;
        count += 1;
      }
      this.level = clamp(Math.sqrt(sum / Math.max(1, count)) * 2.6, 0, 1);
    } else this.level = clamp((this.bass + this.mid + this.treble) / 2, 0, 1);
    let average = 0;
    for (const value of this.history) average += value;
    average /= this.history.length;
    this.history[this.cursor] = instant;
    this.cursor = (this.cursor + 1) % this.history.length;
    this.beatEvent = false;
    if (instant > 0.14 && instant > average * 1.28 && now - this.lastBeat > 333) {
      this.beat = 1;
      this.beatEvent = true;
      this.lastBeat = now;
    } else this.beat = Math.max(0, this.beat - dt / 0.32);
    const silent = this.level < 0.02 && this.bass < 0.04 && this.treble < 0.04;
    if (silent) {
      if (!this.quietSince) this.quietSince = now;
      this.quiet = now - this.quietSince > 1500;
    } else {
      this.quietSince = 0;
      this.quiet = false;
    }
  }

  /** Back to rest: nothing moves and the loop may sleep. */
  rest() {
    this.bands.fill(0);
    this.smooth.fill(0);
    this.peaks.fill(0);
    this.bass = this.mid = this.treble = this.level = this.beat = 0;
    this.quiet = true;
    this.beatEvent = false;
  }

  /** Where the sound sits on the spectrum: the level-weighted mean band, 0 (40 Hz) to 1 (12 kHz); -1 when silent. */
  centroid() {
    let weight = 0;
    let sum = 0;
    for (let band = 0; band < VIZ_BANDS; band += 1) {
      const value = this.smooth[band] ?? 0;
      weight += value;
      sum += value * band;
    }
    return weight > 0.01 ? sum / weight / (VIZ_BANDS - 1) : -1;
  }

  private mean(from: number, to: number) {
    let sum = 0;
    for (let band = from; band < to; band += 1) sum += this.smooth[band] ?? 0;
    return sum / (to - from);
  }
}
