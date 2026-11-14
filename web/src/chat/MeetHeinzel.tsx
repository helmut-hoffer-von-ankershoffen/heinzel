// Meet your first Heinzelmännchen (ADR 0157, canvas boards 17, 17b and 18): a name, the figure with its look
// editor (click a part, or use the list "Everything you can change"), Surprise me with Undo, and the first mission.
// Used by the chat's first start and by the onboarding step 7: one first start, not two.
// The request id is kept until hz confirms, so a lost answer recovers the same Heinzel instead of making a second.
import { useQueryClient } from "@tanstack/react-query";
import { Shuffle, Undo2 } from "lucide-react";
import { type CSSProperties, type FormEvent, type KeyboardEvent, type ReactNode, useId, useRef, useState } from "react";
import { api, problem } from "@/api/client";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { ACCESSORIES, AGES, BEARDS, BUILDS, HATS, headTop, SHIRTS, viewBox } from "@/components/heinzel";
import { Button } from "@/components/ui/button";
import { appearanceLook } from "@/heinzel/look";
import { MISSIONS } from "@/heinzel/missions";
import { AGE_LABEL, BEARD_LABEL, BUILD_LABEL, Swatches } from "@/heinzel/PersonalHeinzels";
import { type Look, type Meeting, START_LOOK, SUGGESTED_NAMES, surprise } from "@/heinzel/surprise";
import { type MessageKey, t } from "@/i18n";
import { useComposerFocus } from "@/lib/chatIntent";
import { cn } from "@/lib/utils";
import { rememberHeinzel } from "./defaultHeinzel";

const REQUEST = "hz.chat.first-heinzel";
const SESSION = /^hz-[0-9A-HJKMNP-TV-Z]{26}$/;

/** The request id for this name and look: kept until hz confirms, so a retry recovers the same Heinzel. */
function requestId(key: string): string {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(REQUEST) ?? "null");
    if (stored && typeof stored === "object" && "id" in stored && "name" in stored && typeof stored.id === "string") {
      if (stored.name === key) return stored.id;
    }
    const fresh = crypto.randomUUID();
    localStorage.setItem(REQUEST, JSON.stringify({ id: fresh, name: key }));
    return fresh;
  } catch {
    return crypto.randomUUID();
  }
}

/** Forget the first-start request: hz confirmed it, or the list already shows a Heinzel. */
export function settleFirstHeinzel() {
  try {
    localStorage.removeItem(REQUEST);
  } catch {
    // Nothing to forget without storage.
  }
}

const PARTS = ["hat", "age", "beard", "shirt", "build", "accessories"] as const;
type PartId = (typeof PARTS)[number];

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

