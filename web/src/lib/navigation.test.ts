import { describe, expect, it } from "vitest";
import { contributionIsActive, contributionPath } from "./navigation";

const current = { pathname: "/doctor", search: "?view=probe&panel=chat", hash: "#details" };
describe("contributed navigation", () => {
  it("opens an unknown provider's panel without discarding the current page or filters", () => {
    expect(contributionPath("/?panel=unforeseen:inspector", current)).toBe(
      "/doctor?view=probe&panel=unforeseen%3Ainspector#details",
    );
  });
  it("preserves explicitly selected pages and non-panel destinations", () => {
    for (const path of [
      "/plugins/unforeseen/page",
      "/?settings=unforeseen",
      "/doctor?panel=unforeseen:inspector",
      "/?panel=x#anchor",
    ])
      expect(contributionPath(path, current)).toBe(path);
  });

  it("matches only the selected query panel and keeps ordinary route matching", () => {
    expect(contributionIsActive("/?panel=clock", current, true)).toBe(false);
    expect(contributionIsActive("/?panel=chat", current, false)).toBe(true);
    expect(contributionIsActive("/?panel=clock", { ...current, search: "?view=probe" }, true)).toBe(false);
    expect(contributionIsActive("/doctor", current, true)).toBe(true);
    expect(contributionIsActive("/settings", current, false)).toBe(false);
    expect(contributionIsActive("/doctor?panel=conditions", { ...current, search: "?panel=chat" }, true)).toBe(false);
    expect(
      contributionIsActive("/doctor?panel=conditions", { ...current, search: "?panel=conditions&view=probe" }, true),
    ).toBe(true);
    expect(contributionIsActive("/doctor?panel=conditions", { ...current, search: "?view=probe" }, true)).toBe(false);
    expect(contributionIsActive("/doctor?panel=conditions", { ...current, search: "?panel=conditions" }, false)).toBe(
      false,
    );
  });
});
