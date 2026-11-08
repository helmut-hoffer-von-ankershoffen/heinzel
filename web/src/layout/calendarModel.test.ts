import { describe, expect, it } from "vitest";
import {
  type AgendaData,
  gapAngle,
  glyphModel,
  labelFontSize,
  levelFor,
  overlapAngle,
  overlapIsUrgent,
  ringLabel,
} from "./calendarModel";

const NOW = Date.parse("2026-11-07T09:43:00Z");
const at = (m: number) => new Date(NOW + m * 60_000).toISOString();
const ev = (title: string, s: number, e: number) => ({ title, start: at(s), end: at(e) });
const model = (current: AgendaData["current"], next: AgendaData["next"], now = NOW) =>
  glyphModel({ agenda: { current, next, problem: null }, failed: false, pending: false, now });

describe("gapAngle: gap minutes to degrees clockwise from 12, the current end anchored at 12", () => {
  it.each([
    [0, 0],
    [7, 42],
    [15, 90],
    [30, 180],
    [45, 270],
    [59, 354],
  ])("%s min is %s degrees", (gap, angle) => expect(gapAngle(gap)).toBeCloseTo(angle, 6));
  it("shows nothing at 60 minutes or more, for overlaps, and for junk", () => {
    for (const gap of [60, 61, 600, -1, Number.NaN, Number.POSITIVE_INFINITY]) expect(gapAngle(gap)).toBeNull();
  });
});

describe("labels", () => {
  it.each([
    [5, "5"],
    [47, "47"],
    [59, "59"],
    [60, "1h"],
    [119, "1h"],
    [150, "2h"],
    [599, "9h"],
    [600, ">9h"],
    [700, ">9h"],
  ])("%s min reads %s", (minutes, text) => expect(ringLabel(minutes)).toBe(text));
  it("sets two characters at full size and three one step down", () => {
    expect(labelFontSize("47")).toBe(11.5);
    expect(labelFontSize("5")).toBe(11.5);
    expect(labelFontSize("1h")).toBe(11.5);
    expect(labelFontSize(">9h")).toBeLessThan(11.5);
  });
  it("escalates amber at 5 and red at 1", () => {
    expect([30, 6, 5, 2, 1].map(levelFor)).toEqual(["calm", "calm", "warn", "warn", "bad"]);
  });
});

describe("overlap diamond", () => {
  it("is urgent when the overlapping meeting starts in 5 minutes or less, neutral later", () => {
    expect([-3, 0, 1, 5].map(overlapIsUrgent)).toEqual([true, true, true, true]);
    expect([6, 8, 30].map(overlapIsUrgent)).toEqual([false, false, false]);
  });
  it("sits where the overlapping start falls on the current meeting's ring", () => {
    expect(overlapAngle(0, 30, 15)).toBe(180);
    expect(overlapAngle(0, 30, 45)).toBe(360);
    expect(overlapAngle(0, 30, -5)).toBe(0);
  });
});

describe("state machine", () => {
  it("is unknown when not connected or failed, and says so", () => {
    const off = glyphModel({
      agenda: { current: null, next: null, problem: "No calendar connected.", fix: "hz setup" },
      failed: false,
      pending: false,
      now: NOW,
    });
    expect(off).toMatchObject({ kind: "unknown", reason: "not-connected" });
    expect(glyphModel({ agenda: undefined, failed: true, pending: false, now: NOW })).toMatchObject({
      kind: "unknown",
      reason: "unavailable",
    });
    expect(
      glyphModel({ agenda: { current: null, next: null, problem: "boom" }, failed: false, pending: false, now: NOW }),
    ).toMatchObject({ kind: "unknown", reason: "unavailable" });
  });
  it("is idle while loading, with nothing coming, or with free time beyond the counted window", () => {
    expect(glyphModel({ agenda: undefined, failed: false, pending: true, now: NOW }).kind).toBe("idle");
    expect(model(null, null).kind).toBe("idle");
    expect(model(null, ev("Far", 700, 730)).kind).toBe("idle");
  });
  it("counts minutes left in a meeting with the elapsed share", () => {
    const m = model(ev("Roadmap sync", -12, 18), ev("Budget review", 90, 120));
    expect(m).toMatchObject({ kind: "meeting", minutes: 18, text: "18", level: "calm", marker: null });
    expect(m.kind === "meeting" && m.fraction).toBeCloseTo(0.4, 6);
  });
  it("goes amber at 4 minutes and red at 1 with number and level in agreement", () => {
    expect(model(ev("A", -26, 4), null)).toMatchObject({ level: "warn", text: "4" });
    expect(model(ev("A", -29, 1), null)).toMatchObject({ level: "bad", text: "1" });
  });
  it("places the next start by the gap: 7 minutes after the end is 7/60 of the circle", () => {
    const m = model(ev("A", -10, 20), ev("B", 27, 57));
    expect(m).toMatchObject({ marker: { kind: "stroke", gap: 7 } });
    expect(m.kind === "meeting" && m.marker?.kind === "stroke" && m.marker.angle).toBeCloseTo(42, 6);
    expect(m.description).toContain("starts 7 min after it ends");
  });
  it("puts back-to-back at 12 and shows no stroke for a gap of an hour or more", () => {
    expect(model(ev("A", -10, 20), ev("B", 20, 50))).toMatchObject({ marker: { kind: "stroke", angle: 0, gap: 0 } });
    expect(model(ev("A", -10, 20), ev("B", 80, 110))).toMatchObject({ marker: null });
    expect(model(ev("A", -10, 20), ev("B", 79, 110))).toMatchObject({ marker: { kind: "stroke" } });
  });
  it("marks an overlap with a diamond: red from 5 minutes, neutral later, with text", () => {
    const red = model(ev("A", -22, 8), ev("B", 5, 35));
    expect(red).toMatchObject({ marker: { kind: "diamond", red: true, startsIn: 5 } });
    expect(red.kind === "meeting" && red.description).toContain("overlapping meeting starts in 5 min");
    const calm = model(ev("A", -22, 40), ev("B", 6, 35));
    expect(calm).toMatchObject({ marker: { kind: "diamond", red: false, startsIn: 6 } });
    expect(model(ev("A", -22, 40), ev("B", 5.5, 35))).toMatchObject({ marker: { red: false, startsIn: 6 } });
  });
  it("shows focus time against the next meeting, with a dot when it is close", () => {
    expect(model(null, ev("Budget review", 47, 77))).toMatchObject({
      kind: "free",
      minutes: 47,
      text: "47",
      marker: null,
    });
    expect(model(null, ev("Hiring", 130, 160))).toMatchObject({ kind: "free", text: "2h" });
    expect(model(null, ev("Budget review", 9, 39))).toMatchObject({ kind: "free", marker: { kind: "dot" } });
    expect(model(null, ev("Soon", 3, 30))).toMatchObject({ kind: "free", level: "warn" });
  });
  it("treats a next meeting that has started on the local clock as the current one", () => {
    expect(model(ev("A", -30, 0), ev("B", -1, 29))).toMatchObject({ kind: "meeting", text: "29" });
  });
});
