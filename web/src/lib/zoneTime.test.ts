import { describe, expect, it } from "vitest";
import { twelveHourLocale, zoneHour, zoneTime } from "./zoneTime";

// 2026-10-23 15:04 UTC: 17:04 in Berlin, 11:04 am in New York, 8:04 am in Los Angeles, 00:04 in Tokyo the next day,
// 2:04 am in Sydney the next day.
const AT = new Date("2026-10-23T15:04:00Z");

describe("a time in the convention of its place", () => {
  it.each([
    ["Europe/Berlin", "17:04"],
    ["America/New_York", "11:04 am"],
    ["America/Los_Angeles", "8:04 am"],
    ["Asia/Tokyo", "00:04"],
    ["Australia/Sydney", "2:04 am"],
  ])("%s shows %s", (zone, expected) => {
    expect(zoneTime(AT, zone)).toBe(expected);
  });

  it("writes working hours the same way", () => {
    expect(zoneHour(18, "America/New_York")).toBe("6:00 pm");
    expect(zoneHour(9, "Australia/Sydney")).toBe("9:00 am");
    expect(zoneHour(18, "Europe/Berlin")).toBe("18:00");
    expect(zoneHour(24, "Asia/Tokyo")).toBe("24:00");
    expect(zoneHour(24, "America/Chicago")).toBe("12:00 am");
  });

  it("knows the 12-hour countries by their zones, and nothing else", () => {
    expect(twelveHourLocale("America/Indiana/Indianapolis")).toBe("en-US");
    expect(twelveHourLocale("Asia/Kolkata")).toBe("en-IN");
    expect(twelveHourLocale("America/Mexico_City")).toBeNull();
    expect(twelveHourLocale("Europe/London")).toBeNull();
  });
});
