import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeinzelAvatar } from "./HeinzelAvatar";
import {
  AGES,
  BEARDS,
  BUILDS,
  HATS,
  heinzelShapes,
  heinzelSvg,
  heinzelSwarmSvg,
  SHIRTS,
  type Shape,
  STATES,
  viewBox,
} from "./heinzel";

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

/** Every fill in a drawing, groups included. */
function fills(shapes: Shape[]): string[] {
  return shapes.flatMap((shape) => (shape.kind === "group" ? fills(shape.children) : [shape.fill]));
}

describe("Heinzelmännchen's drawing", () => {
  it("is what heinzel.svg and heinzel-swarm.svg hold, so the icons show the avatars' Heinzelmännchen (make icons)", () => {
    expect(readFileSync(resolve(MACOS, "heinzel.svg"), "utf-8")).toBe(heinzelSvg());
    expect(readFileSync(resolve(MACOS, "heinzel-swarm.svg"), "utf-8")).toBe(heinzelSwarmSvg());
  });

  it("offers eight hat and eight shirt colours, each standing out on a light and on a dark background", () => {
    for (const palette of [HATS, SHIRTS]) {
      expect(Object.keys(palette)).toHaveLength(8);
      for (const colour of Object.values(palette)) {
        expect(contrast(colour, "#ffffff")).toBeGreaterThanOrEqual(3);
        expect(contrast(colour, "#181818")).toBeGreaterThanOrEqual(3);
      }
    }
  });

  it("draws every combination of the five choices and every state, a badge only for a state that needs one", () => {
    for (const age of AGES)
      for (const beard of BEARDS)
        for (const build of BUILDS)
          for (const state of STATES) {
            const shapes = heinzelShapes({ age, beard, build, state, hat: "rust", shirt: "rose" });
            expect(fills(shapes)).toContain(SHIRTS.rose);
            expect(fills(shapes).includes(HATS.rust)).toBe(age !== "baby");
            expect(shapes.some((shape) => shape.part === "badge")).toBe(!["idle", "working"].includes(state));
          }
  });

  it("gives the cap's height to age: none for a baby, then small, medium and tall, and crops each bust to fit", () => {
    const tops = AGES.map((age) => Number(viewBox(age, "bust").split(" ")[1]));
    expect(tops).toEqual([...tops].sort((a, b) => b - a));
    expect(new Set(tops).size).toBe(AGES.length);
    expect(viewBox("grown", "full")).toBe("0 0 512 860");
  });

  it("is grown by default, with a short beard, a red hat, a green shirt and a regular build, never old", () => {
    const look = { age: "grown", beard: "short", hat: "red", shirt: "green", build: "regular", state: "idle" } as const;
    expect(heinzelSvg()).toBe(heinzelSvg(look));
    expect(heinzelSvg()).not.toBe(heinzelSvg({ age: "old" }));
  });

  it("gives a baby no beard, whatever is asked", () => {
    expect(heinzelSvg({ age: "baby", beard: "long" })).toBe(heinzelSvg({ age: "baby" }));
  });
});

describe("HeinzelAvatar", () => {
  it("is decoration without a label, and says its name and state with one", () => {
    const { container, rerender, getByRole } = render(<HeinzelAvatar />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    rerender(<HeinzelAvatar label="Pip" shirt="teal" state="waiting" />);
    expect(getByRole("img", { name: "Pip, waiting for you" })).toHaveAttribute("data-state", "waiting");
    expect(container.querySelector(`path[fill="${SHIRTS.teal}"]`)).toBeInTheDocument();
  });

  it("shows the whole figure by default and the same drawing's bust for small slots", () => {
    const { container, rerender } = render(<HeinzelAvatar age="young" />);
    expect(container.querySelector("svg")).toHaveAttribute("viewBox", viewBox("young", "full"));
    rerender(<HeinzelAvatar age="young" view="bust" />);
    expect(container.querySelector("svg")).toHaveAttribute("viewBox", viewBox("young", "bust"));
  });

  it("moves only without reduced motion", () => {
    const { container } = render(<HeinzelAvatar state="working" />);
    for (const group of container.querySelectorAll("svg > g[class]"))
      expect(group.getAttribute("class")).toMatch(/^motion-safe:/);
  });
});
