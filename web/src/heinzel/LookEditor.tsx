// The look editor of a Heinzel (canvas boards 17b and Profile): the whole figure with a radio over each part, and the
// list "Everything you can change" with the choices of the chosen part. Meet your first Heinzelmännchen and the
// Heinzel's page share it, so there is one look editor, not two.
import { type CSSProperties, type KeyboardEvent, type ReactNode, useId } from "react";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { ACCESSORIES, AGES, BEARDS, BUILDS, HATS, headTop, SHIRTS, viewBox } from "@/components/heinzel";
import { Button } from "@/components/ui/button";
import { appearanceLook } from "@/heinzel/look";
import { AGE_LABEL, BEARD_LABEL, BUILD_LABEL, Swatches } from "@/heinzel/PersonalHeinzels";
import type { Look } from "@/heinzel/surprise";
import { type MessageKey, t } from "@/i18n";
import { cn } from "@/lib/utils";

export const PARTS = ["hat", "age", "beard", "shirt", "build", "accessories"] as const;
export type PartId = (typeof PARTS)[number];

/** Where each part sits on the full figure, as canvas y ranges (the drawing is one canvas, no new art). */
function areas(look: Look): Record<PartId, { top: number; height: number }> {
  const top = Number(viewBox(look.age, "full", look.accessories ?? []).split(" ")[1]);
  const high = 860 - top;
  const range = (from: number, to: number) => ({
    top: ((from - top) / high) * 100,
    height: ((to - from) / high) * 100,
  });
  return {
    hat: range(Math.min(headTop(look.age), 180), 270),
    age: range(270, 390),
    beard: range(390, 510),
    shirt: range(510, 660),
    build: range(660, 750),
    accessories: range(750, 860),
  };
}

const partLabel = (part: PartId) => t(`meet.part.${part}` as MessageKey);

function summary(part: PartId, look: Look): string {
  switch (part) {
    case "hat":
      return look.hat ? t(`personal.colour.${look.hat}` as MessageKey) : t("personal.themeDefault");
    case "age":
      return t(AGE_LABEL[look.age]);
    case "beard":
      return look.beard ? t(BEARD_LABEL[look.beard]) : t("personal.ageDefault");
    case "shirt":
      return look.shirt
        ? t(`personal.colour.${look.shirt === "red-brown" ? "redBrown" : look.shirt}` as MessageKey)
        : t("personal.themeDefault");
    case "build":
      return t(BUILD_LABEL[look.build]);
    case "accessories":
      return (look.accessories ?? []).length
        ? (look.accessories ?? []).map((item) => t(`personal.accessory.${item}`)).join(", ")
        : t("personal.beard.none");
  }
}

/** One part of the figure or the list as a radio: which part to change is one choice (design/README.md, Choices). */
function PartRadio({
  id,
  checked,
  label,
  onPick,
  style,
  className,
  children,
}: {
  id: PartId;
  checked: boolean;
  label?: string;
  onPick: (id: PartId) => void;
  style?: CSSProperties;
  className: string;
  children?: ReactNode;
}) {
  return (
    // The shared button as a radio, as the Heinzel cards are: the figure's parts hold no radio input.
    <Button
      type="button"
      variant="ghost"
      role="radio"
      aria-checked={checked}
      tabIndex={checked ? 0 : -1}
      aria-label={label}
      title={label ? partLabel(id) : undefined}
      onClick={() => onPick(id)}
      style={style}
      className={cn("h-auto font-normal", className)}
    >
      {children}
    </Button>
  );
}

const NEXT_KEYS: Record<string, number> = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };

/**
 * The radio pattern for a group of parts: one tab stop (the chosen part), arrow keys move the choice and the focus,
 * Home and End go to the first and the last part.
 */
function onPartKey(event: KeyboardEvent<HTMLDivElement>, part: PartId, setPart: (id: PartId) => void) {
  const at = PARTS.indexOf(part);
  const step = NEXT_KEYS[event.key];
  let next: number;
  if (step !== undefined) next = (at + step + PARTS.length) % PARTS.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = PARTS.length - 1;
  else return;
  const target = PARTS[next];
  if (target === undefined) return;
  event.preventDefault();
  setPart(target);
  event.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]')[next]?.focus();
}

