import { describe, expect, it } from "vitest";
import { everyday, expert } from "@/test/fixtures";
import { listed, type NavSection, sections, visibleSections } from "./navigation";

const labels = (all: NavSection[]) => all.flatMap((section) => [section.label, ...section.items.map((i) => i.label)]);

describe("listed", () => {
  it("lists screens without an operation always, and everyday ones in every mode", () => {
    expect(listed(undefined, undefined)).toBe(true);
    expect(listed("system.doctor:doctor", everyday)).toBe(true);
    expect(listed("system.doctor:doctor", expert)).toBe(true);
  });

  it("lists screens of expert operations in expert mode only", () => {
    expect(listed("workflow:graph", everyday)).toBe(false);
    expect(listed("workflow:graph", expert)).toBe(true);
  });

  it("lists no operation's screen before the mode is known, nor one the API doesn't have", () => {
    expect(listed("system.doctor:doctor", undefined)).toBe(false);
    expect(listed("plugin:gone", expert)).toBe(false);
  });
});

describe("visibleSections", () => {
  it("keeps every section and screen in expert mode", () => {
    expect(labels(visibleSections(sections, expert))).toEqual(labels(sections));
  });

  it("drops expert screens, and sections left empty, outside it; the contracts section stays for its entries", () => {
    expect(labels(visibleSections(sections, everyday))).toEqual([
      "nav.home",
      "nav.settings",
      "nav.contracts",
      "nav.organisation",
      "nav.team",
      "nav.doctor",
    ]);
  });
});
