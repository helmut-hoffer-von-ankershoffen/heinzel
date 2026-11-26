import { describe, expect, it } from "vitest";
import { releaseTime } from "./releaseTime";

describe("releaseTime", () => {
  it("shows the day, the time and the zone of the viewer", () => {
    const publishedAt = "2026-11-22T07:41:00Z";
    const local = new Date(publishedAt);
    const day = local.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
    const clock = [local.getHours(), local.getMinutes()].map((part) => String(part).padStart(2, "0")).join(":");
    const prefix = `${day}, ${clock} `;
    const label = releaseTime(publishedAt, "2026-11-22") ?? "";
    expect(label.slice(0, prefix.length)).toBe(prefix);
    expect(label.slice(prefix.length)).toMatch(/^\S+$/);
  });

  it("falls back to the day alone, then to nothing", () => {
    expect(releaseTime(null, "2026-11-21")).toBe("21 Nov 2026");
    expect(releaseTime("odd", null)).toBeNull();
    expect(releaseTime(null, null)).toBeNull();
  });
});
