import { describe, expect, it } from "vitest";
import { type Look, START_LOOK, SUGGESTED_NAMES, sameLook, surprise, surpriseLook } from "./surprise";

/** A random source that walks a fixed list, so a run is the same every time. */
const seeded = (values: number[]) => {
  let at = 0;
  return () => values[at++ % values.length] as number;
};

describe("surprise me", () => {
  it("never gives the look you have now, over many clicks in a row", () => {
    let look = START_LOOK;
    let seed = 7;
    const random = () => {
      seed = (seed * 16807) % 2147483647;
      return seed / 2147483647;
    };
    for (let click = 0; click < 300; click++) {
      const next = surpriseLook(look, random);
      expect(sameLook(next, look)).toBe(false);
      look = next;
    }
  });

  it("moves on even when every roll hits the current look", () => {
    const roll = surpriseLook(START_LOOK, () => 0);
    expect(
      sameLook(
        roll,
        surpriseLook(START_LOOK, () => 0),
      ),
    ).toBe(true);
    const stuck: Look = {
      age: "baby",
      build: "slim",
      beard: "none",
      hat: "red",
      shirt: "green",
      accessories: ["umbrella", "sunglasses", "scarf"],
    };
    expect(sameLook(roll, stuck)).toBe(true);
    expect(
      sameLook(
        surpriseLook(stuck, () => 0),
        stuck,
      ),
    ).toBe(false);
  });

  it("renames while the name is the suggestion, and keeps a name you typed", () => {
    const fresh = { name: "Helmi", suggested: "Helmi", look: START_LOOK };
    const renamed = surprise(fresh, seeded([0.5]));
    expect(renamed.name).not.toBe("Helmi");
    expect(SUGGESTED_NAMES).toContain(renamed.name as never);
    expect(renamed.suggested).toBe(renamed.name);
    const typed = surprise({ ...fresh, name: "Ada" }, seeded([0.5]));
    expect(typed.name).toBe("Ada");
    expect(typed.suggested).toBe("Helmi");
    expect(sameLook(typed.look, fresh.look)).toBe(false);
    expect(surprise({ ...fresh, name: "" }, seeded([0.5])).name).not.toBe("");
  });
});
