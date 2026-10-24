import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeinzelAvatar } from "./HeinzelAvatar";
import { AGES, BEARDS, heinzelGroupSvg, heinzelShapes, heinzelSvg, STATES, TUNICS } from "./heinzel";

/** Where the icons' SVGs are, from web/ (where vitest runs). */
const MACOS = resolve("../src/heinzel/modules/system/notify/macos");

/** WCAG's relative luminance of a #rrggbb colour. */
function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = Number.parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

describe("Heinzelmännchen's drawing", () => {
  it("is what heinzel.svg and heinzel-group.svg hold, so the icons show the avatars' gnome (make icons)", () => {
    expect(readFileSync(resolve(MACOS, "heinzel.svg"), "utf-8")).toBe(heinzelSvg());
    expect(readFileSync(resolve(MACOS, "heinzel-group.svg"), "utf-8")).toBe(heinzelGroupSvg());
  });

  it("dresses every tunic so it stands out on a light and on a dark background", () => {
    expect(Object.keys(TUNICS)).toHaveLength(8);
    for (const colour of Object.values(TUNICS)) {
      expect(contrast(colour, "#ffffff")).toBeGreaterThanOrEqual(3);
      expect(contrast(colour, "#181818")).toBeGreaterThanOrEqual(3);
    }
  });

  it("draws every age, beard, tunic and state, the tunic in its colour and a badge only for a state", () => {
    for (const age of AGES)
      for (const beard of BEARDS)
        for (const [tunic, colour] of Object.entries(TUNICS) as [keyof typeof TUNICS, string][])
          for (const state of STATES) {
            const shapes = heinzelShapes({ age, beard, tunic, state });
            expect(shapes[0]).toMatchObject({ fill: colour });
            const badged = shapes.some((shape) => shape.part === "badge");
            expect(badged).toBe(!["idle", "working"].includes(state));
          }
  });

  it("is the classic one by default: old, a long white beard, in green", () => {
    expect(heinzelSvg()).toBe(heinzelSvg({ tunic: "green", age: "old", beard: "long", state: "idle" }));
  });
});

describe("HeinzelAvatar", () => {
  it("is decoration without a label, and says its name and state with one", () => {
    const { container, rerender, getByRole } = render(<HeinzelAvatar />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    rerender(<HeinzelAvatar label="Pip" tunic="teal" state="waiting" />);
    expect(getByRole("img", { name: "Pip, waiting for you" })).toHaveAttribute("data-state", "waiting");
    expect(container.querySelector('path[fill="#1F9488"]')).toBeInTheDocument();
  });

  it("moves only without reduced motion", () => {
    const { container } = render(<HeinzelAvatar state="working" />);
    for (const group of container.querySelectorAll("g[class]"))
      expect(group.getAttribute("class")).toMatch(/^motion-safe:/);
  });
});