export function MeetHeinzel({ onCreated }: { onCreated: (sessionId: string) => void }) {
  const client = useQueryClient();
  const [meeting, setMeeting] = useState<Meeting>({
    name: SUGGESTED_NAMES[0],
    suggested: SUGGESTED_NAMES[0],
    look: START_LOOK,
  });
  const [earlier, setEarlier] = useState<Meeting[]>([]);
  const [part, setPart] = useState<PartId>("hat");
  const [mission, setMission] = useState<string>(MISSIONS[0]?.id ?? "day");
  const [announce, setAnnounce] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const creating = useRef(false);
  const field = useId();
  const nameInput = useRef<HTMLInputElement>(null);
  useComposerFocus(nameInput, true);
  const group = useId();
  const { name, look } = meeting;
  const chosen = MISSIONS.find((item) => item.id === mission) ?? MISSIONS[0];
  const shown = name.trim() || meeting.suggested;
  const place = areas(look);
  // Every change of the look or the name is one step back; typing a name is one step per burst, not per letter.
  const change = (next: Meeting) => {
    setEarlier((steps) => [...steps, meeting]);
    setMeeting(next);
  };
  const rollAgain = () => {
    const next = surprise(meeting);
    change(next);
    setAnnounce(t("meet.surprised", { name: next.name.trim() || next.suggested }));
  };
  const undo = () => {
    const previous = earlier[earlier.length - 1];
    if (!previous) return;
    setEarlier((steps) => steps.slice(0, -1));
    // The look goes back; the name only while it is still the suggestion, so Undo never eats what you typed.
    const untouched = name === meeting.suggested;
    setMeeting(untouched ? previous : { ...meeting, look: previous.look });
    setAnnounce("");
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (creating.current) return;
    creating.current = true;
    setBusy(true);
    setFailed(false);
    try {
      const { data, error, response } = await api.POST("/heinzel/new-chat", {
        body: { request_id: requestId(`${shown}|${JSON.stringify(look)}`), name: shown, appearance: look },
      });
      if (!response.ok || !data) throw problem(response.status, error);
      if (!SESSION.test(data.session_id)) throw new Error("Invalid creation acknowledgement");
      settleFirstHeinzel();
      rememberHeinzel(data.session_id);
      client.setQueryData(["heinzel", "session", data.session_id], data);
      await client.invalidateQueries({ queryKey: ["heinzel", "sessions"] });
      onCreated(data.session_id);
    } catch {
      setFailed(true);
      // A lost answer may still have made the Heinzel: the panel opens it as soon as the list shows it.
      void client.invalidateQueries({ queryKey: ["heinzel", "sessions"] });
    } finally {
      creating.current = false;
      setBusy(false);
    }
  };
  return (
    <form onSubmit={(event) => void submit(event)} className="flex w-full max-w-2xl flex-col gap-5 text-left">
      <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
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
            <HeinzelAvatar
              label={t("meet.figure", { name: shown })}
              {...appearanceLook(look)}
              className="h-full w-full"
            />
            {/* The part to change is one choice: the figure's parts are its radios, as the list below is. */}
            <div
              role="radiogroup"
              aria-label={t("meet.figureParts")}
              className="absolute inset-0"
              onKeyDown={(event) => onPartKey(event, part, setPart)}
            >
              {PARTS.map((id) => (
                <PartRadio
                  key={id}
                  id={id}
                  checked={part === id}
                  label={t("meet.change", { part: partLabel(id).toLowerCase() })}
                  onPick={setPart}
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
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label htmlFor={field} className="text-sm font-medium">
              {t("oneChat.nameLabel")}
            </label>
            <input
              id={field}
              ref={nameInput}
              value={name}
              maxLength={100}
              autoComplete="off"
              onChange={(event) => setMeeting({ ...meeting, name: event.target.value })}
              className="h-11 rounded-lg border border-border bg-card px-3"
            />
            <p className="text-xs text-muted-foreground">{t("meet.nameHelp")}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" className="min-h-11" onClick={rollAgain}>
              <Shuffle aria-hidden />
              {t("meet.surprise")}
            </Button>
            <Button type="button" variant="ghost" className="min-h-11" disabled={earlier.length === 0} onClick={undo}>
              <Undo2 aria-hidden />
              {t("meet.undo")}
            </Button>
          </div>
          <p role="status" className="sr-only">
            {announce}
          </p>
          <section aria-labelledby={`${group}-parts`} className="flex flex-col gap-2">
            <h3 id={`${group}-parts`} className="text-sm font-medium">
              {t("meet.parts")}
            </h3>
            <div
              role="radiogroup"
              aria-labelledby={`${group}-parts`}
              className="flex flex-col gap-1"
              onKeyDown={(event) => onPartKey(event, part, setPart)}
            >
              {PARTS.map((id) => (
                <PartRadio
                  key={id}
                  id={id}
                  checked={part === id}
                  onPick={setPart}
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
            <Choices part={part} look={look} onLook={(next) => change({ ...meeting, look: next })} />
          </section>
        </div>
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">{t("meet.mission")}</legend>
        {MISSIONS.map((item) => (
          <label
            key={item.id}
            className="flex min-h-11 cursor-pointer items-start gap-2 rounded-lg border border-border p-3 text-sm has-checked:border-primary has-checked:bg-primary/10"
          >
            <input
              type="radio"
              name={`${group}-mission`}
              checked={mission === item.id}
              onChange={() => setMission(item.id)}
              className="mt-1"
            />
            <span className="flex flex-col">
              <span className="font-medium">
                {t(item.title)}
                {item.recommended ? ` (${t("meet.missionRecommended")})` : ""}
              </span>
              <span className="text-muted-foreground">{t(item.job)}</span>
            </span>
          </label>
        ))}
        {chosen && <p className="text-sm italic">{t(chosen.motto)}</p>}
        <p className="text-xs text-muted-foreground">{t("meet.missionHelp")}</p>
      </fieldset>
      <Button type="submit" disabled={busy} className="h-11 self-start">
        {t("oneChat.start")}
      </Button>
      {failed && (
        <p role="alert" className="text-sm">
          {t("oneChat.createError")}
        </p>
      )}
      <p className="text-sm text-muted-foreground">{t("oneChat.later")}</p>
    </form>
  );
}
