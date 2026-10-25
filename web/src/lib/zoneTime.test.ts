import { describe, expect, it } from "vitest";
import { zoneClock, zoneHour, zoneTime } from "./zoneTime";

// 2026-10-23 15:04:09 UTC: 17:04 in Berlin, 11:04 in New York, 00:04 in Tokyo the next day.
const AT = new Date("2026-10-23T15:04:09Z");

describe("times are always on the 24-hour clock", () => {
  it.each([
    ["Europe/Berlin", "17:04"],
    ["America/New_York", "11:04"],
    ["Asia/Tokyo", "00:04"],
    ["Australia/Sydney", "02:04"],
  ])("%s shows %s", (zone, expected) => {
    expect(zoneTime(AT, zone)).toBe(expected);
  });

  it("shows seconds and an afternoon hour without am or pm", () => {
    expect(zoneClock(AT, "America/New_York")).toBe("11:04:09");
    expect(zoneClock(new Date("2026-10-23T20:45:00Z"), "UTC")).toBe("20:45:00");
    expect(zoneTime(new Date("2026-10-23T20:45:00Z"), "UTC")).toBe("20:45");
  });

  it("writes working hours the same way", () => {
    expect(zoneHour(18)).toBe("18:00");
    expect(zoneHour(9)).toBe("09:00");
  });
});
