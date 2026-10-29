import { describe, expect, it } from "vitest";
import { contributionPath } from "./navigation";

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
});
