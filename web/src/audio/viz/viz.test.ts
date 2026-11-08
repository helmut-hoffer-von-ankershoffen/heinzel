import { describe, expect, it, vi } from "vitest";
import { readPalette } from "../aurora";
import { Analysis, bandRanges, VIZ_BANDS } from "./analysis";
import { watchTheme } from "./color";
import { drawRail, railLayout } from "./rail";
import { DEFAULT_SETTINGS, parseSettings, styleOn } from "./settings";
import { readVizTheme } from "./theme";

const theme = { a: { r: 166, g: 64, b: 40 }, b: { r: 31, g: 116, b: 68 }, c: { r: 183, g: 132, b: 28 }, dark: false };

function analysis() {
  return new Analysis(bandRanges(48000, 2048, 1024));
}

describe("analysis", () => {
  it("covers 40 Hz to 12 kHz in 32 strictly increasing bands", () => {
    const ranges = bandRanges(48000, 2048, 1024);
    expect(ranges).toHaveLength(VIZ_BANDS);
    for (let index = 0; index < ranges.length; index += 1) {
      const [from, to] = ranges[index] ?? [0, 0];
      expect(to).toBeGreaterThan(from);
      if (index) expect(from).toBeGreaterThanOrEqual(ranges[index - 1]?.[0] ?? 0);
    }
    expect(ranges.at(-1)?.[1]).toBeLessThanOrEqual(1024);
  });

  it("eases up fast, falls slowly, holds peaks and finds a beat on a bass hit", () => {
    const a = analysis();
    const loud = new Uint8Array(1024);
    loud.fill(255, 0, 40);
    for (let frame = 0; frame < 3; frame += 1) a.step(new Uint8Array(1024), null, frame * 33, 0.033);
    a.step(loud, null, 1000, 0.033);
    expect(a.beatEvent).toBe(true);
    expect(a.beat).toBe(1);
    expect(a.bass).toBeGreaterThan(0.3);
    const peak = a.peaks[0] ?? 0;
    expect(peak).toBeGreaterThan(0.4);
    a.step(new Uint8Array(1024), null, 1033, 0.033);
    expect(a.smooth[0]).toBeLessThan(peak);
    expect(a.smooth[0]).toBeGreaterThan(0.25);
    expect(a.peaks[0]).toBeGreaterThanOrEqual(a.smooth[0] ?? 0);
  });

  it("does not flash faster than three beats a second", () => {
    const a = analysis();
    const loud = new Uint8Array(1024);
    loud.fill(255, 0, 40);
    let beats = 0;
    for (let frame = 0; frame < 90; frame += 1) {
      a.step(frame % 2 ? loud : new Uint8Array(1024), null, 2000 + frame * 33, 0.033);
      if (a.beatEvent) beats += 1;
    }
    expect(beats / 3).toBeLessThanOrEqual(3.1);
  });

  it("places the sound on the spectrum: low for bass, high for treble, -1 when silent", () => {
    const low = analysis();
    expect(low.centroid()).toBe(-1);
    const bass = new Uint8Array(1024);
    bass.fill(255, 1, 6);
    low.step(bass, null, 1000, 0.033);
    const high = analysis();
    const treble = new Uint8Array(1024);
    treble.fill(255, 400, 512);
    high.step(treble, null, 1000, 0.033);
    expect(low.centroid()).toBeLessThan(0.15);
    expect(high.centroid()).toBeGreaterThan(0.85);
  });

  it("goes quiet after 1.5 s of silence and rests", () => {
    const a = analysis();
    for (let frame = 0; frame < 80; frame += 1) a.step(new Uint8Array(1024), null, frame * 33, 0.033);
    expect(a.quiet).toBe(true);
    a.rest();
    expect(a.level).toBe(0);
  });
});