function Choices({ part, look, onLook }: { part: PartId; look: Look; onLook: (look: Look) => void }) {
  const name = useId();
  const radios = <Item extends string>(
    items: readonly Item[],
    labels: Record<Item, MessageKey>,
    value: Item | null,
    set: (item: Item | null) => void,
    extra?: ReactNode,
  ) => (
    <div role="radiogroup" aria-label={partLabel(part)} className="flex flex-wrap gap-2">
      {extra}
      {items.map((item) => (
        <label
          key={item}
          className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border border-border px-3 text-sm has-checked:border-primary has-checked:bg-primary/10"
        >
          <input type="radio" name={`${name}-${part}`} checked={value === item} onChange={() => set(item)} />
          {t(labels[item])}
        </label>
      ))}
    </div>
  );
  switch (part) {
    case "hat":
      return (
        <Swatches
          label={partLabel(part)}
          palette={HATS}
          value={look.hat ?? null}
          onChange={(hat) => onLook({ ...look, hat })}
        />
      );
    case "shirt":
      return (
        <Swatches
          label={partLabel(part)}
          palette={SHIRTS}
          value={look.shirt ?? null}
          onChange={(shirt) => onLook({ ...look, shirt })}
        />
      );
    case "age":
      return radios(AGES, AGE_LABEL, look.age, (age) => age && onLook({ ...look, age }));
    case "build":
      return radios(BUILDS, BUILD_LABEL, look.build, (build) => build && onLook({ ...look, build }));
    case "beard":
      return radios(BEARDS, BEARD_LABEL, look.beard ?? null, (beard) => onLook({ ...look, beard }));
    case "accessories":
      return (
        <fieldset className="flex flex-wrap gap-2">
          <legend className="sr-only">{partLabel(part)}</legend>
          {ACCESSORIES.map((accessory) => (
            <label
              key={accessory}
              className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border border-border px-3 text-sm has-checked:border-primary has-checked:bg-primary/10"
            >
              <input
                type="checkbox"
                checked={(look.accessories ?? []).includes(accessory)}
                onChange={(event) =>
                  onLook({
                    ...look,
                    accessories: event.target.checked
                      ? [...(look.accessories ?? []), accessory]
                      : (look.accessories ?? []).filter((item) => item !== accessory),
                  })
                }
              />
              {t(`personal.accessory.${accessory}`)}
            </label>
          ))}
        </fieldset>
      );
  }
}

/** The whole figure, each part a radio over its place in the drawing. */
export function LookFigure({
  name,
  look,
  part,
  onPart,
}: {
  name: string;
  look: Look;
  part: PartId;
  onPart: (part: PartId) => void;
}) {
  const place = areas(look);
  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="relative w-44 shrink-0"
        style={{
          aspectRatio: viewBox(look.age, "full", look.accessories ?? [])
            .split(" ")
            .slice(2)
            .join(" / "),
        }}
      >
        <HeinzelAvatar label={t("meet.figure", { name })} {...appearanceLook(look)} className="h-full w-full" />
        {/* The part to change is one choice: the figure's parts are its radios, as the list below is. */}
        <div
          role="radiogroup"
          aria-label={t("meet.figureParts")}
          className="absolute inset-0"
          onKeyDown={(event) => onPartKey(event, part, onPart)}
        >
          {PARTS.map((id) => (
            <PartRadio
              key={id}
              id={id}
              checked={part === id}
              label={t("meet.change", { part: partLabel(id).toLowerCase() })}
              onPick={onPart}
              style={{ top: `${place[id].top}%`, height: `${place[id].height}%` }}
              className={cn(
                "absolute inset-x-0 rounded-md focus-visible:outline-2 focus-visible:outline-primary",
                part === id ? "bg-primary/10 ring-2 ring-primary/60" : "hover:bg-primary/5",
              )}
            />
          ))}
        </div>
      </div>
      <p className="max-w-44 text-center text-xs text-muted-foreground">{t("meet.figureHelp")}</p>
    </div>
  );
}

/** "Everything you can change": one radio per part with its current choice, then the choices of the chosen part. */
export function LookParts({
  look,
  part,
  onPart,
  onLook,
}: {
  look: Look;
  part: PartId;
  onPart: (part: PartId) => void;
  onLook: (look: Look) => void;
}) {
  const group = useId();
  return (
    <section aria-labelledby={`${group}-parts`} className="flex flex-col gap-2">
      <h3 id={`${group}-parts`} className="text-sm font-medium">
        {t("meet.parts")}
      </h3>
      <div
        role="radiogroup"
        aria-labelledby={`${group}-parts`}
        className="flex flex-col gap-1"
        onKeyDown={(event) => onPartKey(event, part, onPart)}
      >
        {PARTS.map((id) => (
          <PartRadio
            key={id}
            id={id}
            checked={part === id}
            onPick={onPart}
            className={cn(
              "flex min-h-11 w-full items-center justify-between gap-3 rounded-lg border px-3 text-sm",
              part === id ? "border-primary bg-primary/10" : "border-border",
            )}
          >
            <span>{partLabel(id)}</span>
            <span className="text-muted-foreground">{summary(id, look)}</span>
          </PartRadio>
        ))}
      </div>
      <Choices part={part} look={look} onLook={onLook} />
    </section>
  );
}
