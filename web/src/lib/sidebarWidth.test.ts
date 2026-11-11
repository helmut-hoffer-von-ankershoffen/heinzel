import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clampSidebar,
  readSidebarWidth,
  SIDEBAR_DEFAULT,
  SIDEBAR_MIN,
  sidebarMax,
  writeSidebarWidth,
} from "./sidebarWidth";

describe("sidebar width", () => {
  beforeEach(() => localStorage.clear());

  it("keeps 320 px up to 70% of the window and 360 px for the content", () => {
    expect(sidebarMax(2000)).toBe(1400);
    expect(sidebarMax(1280, 1000)).toBe(640);
    expect(sidebarMax(1280, 1280)).toBe(896);
    expect(sidebarMax(600, 600)).toBe(SIDEBAR_MIN);
    expect(clampSidebar(100, 1280)).toBe(SIDEBAR_MIN);
    expect(clampSidebar(5000, 1280, 1000)).toBe(640);
    expect(clampSidebar(500, 1280)).toBe(500);
    expect(clampSidebar(Number.NaN, 1280)).toBe(SIDEBAR_DEFAULT);
  });

  it("persists the width and falls back to the default for nothing, junk or no storage", () => {
    expect(readSidebarWidth()).toBe(SIDEBAR_DEFAULT);
    writeSidebarWidth(512.4);
    expect(readSidebarWidth()).toBe(512);
    localStorage.setItem("hz.workspace.width", "wide");
    expect(readSidebarWidth()).toBe(SIDEBAR_DEFAULT);
    const broken = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(readSidebarWidth()).toBe(SIDEBAR_DEFAULT);
    broken.mockRestore();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(() => writeSidebarWidth(400)).not.toThrow();
    vi.restoreAllMocks();
  });
});
