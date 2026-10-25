import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeinzelAvatar } from "./HeinzelAvatar";
import {
  ACCESSORIES,
  AGES,
  BEARDS,
  BUILDS,
  HATS,
  heinzelShapes,
  heinzelSvg,
  heinzelSwarmSvg,
  isAccessory,
  parseAccessories,
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
  return shapes.flatMap((shape) =>
    shape.kind === "group" ? fills(shape.children) : shape.kind === "image" ? [] : [shape.fill],
  );
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

/** Every shape tagged as this accessory's layer, groups searched. */
function layerOf(shapes: Shape[], accessory: string): Shape[] {
  return shapes.flatMap((shape) => [
    ...(shape.accessory === accessory ? [shape] : []),
    ...(shape.kind === "group" ? layerOf(shape.children, accessory) : []),
  ]);
}

describe("Heinzelmännchen's accessories", () => {
  it("are a closed set that parses untrusted data: known names once each, in layering order, the rest dropped", () => {
    expect(ACCESSORIES).toEqual(["umbrella", "sunglasses", "scarf"]);
    expect(parseAccessories(["scarf", "umbrella", "scarf", "crown", 7, null])).toEqual(["umbrella", "scarf"]);
    expect(parseAccessories("umbrella")).toEqual([]);
    expect(parseAccessories(undefined)).toEqual([]);
    expect(ACCESSORIES.every(isAccessory)).toBe(true);
    expect(isAccessory("crown")).toBe(false);
  });

  it("change nothing when there are none, or only unknown ones", () => {
    expect(heinzelSvg({ accessories: [] })).toBe(heinzelSvg());
    expect(heinzelSvg({ accessories: ["crown"] as never })).toBe(heinzelSvg());
    expect(viewBox("old", "full", [])).toBe("0 0 512 860");
  });

  it("are drawn for every age, build, state and view, each as its own layer and only when asked", () => {
    for (const age of AGES)
      for (const build of BUILDS)
        for (const state of STATES)
          for (const accessory of ACCESSORIES) {
            const shapes = heinzelShapes({ age, build, state, accessories: [accessory] });
            expect(layerOf(shapes, accessory).length).toBeGreaterThan(0);
            for (const other of ACCESSORIES.filter((a) => a !== accessory))
              expect(layerOf(shapes, other)).toHaveLength(0);
            for (const view of ["full", "bust"] as const) {
              const svg = heinzelSvg({ age, build, state, accessories: [accessory] }, view);
              expect(svg).not.toBe(heinzelSvg({ age, build, state }, view));
              expect(svg).not.toContain("NaN");
            }
          }
  });

  it("let a baby wear all of them, and layer together", () => {
    const shapes = heinzelShapes({ age: "baby", accessories: [...ACCESSORIES] });
    for (const accessory of ACCESSORIES) expect(layerOf(shapes, accessory).length).toBeGreaterThan(0);
  });

  it("make room above the head for an umbrella, keeping the bust square and the bottom where it was", () => {
    for (const age of AGES) {
      const [x = 0, y = 0, w = 0, h = 0] = viewBox(age, "bust", ["umbrella"]).split(" ").map(Number);
      const [bx = 0, by = 0, side = 0] = viewBox(age, "bust").split(" ").map(Number);
      expect(w).toBe(h);
      expect(y).toBeLessThanOrEqual(by);
      expect(y + h).toBe(by + side);
      expect(x + w / 2).toBe(bx + side / 2);
      expect(x + w).toBeGreaterThanOrEqual(515);
      expect(viewBox(age, "full", ["umbrella"])).toMatch(/^0 -?\d+ 512 \d+$/);
    }
    expect(viewBox("old", "full", ["umbrella"])).not.toBe("0 0 512 860");
    expect(viewBox("old", "bust", ["sunglasses", "scarf"])).toBe(viewBox("old", "bust"));
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

  it("keeps accessories decoration, and names them only when it has a name", () => {
    const { container, rerender, getByRole } = render(<HeinzelAvatar accessories={["umbrella", "scarf"]} />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector("svg")).not.toHaveAttribute("aria-label");
    rerender(<HeinzelAvatar label="Pip" state="working" accessories={["scarf", "sunglasses", "bogus" as never]} />);
    expect(getByRole("img", { name: "Pip, working, wearing sunglasses, wearing a scarf" })).toBeInTheDocument();
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

describe("HeinzelAvatar idle life", () => {
  it("draws the eyes, the hat and the waving hand as their own groups so they can move", () => {
    const { container } = render(<HeinzelAvatar age="grown" />);
    for (const part of ["eyes", "hat", "wave"]) expect(container.querySelector(`g.hz-${part}`)).not.toBeNull();
  });

  it("has no waving hand when it holds an umbrella", () => {
    const { container } = render(<HeinzelAvatar accessories={["umbrella"]} />);
    expect(container.querySelector("g.hz-wave")).toBeNull();
  });
});

describe("theme dressing", () => {
  const winter = { palette: { hat: "#aabbcc", shirt: "#336699", scarf: "#ddeeff" }, decorations: ["snow"] as const };
  it("dresses every age and build, preserves the baby's bare head and never duplicates a scarf", () => {
    for (const age of AGES)
      for (const build of BUILDS) {
        const shapes = heinzelShapes({ age, build, accessories: ["scarf"], theme: winter });
        expect(shapes.filter((shape) => shape.accessory === "scarf")).toHaveLength(1);
        expect(fills(shapes)).toContain(winter.palette.shirt);
        if (age === "baby") expect(fills(shapes)).not.toContain(winter.palette.hat);
        else expect(fills(shapes)).toContain(winter.palette.hat);
      }
  });
  it("keeps personal colours ahead of the palette, and returns to the original drawing without a theme", () => {
    const personal = fills(heinzelShapes({ hat: "red", shirt: "green", theme: winter }));
    expect(personal).toContain(HATS.red);
    expect(personal).toContain(SHIRTS.green);
    expect(personal).not.toContain(winter.palette.hat);
    expect(heinzelShapes({ theme: undefined })).toEqual(heinzelShapes());
  });
  it("accepts a short hex palette without invalid mixed colours", () => {
    const colours = fills(
      heinzelShapes({ theme: { palette: { shirt: "#abc", scarf: "#def" }, decorations: ["snow"] } }),
    );
    expect(colours.some((colour) => colour.includes("NaN"))).toBe(false);
  });
});

describe("theme avatar SVG layers", () => {
  const asset = (slot: string) => `/theme-assets/winter/heinzel-${slot}.svg?v=0123456789abcdef`;
  it("keeps personal colours, expressions, accessories and movable hat grouping for each age and build", () => {
    for (const age of AGES)
      for (const build of BUILDS)
        for (const state of STATES) {
          const assets = { [`hat-${age}`]: asset(`hat-${age}`), [`outfit-${age}`]: asset(`outfit-${age}`) };
          const { container, unmount } = render(
            <HeinzelAvatar
              age={age}
              build={build}
              state={state}
              hat="purple"
              shirt="rose"
              accessories={["sunglasses", "scarf"]}
              theme={{ assets }}
            />,
          );
          const images = container.querySelectorAll("image");
          expect(images).toHaveLength(age === "baby" ? 1 : 2);
          if (age !== "baby")
            expect(container.querySelector(".hz-hat image")).toHaveAttribute("href", asset(`hat-${age}`));
          expect(container.querySelector(`path[fill="${SHIRTS.rose}"]`)).not.toBeNull();
          expect(container.querySelector("svg")).toHaveAttribute("data-state", state);
          expect(container.querySelector(".hz-eyes")).not.toBeNull();
          unmount();
        }
  });
  it("drops unsafe URLs and leaves the complete personal drawing when a layer fails", () => {
    const { container, rerender } = render(
      <HeinzelAvatar theme={{ assets: { "hat-grown": "https://example.invalid/a.svg" } }} />,
    );
    expect(container.querySelector("image")).toBeNull();
    const corePaths = container.querySelectorAll("path").length;
    rerender(<HeinzelAvatar theme={{ assets: { "hat-grown": asset("hat-grown") } }} />);
    const image = container.querySelector("image");
    expect(image).not.toBeNull();
    fireEvent.error(image as SVGImageElement);
    expect(image).toHaveStyle({ display: "none" });
    expect(container.querySelectorAll("path")).toHaveLength(corePaths);
    rerender(
      <HeinzelAvatar
        theme={{ assets: { "hat-grown": asset("hat-grown").replace("0123456789abcdef", "fedcba9876543210") } }}
      />,
    );
    expect(container.querySelector("image")).not.toHaveStyle({ display: "none" });
    rerender(<HeinzelAvatar />);
    expect(container.querySelector("image")).toBeNull();
  });
});
