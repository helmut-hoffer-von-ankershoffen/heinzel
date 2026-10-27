// Keep new screens on the shared phone-safe geometry path instead of reintroducing layout-viewport overlays.
import { expect, it } from "vitest";

const screens = import.meta.glob<string>("../**/*.tsx", { query: "?raw", import: "default", eager: true });

it("requires viewport-aware sizing and the shared primitive for dropdowns", () => {
  const violations = Object.entries(screens).flatMap(([file, source]) => {
    if (file.endsWith(".test.tsx")) return [];
    return [
      /100vh|\bh-screen\b/.test(source) && `${file}: use the visible viewport for full-height UI`,
      /window\.innerHeight/.test(source) && `${file}: use viewportBounds instead of layout viewport height`,
      /absolute (?:top-full|bottom-\d)[^"\n]*z-\(--layer-popover\)/.test(source) &&
        `${file}: use AnchoredPopover for dropdowns`,
    ].filter(Boolean);
  });
  expect(violations).toEqual([]);
});
