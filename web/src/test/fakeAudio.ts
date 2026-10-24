// A Web Audio stand-in for tests: records what the mixer asks of it, decodes nothing (every buffer lasts `seconds`).
import { vi } from "vitest";

export class FakeParam {
  value = 1;
  readonly cancelScheduledValues = vi.fn();
  readonly setValueAtTime = vi.fn();
  readonly setTargetAtTime = vi.fn((value: number) => {
    this.target = value;
  });
  target: number | undefined;
}

export class FakeGain {
  readonly gain = new FakeParam();
  readonly connect = vi.fn();
}

export class FakeSource {
  buffer: { duration: number } | null = null;
  loop = false;
  readonly connect = vi.fn();
  readonly start = vi.fn();
  readonly stop = vi.fn();
}

/** A fake `AudioContext` class whose instances are kept in `contexts`; `seconds` is how long each decoded file is. */
export function fakeAudio(seconds: (url: string) => number = () => 10) {
  const contexts: FakeContext[] = [];
  class FakeContext {
    currentTime = 0;
    state = "running";
    readonly destination = {};
    readonly gains: FakeGain[] = [];
    readonly sources: FakeSource[] = [];
    readonly close = vi.fn(async () => undefined);
    readonly resume = vi.fn(async () => undefined);

    constructor() {
      contexts.push(this);
    }

    createGain() {
      const gain = new FakeGain();
      this.gains.push(gain);
      return gain;
    }

    createBufferSource() {
      const source = new FakeSource();
      this.sources.push(source);
      return source;
    }

    async decodeAudioData(data: ArrayBuffer) {
      return { duration: seconds(new TextDecoder().decode(data)) };
    }
  }
  return { Context: FakeContext as unknown as typeof AudioContext, contexts };
}

export type FakeContext = ReturnType<typeof fakeAudio>["contexts"][number];
