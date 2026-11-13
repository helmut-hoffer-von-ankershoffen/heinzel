import type { HeinzelLook } from "@/components/heinzel";
import { type MessageKey, t } from "@/i18n";

type Appearance = {
  age: NonNullable<HeinzelLook["age"]>;
  build: NonNullable<HeinzelLook["build"]>;
  beard?: NonNullable<HeinzelLook["beard"]> | null;
  hat?: NonNullable<HeinzelLook["hat"]> | null;
  shirt?: NonNullable<HeinzelLook["shirt"]> | null;
  accessories?: NonNullable<HeinzelLook["accessories"]>;
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

const STATES = ["proposed", "awake", "asleep", "waiting", "paused", "retired"] as const;

/** A personal Heinzel's lifecycle state in words; an unknown state is shown as the service named it. */
export function stateLabel(state: string): string {
  return (STATES as readonly string[]).includes(state) ? t(`personal.state.${state}` as MessageKey) : state;
}
