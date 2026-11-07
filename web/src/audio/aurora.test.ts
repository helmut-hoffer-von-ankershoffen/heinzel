import { describe, expect, it } from "vitest";
import {
  BANDS,
  bandEdges,
  bandLevels,
  canvasSize,
  createState,
  detectBeat,
  ease,
  holdPeak,
  isSettled,
  moteAlpha,
  parseColor,
  ribbonOffset,
  sampleLevels,
  spawnMote,
  stepMotes,
  stepState,
} from "./aurora";

const fallback = { r: 1, g: 2, b: 3 };

describe("aurora maths", () => {
  it("parses theme colours and falls back for anything else", () => {
    expect(parseColor("#a64028", fallback)).toEqual({ r: 166, g: 64, b: 40 });
    expect(parseColor(" #fa0 ", fallback)).toEqual({ r: 255, g: 170, b: 0 });
    expect(parseColor("rgb(10, 20, 300)", fallback)).toEqual({ r: 10, g: 20, b: 255 });
    expect(parseColor("var(--x)", fallback)).toBe(fallback);
  });

  it("builds strictly increasing log-spaced edges that cover every bin", () => {
    for (const bins of [64, 512, 2048]) {
      const edges = bandEdges(bins);
      expect(edges).toHaveLength(BANDS + 1);
      expect(edges.at(-1)).toBeGreaterThanOrEqual(bins);
      for (let index = 1; index < edges.length; index += 1) expect(edges[index]).toBeGreaterThan(edges[index - 1] ?? 0);
    }
    const wide = bandEdges(512);
    expect((wide[1] ?? 0) - (wide[0] ?? 0)).toBeLessThan((wide[BANDS] ?? 0) - (wide[BANDS - 1] ?? 0));
  });

  it("maps bytes to levels in 0..1", () => {
    const edges = bandEdges(64);
    const loud = bandLevels(new Uint8Array(64).fill(255), edges, new Float32Array(BANDS));
    const silent = bandLevels(new Uint8Array(64), edges, new Float32Array(BANDS));
    expect(Math.min(...loud)).toBeCloseTo(1, 5);
    expect(Math.max(...silent)).toBe(0);
  });

  it("attacks quickly, releases slowly and holds peaks", () => {
    const up = ease(0, 1, 0.016, 14, 3);
    const down = 1 - ease(1, 0, 0.016, 14, 3);
    expect(up).toBeGreaterThan(down * 3);
    expect(ease(0.5, 0.5, 1, 14, 3)).toBe(0.5);
    expect(holdPeak(0.2, 0.8, 0.016, 0.35)).toBe(0.8);
    expect(holdPeak(0.8, 0.1, 1, 0.35)).toBeCloseTo(0.45);
    expect(holdPeak(0.1, 0, 10, 0.35)).toBe(0);
  });

  it("detects a beat only on a clear bass rise, once per refractory period", () => {
    expect(detectBeat(0.7, 0.2, -Infinity, 1000)).toBe(true);
    expect(detectBeat(0.7, 0.2, 900, 1000)).toBe(false);
    expect(detectBeat(0.7, 0.6, -Infinity, 1000)).toBe(false);
    expect(detectBeat(0.1, 0, -Infinity, 1000)).toBe(false);
  });

  it("interpolates band levels and keeps ribbons in range", () => {
    expect(sampleLevels([0, 1], 0.5)).toBeCloseTo(0.5);
    expect(sampleLevels([0, 1], 2)).toBe(1);
    for (let step = 0; step <= 20; step += 1) {
      const offset = ribbonOffset(step / 20, step, step % 3, step / 20);
      expect(Math.abs(offset)).toBeLessThanOrEqual(1);
    }
  });

  it("caps the canvas ratio and pixel count", () => {
    expect(canvasSize(1000, 800, 3).ratio).toBe(1.5);
    expect(canvasSize(1000, 800, 1)).toMatchObject({ ratio: 1, width: 1000, height: 800 });
    const huge = canvasSize(5120, 2880, 2);
    expect(huge.width * huge.height).toBeLessThanOrEqual(2_430_000);
  });

  it("drifts sparks up, fades them and removes the finished ones", () => {
    const state = createState(7);
    for (let index = 0; index < 200; index += 1) spawnMote(state, 800, 600, 1);
    expect(state.motes.length).toBeLessThanOrEqual(72);
    const first = state.motes[0];
    const y = first?.y ?? 0;
    stepMotes(state, 0.5);
    expect(first?.y).toBeLessThan(y);
    expect(moteAlpha({ ...(first as NonNullable<typeof first>), age: 0 })).toBeLessThan(0.3);
    expect(stepMotes(state, 10)).toBe(0);
  });

  it("pulses and sparkles on a beat, stays calm when reduced, and settles in silence", () => {
    const edges = bandEdges(64);
    const bass = new Uint8Array(64);
    bass.fill(230, 0, 32);
    const options = { reduced: false, width: 800, height: 600 };
    const live = createState(3);
    expect(stepState(live, bass, edges, 0.016, 1000, options)).toBe(true);
    expect(live.pulse).toBeGreaterThan(0.5);
    expect(live.motes.length).toBeGreaterThan(2);
    const calm = createState(3);
    expect(stepState(calm, bass, edges, 0.016, 1000, { ...options, reduced: true })).toBe(false);
    expect(calm.motes).toHaveLength(0);
    expect(calm.phase).toBe(0);
    const silence = new Uint8Array(64);
    for (let index = 0; index < 600; index += 1) stepState(live, silence, edges, 0.05, 2000 + index * 50, options);
    expect(isSettled(live)).toBe(true);
  });
});
