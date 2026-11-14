// "Surprise me" for the first start (canvas boards 17 and 17b): a new look from the choices the drawing already has
// (age, build, beard, hat and shirt colour, accessories), never the one you have now, and a new suggested name while
// the name is still the suggestion. Pure functions with the random source passed in, so tests can pin it.
import type { SchemaHeinzelAppearance } from "@/api/client";
import { ACCESSORIES, AGES, BEARDS, BUILDS, HATS, SHIRTS } from "@/components/heinzel";

export type Look = SchemaHeinzelAppearance;
export type Random = () => number;

/** Names offered as the suggestion; the first one is what the first start shows. */
export const SUGGESTED_NAMES = ["Helmi", "Fritz", "Mila", "Otto", "Pia", "Kasimir", "Lotte", "Emil"] as const;

export const START_LOOK: Look = {
  age: "grown",
  build: "regular",
  beard: null,
  hat: null,
  shirt: null,
  accessories: [],
};

function pick<T>(items: readonly T[], random: Random): T {
  return items[Math.min(items.length - 1, Math.floor(random() * items.length))] as T;
}

/** Whether two looks draw the same figure (an absent choice and a null one are the same). */
export function sameLook(a: Look, b: Look): boolean {
  const accessories = (look: Look) => ACCESSORIES.filter((item) => look.accessories?.includes(item)).join(",");
  return (
    a.age === b.age &&
    a.build === b.build &&
    (a.beard ?? null) === (b.beard ?? null) &&
    (a.hat ?? null) === (b.hat ?? null) &&
    (a.shirt ?? null) === (b.shirt ?? null) &&
    accessories(a) === accessories(b)
  );
}

function roll(random: Random): Look {
  return {
    age: pick(AGES, random),
    build: pick(BUILDS, random),
    beard: pick(BEARDS, random),
    hat: pick(Object.keys(HATS) as (keyof typeof HATS)[], random),
    shirt: pick(Object.keys(SHIRTS) as (keyof typeof SHIRTS)[], random),
    accessories: ACCESSORIES.filter(() => random() < 0.25),
  };
}

/** A look that differs from `current`; if the dice keep hitting it, the hat moves on, so a click never repeats. */
export function surpriseLook(current: Look, random: Random = Math.random): Look {
  for (let attempt = 0; attempt < 8; attempt++) {
    const next = roll(random);
    if (!sameLook(next, current)) return next;
  }
  const hats = Object.keys(HATS) as (keyof typeof HATS)[];
  const at = hats.indexOf((current.hat ?? hats[0]) as keyof typeof HATS);
  return { ...roll(random), hat: hats[(at + 1) % hats.length] };
}

/** A suggested name other than `current`. */
export function surpriseName(current: string, random: Random = Math.random): string {
  const others = SUGGESTED_NAMES.filter((name) => name !== current);
  return pick(others, random);
}

export type Meeting = { name: string; suggested: string; look: Look };

/** One Surprise me click: a new look; a new name too, but only while the name is still the suggestion. */
export function surprise(current: Meeting, random: Random = Math.random): Meeting {
  const look = surpriseLook(current.look, random);
  const untouched = current.name.trim() === "" || current.name === current.suggested;
  if (!untouched) return { ...current, look };
  const name = surpriseName(current.suggested, random);
  return { name, suggested: name, look };
}
