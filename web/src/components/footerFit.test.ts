import { describe, expect, it } from "vitest";
import { fitFooter, footerWidths } from "./footerFit";

const items = [
  { id: "version", width: 48, priority: 0, pinned: true },
  { id: "daemon", width: 24, priority: 30 },
  { id: "update", width: 24, priority: 100 },
  { id: "health", width: 24, priority: 90 },
  { id: "clock", width: 80, priority: 0, pinned: true },
  { id: "credit", width: 20, priority: 0 },
];

describe("footer fit from measured content", () => {
  it("keeps every icon at narrow widths when its actual content fits", () => {
    expect(fitFooter(375, items, 8, 24)).toEqual({ visible: items.map((item) => item.id), hidden: [] });
    expect(fitFooter(260, items, 8, 24).hidden).toEqual([]);
  });
  it("reserves space for More and retains the highest-priority control without moving the clock/version", () => {
    expect(fitFooter(220, items, 8, 24)).toEqual({
      visible: ["version", "update", "clock"],
      hidden: ["daemon", "health", "credit"],
    });
  });
  it("does not let one wide meter group hide smaller controls that still fit", () => {
    const wide = [...items.slice(0, 1), { id: "resources", width: 160, priority: 60 }, ...items.slice(1)];
    const fit = fitFooter(280, wide, 8, 24);
    expect(fit.hidden).toContain("resources");
    expect(fit.visible).toEqual(["version", "daemon", "update", "health", "clock"]);
  });
  it("has no overflow button when all content just fits, and keeps stable source order within selected controls", () => {
    expect(fitFooter(260, items, 8, 24).hidden).toEqual([]);
    expect(fitFooter(259, items, 8, 24).hidden.length).toBeGreaterThan(0);
    expect(fitFooter(300, [...items].reverse(), 8, 24).visible).toEqual([...items].reverse().map((item) => item.id));
  });
  it("compacts the Berlin text before hiding a status control and expands it again when room returns", () => {
    const credit = { id: "credit", expanded: 180, compact: 20 };
    const narrow = footerWidths(375, items, 8, credit);
    expect(narrow.compactCredit).toBe(true);
    expect(fitFooter(375, narrow.items, 8, 24).hidden).toEqual([]);
    const wide = footerWidths(500, narrow.items, 8, credit);
    expect(wide.compactCredit).toBe(false);
    expect(wide.items.find((item) => item.id === "credit")?.width).toBe(180);
  });
  it("ignores not-yet-rendered controls, rather than reserving phantom gaps", () => {
    expect(fitFooter(260, [...items, { id: "pending", width: 0, priority: 50 }], 8, 24).hidden).toEqual([]);
  });
  it("keeps attribution expanded when unresolved controls add no actual width or gap", () => {
    const measured = [
      { id: "version", width: 40, priority: 0, pinned: true },
      { id: "pending", width: 0, priority: 0 },
      { id: "credit", width: 72, priority: 0 },
    ];
    const result = footerWidths(120, measured, 8, { id: "credit", expanded: 72, compact: 0 });
    expect(result.compactCredit).toBe(false);
    expect(fitFooter(120, result.items, 8, 24)).toEqual({ visible: ["version", "credit"], hidden: [] });
  });
});
