// The chat's days and times, in a zone given on purpose (never the runner's): headings across midnight, a day that is
// 23 and one that is 25 hours long, "Today" renewed after midnight, and a dated message after an undated one.
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dayHeading, dayStarts, fullMoment, timeOfDay, useNow } from "@/lib/dayTime";

afterEach(() => vi.useRealTimers());

const berlin = "Europe/Berlin";

describe("dayHeading", () => {
  const now = new Date("2026-11-14T15:00:00Z");
  it("puts the same instant on different days in different zones", () => {
    const morning = "2026-11-14T08:00:00Z";
    expect(dayHeading(morning, now, berlin)).toBe("Today"); // 09:00 on the 14th
    expect(dayHeading(morning, now, "Pacific/Auckland")).toBe("Yesterday"); // 21:00 on the 14th, it is the 15th there
    const night = "2026-11-14T03:00:00Z";
    expect(dayHeading(night, now, berlin)).toBe("Today"); // 04:00 on the 14th
    expect(dayHeading(night, now, "America/New_York")).toBe("Yesterday"); // 22:00 on the 13th
  });
  it("names the weekday and date, and the year only when it is not this year", () => {
    expect(dayHeading("2026-11-12T10:00:00Z", now, berlin)).toBe("Thursday 12 November");
    expect(dayHeading("2025-12-31T10:00:00Z", now, berlin)).toBe("Wednesday 31 December 2025");
  });
});

describe("times", () => {
  it("reads the 24-hour clock in the zone", () => {
    expect(timeOfDay("2026-11-14T08:05:00Z", berlin)).toBe("09:05");
    expect(timeOfDay("2026-11-14T08:05:00Z", "Pacific/Auckland")).toBe("21:05");
    expect(timeOfDay("2026-11-14T08:05:00Z", "America/New_York")).toBe("03:05");
  });
  it("gives the screen reader the whole moment", () => {
    expect(fullMoment("2026-11-14T08:05:00Z", berlin)).toBe("Saturday 14 November 2026, 09:05");
  });
});

describe("dayStarts", () => {
  it("starts a day at the first message and at each change of day, not within one", () => {
    const at = ["2026-11-13T09:00:00Z", "2026-11-13T10:00:00Z", "2026-11-14T09:00:00Z"];
    expect([...dayStarts(at, berlin)]).toEqual([0, 2]);
  });
  it("splits at midnight in the zone, not in UTC", () => {
    const at = ["2026-11-13T22:30:00Z", "2026-11-13T23:30:00Z"]; // 23:30 and 00:30 in Berlin
    expect([...dayStarts(at, berlin)]).toEqual([0, 1]);
    expect([...dayStarts(at, "UTC")]).toEqual([0]);
  });
  it("compares against the last dated message, so an undated one never repeats a heading", () => {
    const at = ["2026-11-13T09:00:00Z", undefined, "2026-11-13T11:00:00Z", undefined, "2026-11-14T11:00:00Z"];
    expect([...dayStarts(at, berlin)]).toEqual([0, 4]);
  });
  it("keeps a 23-hour day (spring) and a 25-hour day (autumn) whole", () => {
    const spring = ["2026-03-28T23:30:00Z", "2026-03-29T21:30:00Z", "2026-03-29T22:30:00Z"]; // 00:30, 23:30, 00:30 next
    expect([...dayStarts(spring, berlin)]).toEqual([0, 2]);
    const autumn = ["2026-10-24T22:30:00Z", "2026-10-25T22:30:00Z", "2026-10-25T23:30:00Z"]; // 00:30, 23:30, 00:30 next
    expect([...dayStarts(autumn, berlin)]).toEqual([0, 2]);
  });
});

describe("useNow", () => {
  it("renews just after midnight in the zone, so Today does not outlive its day", () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(new Date("2026-11-14T22:59:00Z")); // 23:59 in Berlin
    const { result } = renderHook(() => useNow(berlin));
    const message = "2026-11-14T10:00:00Z";
    expect(dayHeading(message, result.current, berlin)).toBe("Today");
    act(() => {
      vi.advanceTimersByTime(62_000);
    });
    expect(dayHeading(message, result.current, berlin)).toBe("Yesterday");
  });
});