describe("spectrum rail", () => {
  const ctx = () =>
    ({
      clearRect: vi.fn(),
      beginPath: vi.fn(),
      roundRect: vi.fn(),
      rect: vi.fn(),
      fill: vi.fn(),
      globalAlpha: 1,
      shadowBlur: 0,
    }) as unknown as CanvasRenderingContext2D;

  it("lays 32 pills over the full height", () => {
    const { gap, pill } = railLayout(36, 900);
    expect(32 * pill + 33 * gap).toBeCloseTo(900, 3);
  });

  it("keeps every pill and peak cap inside the rail, even when it is narrow or short", () => {
    const sizes: [number, number][] = [
      [1, 1],
      [28, 24],
      [36, 320],
      [36, 900],
    ];
    for (const [width, height] of sizes) {
      const a = analysis();
      a.step(new Uint8Array(1024).fill(255), null, 1000, 0.033);
      a.step(new Uint8Array(1024).fill(40), null, 1033, 0.033);
      const draw = ctx();
      drawRail(draw, a, theme, width, height, false);
      const boxes = vi
        .mocked(draw.roundRect)
        .mock.calls.map(([x, y, w, h]) => [Number(x), Number(y), Number(w), Number(h)] as const);
      expect(boxes.length).toBeGreaterThanOrEqual(32);
      for (const [x, y, w, h] of boxes) {
        expect(x).toBeGreaterThanOrEqual(-1e-9);
        expect(y).toBeGreaterThanOrEqual(-1e-9);
        expect(x + w).toBeLessThanOrEqual(width + 1e-9);
        expect(y + h).toBeLessThanOrEqual(height + 1e-9);
      }
    }
  });

  it("is a dim still ladder when quiet, and widens pills with their band when loud", () => {
    const a = analysis();
    const quiet = ctx();
    drawRail(quiet, a, theme, 36, 600, false);
    expect(quiet.roundRect).toHaveBeenCalledTimes(32);
    const loud = new Uint8Array(1024).fill(255);
    a.step(loud, null, 1000, 0.033);
    const busy = ctx();
    drawRail(busy, a, theme, 36, 600, false);
    const widths = vi.mocked(busy.roundRect).mock.calls.map((call) => Number(call[2]));
    expect(Math.max(...widths)).toBeGreaterThan(20);
    expect(vi.mocked(quiet.roundRect).mock.calls.every((call) => Number(call[2]) === 4)).toBe(true);
  });

  it("draws no glow under reduced motion", () => {
    const a = analysis();
    a.step(new Uint8Array(1024).fill(255), null, 1000, 0.033);
    const calm = ctx();
    drawRail(calm, a, theme, 36, 600, true);
    expect(calm.shadowBlur).toBe(0);
  });
});

describe("settings", () => {
  it("is all on by default and reads a stored choice", () => {
    expect(parseSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(parseSettings("not json")).toEqual(DEFAULT_SETTINGS);
    const stored = parseSettings(JSON.stringify({ master: true, styles: { aurora: false } }));
    expect(stored.styles).toEqual({ spectrum: true, aurora: false });
    expect(styleOn(stored, "aurora")).toBe(false);
    expect(styleOn(parseSettings(JSON.stringify({ master: false })), "spectrum")).toBe(false);
  });
});

describe("theme colours", () => {
  it("takes every hue from the live tokens and reports a theme switch", async () => {
    const root = document.documentElement;
    root.style.setProperty("--color-primary", "#123456");
    root.style.setProperty("--color-ok", "rgb(1, 2, 3)");
    root.style.setProperty("--color-warn", "#abcdef");
    const changed = vi.fn();
    const unwatch = watchTheme(changed);
    try {
      expect(readVizTheme()).toMatchObject({ a: { r: 18, g: 52, b: 86 }, b: { r: 1, g: 2, b: 3 } });
      expect(readPalette()).toMatchObject({ low: { r: 18, g: 52, b: 86 }, high: { r: 171, g: 205, b: 239 } });
      root.style.setProperty("--color-primary", "#654321");
      root.classList.add("dark");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(changed).toHaveBeenCalled();
      expect(readVizTheme()).toMatchObject({ a: { r: 101, g: 67, b: 33 }, dark: true });
      // A token the browser cannot read falls back to another token, never to a written colour.
      root.style.setProperty("--color-ok", "not-a-colour");
      expect(readVizTheme().b).toEqual(readVizTheme().a);
    } finally {
      unwatch();
      root.classList.remove("dark");
      for (const name of ["--color-primary", "--color-ok", "--color-warn"]) root.style.removeProperty(name);
    }
  });
});
