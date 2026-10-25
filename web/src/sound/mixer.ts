// A layered soundscape's mixer (ADR 0058): its files, fetched from hz (`/sound-assets`, this origin), decoded and
// looped through one gain per layer and a master gain, all faded with Web Audio. Created inside a Play click, so the
// browser lets it sound.
import { type Layer, layerGain, type Signals } from "./machine";

/** A decoded layer longer than this is refused: a small, heavily compressed file can decode to a lot of memory. */
export const MAX_LAYER_SECONDS = 120;
/** An earcon is a short sound. */
export const MAX_EARCON_SECONDS = 5;

export type LayerView = Layer & { url: string };
export type EarconView = { kind: string; url: string };

async function decode(context: BaseAudioContext, url: string, limit: number): Promise<AudioBuffer> {
  const response = await fetch(new URL(url, window.location.origin));
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const buffer = await context.decodeAudioData(await response.arrayBuffer());
  if (buffer.duration > limit) throw new Error(`longer than ${limit} s`);
  return buffer;
}

export class Mixer {
  private readonly master: GainNode;
  private readonly gains: { node: GainNode; layer: Layer }[] = [];
  private readonly sources: AudioBufferSourceNode[] = [];
  private readonly earcons = new Map<string, AudioBuffer>();

  constructor(private readonly context: AudioContext) {
    this.master = context.createGain();
    this.master.gain.value = 0;
    this.master.connect(context.destination);
  }

  /** Fetch, decode and start every layer looping, silent until `fade` raises the master gain. */
  async load(layers: LayerView[], earcons: EarconView[], signals: Signals): Promise<void> {
    const decoded = await Promise.all(layers.map((layer) => decode(this.context, layer.url, MAX_LAYER_SECONDS)));
    decoded.forEach((buffer, index) => {
      const layer = layers[index] as LayerView;
      const node = this.context.createGain();
      node.gain.value = layerGain(layer, signals);
      node.connect(this.master);
      const source = this.context.createBufferSource();
      source.buffer = buffer;
      source.loop = true;
      source.connect(node);
      source.start();
      this.gains.push({ node, layer });
      this.sources.push(source);
    });
    for (const earcon of earcons) {
      try {
        this.earcons.set(earcon.kind, await decode(this.context, earcon.url, MAX_EARCON_SECONDS));
      } catch {
        // an earcon that won't decode stays silent; the layers still play
      }
    }
  }

  /** Fade the whole mix to `gain` over `seconds`. */
  fade(gain: number, seconds: number): void {
    ramp(this.master.gain, gain, seconds, this.context.currentTime);
  }

  /** Follow the signals: each layer fades to its gain under them over `seconds`. */
  follow(signals: Signals, seconds: number): void {
    for (const { node, layer } of this.gains)
      ramp(node.gain, layerGain(layer, signals), seconds, this.context.currentTime);
  }

  /** Play the earcon of notice kind `kind` once through the master gain, if the soundscape has one. */
  earcon(kind: string): void {
    const buffer = this.earcons.get(kind);
    if (!buffer) return;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.master);
    source.start();
  }

  async close(): Promise<void> {
    for (const source of this.sources) source.stop();
    await this.context.close();
  }
}

/** Ramp `param` to `value`: about 95% there after `seconds` (three time constants). */
export function ramp(param: AudioParam, value: number, seconds: number, now: number): void {
  param.cancelScheduledValues(now);
  param.setValueAtTime(param.value, now);
  param.setTargetAtTime(value, now, Math.max(seconds, 0.01) / 3);
}
