import { afterEach, describe, expect, it, vi } from "vitest";
import { isLocalServer } from "./browser";

afterEach(() => vi.restoreAllMocks());

describe("native notification controls", () => {
  it.each([
    ["localhost", true],
    ["127.0.0.1", true],
    ["[::1]", true],
    ["hz.example.test", false],
    ["192.0.2.10", false],
    ["localhost.example.test", false],
  ])("offers server controls on desktop at %s: %s", (hostname, expected) => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15");
    vi.stubGlobal("window", { location: { hostname } });
    expect(isLocalServer()).toBe(expected);
  });

  it("withholds server controls on iPhone even at a loopback address", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15");
    vi.stubGlobal("window", { location: { hostname: "localhost" } });
    expect(isLocalServer()).toBe(false);
  });
});
