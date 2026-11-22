import { describe, expect, it } from "vitest";
import { releaseTime } from "./releaseTime";

describe("releaseTime", () => {
  it("shows the day, the time and the zone of the viewer", () => {
    const label = releaseTime("2026-11-22T07:41:00Z", "2026-11-22") ?? "";
    expect(label).toMatch(/^22 Nov 2026, \d\d:\d\d \S+$/);
  });

  it("falls back to the day alone, then to nothing", () => {
    expect(releaseTime(null, "2026-11-21")).toBe("21 Nov 2026");
    expect(releaseTime("odd", null)).toBeNull();
    expect(releaseTime(null, null)).toBeNull();
  });
});
