// The nine states of one kind of chat as faces: each has its own pose or badge shape and its own word, only
// "working" moves, and an idle Heinzel is awake in an open chat and dozing elsewhere.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { heinzelSvg } from "@/components/heinzel";
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

  it("has a badge shape and a word for every state but idle, never colour alone", () => {
    const words = new Set<string>();
    for (const activity of ACTIVITIES) {
      const word = activityWord(activity);
      expect(word).toBeTruthy();
      words.add(word);
      const { container } = render(<HeinzelAvatar state={activityPose(activity, false)} view="bust" />);
      const svg = container.querySelector("svg");
      expect(svg).toHaveAttribute("data-state", activityPose(activity, false));
      // Idle has no badge (awake) or dozes (elsewhere); every other state carries one.
      expect(container.querySelectorAll("svg > g").length).toBe(activity === "idle" ? 1 : 2);
    }
    expect(words.size).toBe(ACTIVITIES.length);
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

  it("moves only for working, and reduced motion stops that bob", () => {
    for (const activity of ACTIVITIES) {
      const { container } = render(<HeinzelAvatar state={activityPose(activity, true)} />);
      const moving = [...container.querySelectorAll("svg g[class]")].filter((g) =>
        /animate/.test(g.getAttribute("class") ?? ""),
      );
      if (activity === "working") {
        expect(moving).toHaveLength(1);
        expect(moving[0]?.getAttribute("class")).toMatch(/^motion-safe:animate-\[hz-bob/);
      } else expect(moving.filter((g) => /hz-bob/.test(g.getAttribute("class") ?? ""))).toHaveLength(0);
    }
  });

  it("holds the badge still for the new states", () => {
    for (const activity of ["paused", "overloaded", "working", "waiting_on_others", "asleep"] as const) {
      const { container } = render(<HeinzelAvatar state={activityPose(activity, true)} view="bust" />);
      expect(
        container.querySelector("svg > g.motion-safe\\:animate-\\[hz-breathe_2\\.4s_ease-in-out_infinite\\]"),
      ).toBeNull();
    }
  });
});
