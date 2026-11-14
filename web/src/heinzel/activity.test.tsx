// The nine states of one kind of chat as faces: each has its own pose or badge shape and its own word, only
// "working" moves, and an idle Heinzel is awake in an open chat and dozing elsewhere.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { heinzelShapes, heinzelSvg } from "@/components/heinzel";
import { t } from "@/i18n";
import { ACTIVITIES, activityPose, activityWord } from "./activity";

const drawing = (activity: (typeof ACTIVITIES)[number], awake = false) =>
  heinzelSvg({ state: activityPose(activity, awake) }, "full");

describe("the nine states on the face", () => {
  it("draws each of the nine in its own way", () => {
    const awake = new Set(ACTIVITIES.map((activity) => drawing(activity, true)));
    expect(awake.size).toBe(ACTIVITIES.length);
    expect(drawing("idle", false)).not.toBe(drawing("idle", true));
  });

  it("has its own badge shape and word for every state but idle, never colour alone", () => {
    const words = new Set<string>();
    const badges = new Map<string, string>();
    for (const activity of ACTIVITIES) {
      words.add(activityWord(activity));
      const shapes = heinzelShapes({ age: "grown", state: activityPose(activity, false) });
      const badge = JSON.stringify(shapes.filter((shape) => shape.part === "badge"));
      if (activity !== "idle") badges.set(activity, badge);
      else expect(badge).toBe("[]");
    }
    expect(words.size).toBe(ACTIVITIES.length);
    expect(badges.size).toBe(ACTIVITIES.length - 1);
    expect(new Set(badges.values()).size).toBe(badges.size);
    // Not even the colour is shared, so a drawing without the colour still differs by its mark.
    const marks = [...badges.values()].map((badge) => badge.replace(/#[0-9A-Fa-f]{6}/g, ""));
    expect(new Set(marks).size).toBe(marks.length);
  });

  it("says done, never made, and keeps the words the canvas names", () => {
    expect(activityWord("waiting_for_you")).toBe("Waiting for you");
    expect(activityWord("proud")).toBe("Done and proud");
    for (const activity of ACTIVITIES) expect(activityWord(activity)).not.toMatch(/made/i);
    expect(activityWord(undefined)).toBe(t("activity.idle"));
    expect(activityWord("something-new" as never)).toBe(t("activity.idle"));
  });

  it("draws the hammer, the parcels and the stop sign only for their states", () => {
    const tray = heinzelSvg({ state: "working" }, "full");
    for (const state of ["hammering", "overloaded", "watching"] as const) {
      expect(heinzelSvg({ state }, "full")).not.toBe(tray);
    }
    // The tray with the mug is today's "working" only; the new drawings do not carry it.
    expect(heinzelSvg({ state: "hammering" }, "full")).not.toContain("#C98A1E");
    expect(heinzelSvg({ state: "overloaded" }, "full")).not.toContain("#C98A1E");
  });

  it("moves only for working: no other state animates anything, in the figure or the badge", () => {
    for (const activity of ACTIVITIES) {
      for (const awake of [true, false]) {
        const { container, unmount } = render(<HeinzelAvatar state={activityPose(activity, awake)} />);
        const moving = [...container.querySelectorAll("svg g[class]")].filter((g) =>
          /animate/.test(g.getAttribute("class") ?? ""),
        );
        if (activity === "working") {
          expect(moving).toHaveLength(1);
          // Reduced motion stops it: the animation applies only where motion is safe.
          expect(moving[0]?.getAttribute("class")).toMatch(/^motion-safe:animate-\[hz-bob/);
        } else expect(moving).toHaveLength(0);
        unmount();
      }
    }
  });

  it("draws every colour of a badge from a design token", () => {
    const tokens = readFileSync(resolve("../design/tokens.json"), "utf-8").toLowerCase();
    const source = readFileSync(resolve("src/components/heinzel.ts"), "utf-8");
    const used = [...source.matchAll(/return mark\("(#[0-9A-Fa-f]{6})"/g)].map(
      (match) => match[1]?.toLowerCase() ?? "",
    );
    expect(used.length).toBeGreaterThanOrEqual(9);
    for (const colour of used) expect(tokens).toContain(`"${colour}"`);
    // Two states never share one badge colour (the old sleeping badge is retired for asleep).
    expect(new Set(used).size).toBe(used.length);
  });
});
