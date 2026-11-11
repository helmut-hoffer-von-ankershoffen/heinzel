import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CalendarGlyph } from "./CalendarGlyph";
import { type CountingModel, type GlyphModel, labelFontSize } from "./calendarModel";

const meeting = (over: Partial<CountingModel> = {}): CountingModel => ({
  kind: "meeting",
  level: "calm",
  minutes: 18,
  text: "18",
  fraction: 0.4,
  marker: null,
  description: "In A, 18 min left",
  ...over,
});
const draw = (model: GlyphModel) =>
  render(<CalendarGlyph model={model} />).container.querySelector("svg") as SVGSVGElement;

describe("CalendarGlyph", () => {
  it("keeps the 24px footprint in every state", () => {
    const models: GlyphModel[] = [
      meeting(),
      meeting({ level: "warn", minutes: 4, text: "4" }),
      meeting({ level: "bad", minutes: 1, text: "1" }),
      meeting({ minutes: 700, text: ">9h" }),
      { kind: "free", level: "calm", minutes: 47, text: "47", fraction: 0.39, marker: null, description: "" },
      { kind: "idle", description: "" },
      { kind: "unknown", reason: "unavailable", description: "" },
    ];
    for (const model of models) {
      const svg = draw(model);
      expect(svg).toHaveClass("size-6");
      expect(svg.getAttribute("viewBox")).toBe("0 0 24 24");
      expect(svg.getAttribute("aria-hidden")).toBe("true");
    }
  });
  it("uses a smaller number for three characters and tabular numerals", () => {
    const svg = draw(meeting({ text: ">9h", minutes: 700 }));
    const label = svg.querySelector("[data-label]") as SVGTextElement;
    expect(Number(label.getAttribute("font-size"))).toBe(labelFontSize(">9h"));
    expect(label.style.fontVariantNumeric).toBe("tabular-nums");
  });
  it("thickens amber and fills red with the pulse limited to motion-safe", () => {
    expect(draw(meeting({ level: "warn", text: "4" })).querySelector("[data-ring]")).toHaveAttribute(
      "stroke-width",
      "3",
    );
    const bad = draw(meeting({ level: "bad", text: "1" }));
    expect(bad).toHaveClass("text-bad", "motion-safe:animate-pulse");
    expect(bad.querySelectorAll("circle").length).toBe(2);
  });
  it("dashes free time and solid-draws a meeting", () => {
    const free = draw({
      kind: "free",
      level: "calm",
      minutes: 47,
      text: "47",
      fraction: 0.4,
      marker: null,
      description: "",
    });
    expect(free.querySelector("[data-ring]")).toHaveAttribute("stroke-dasharray");
    expect(draw(meeting()).querySelector("[data-ring]")).not.toHaveAttribute("stroke-dasharray");
  });
  it("draws the gap stroke at its angle, back to back at 12, and a diamond that is red only when urgent", () => {
    const gap = draw(meeting({ marker: { kind: "stroke", angle: 42, gap: 7 } })).querySelector("[data-marker]");
    expect(gap).toHaveAttribute("data-marker", "gap");
    expect(gap).toHaveAttribute("data-angle", "42");
    expect(
      draw(meeting({ marker: { kind: "stroke", angle: 0, gap: 0 } })).querySelector("[data-marker]"),
    ).toHaveAttribute("data-marker", "back-to-back");
    const red = draw(meeting({ marker: { kind: "diamond", angle: 100, red: true, startsIn: 3 } })).querySelector(
      "[data-marker=diamond]",
    );
    expect(red).toHaveAttribute("data-red", "true");
    expect(red).toHaveClass("fill-bad", "stroke-bad");
    const calm = draw(meeting({ marker: { kind: "diamond", angle: 100, red: false, startsIn: 8 } })).querySelector(
      "[data-marker=diamond]",
    );
    expect(calm).toHaveAttribute("data-red", "false");
    expect(calm).not.toHaveClass("fill-bad");
  });
  it("shows the question mark badge when unknown", () => {
    const svg = draw({ kind: "unknown", reason: "not-connected", description: "" });
    expect(svg.textContent).toBe("?");
  });
});
