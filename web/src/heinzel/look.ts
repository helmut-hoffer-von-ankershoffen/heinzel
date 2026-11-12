import type { HeinzelLook } from "@/components/heinzel";

type Appearance = {
  age: NonNullable<HeinzelLook["age"]>;
  build: NonNullable<HeinzelLook["build"]>;
  beard?: HeinzelLook["beard"] | null;
  hat?: HeinzelLook["hat"] | null;
  shirt?: HeinzelLook["shirt"] | null;
  accessories?: HeinzelLook["accessories"];
};

/** The avatar's look for a stored appearance: absent choices (null) fall back to the age and theme defaults. */
export function appearanceLook(appearance: Appearance | null | undefined): HeinzelLook {
  if (!appearance) return {};
  return {
    age: appearance.age,
    build: appearance.build,
    beard: appearance.beard ?? undefined,
    hat: appearance.hat ?? undefined,
    shirt: appearance.shirt ?? undefined,
    accessories: appearance.accessories,
  };
}
