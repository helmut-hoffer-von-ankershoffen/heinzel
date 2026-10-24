// The sound's controls (ADR 0058): the speaker button beside the light/dark toggle (a click plays or pauses; its
// chevron, a right-click or a long press opens the popover), and the same choices in Settings → General.
import { Check, ChevronUp, Volume, Volume2, VolumeX } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { SchemaSoundList, SchemaSoundscapeView } from "@/api/client";
import { t } from "@/i18n";
import { Switch } from "@/layout/SettingControls";
import { cn } from "@/lib/utils";
import { effective, useSetSound, useSoundSetting, useSoundscapes } from "./queries";
import { toggle, useSound } from "./store";

const LONG_PRESS_MS = 500;
const VOLUME_DEBOUNCE_MS = 400;

/** What a soundscape is, under its title: where a stream plays from, a layer's attribution. */
function detail(soundscape: SchemaSoundscapeView): string | null {
  if (soundscape.stream)
    return t("sound.playsFrom", {
      host: soundscape.stream.provider === "youtube" ? "YouTube" : soundscape.stream.provider,
    });
  const credits = [
    ...new Set(
      [...(soundscape.layers ?? []), ...(soundscape.earcons ?? [])].map((asset) => asset.attribution).filter(Boolean),
    ),
  ];
  return credits.length ? t("sound.credits", { credits: credits.join(", ") }) : null;
}

/** Off, Claude FM and the plugins' soundscapes, the configured one checked, as a group of radios. */
export function SoundChoices({ list, onPicked }: { list: SchemaSoundList; onPicked?: () => void }) {
  const choose = useSetSound();
  return (
    <div role="radiogroup" aria-label={t("sound.title")} className="flex flex-col gap-0.5">
      {list.soundscapes.map((soundscape) => {
        const checked = soundscape.name === list.selected;
        const about = detail(soundscape);
        return (
          // biome-ignore lint/a11y/useSemanticElements: a list of choices, announced as the radios of its group
          <button
            key={soundscape.name}
            type="button"
            role="radio"
            aria-checked={checked}
            disabled={choose.isPending}
            onClick={() => {
              if (!checked) choose.mutate(soundscape.name);
              onPicked?.();
            }}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-ui hover:bg-accent disabled:opacity-50"
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate">{soundscape.title}</span>
              {about && <span className="block truncate text-xs text-muted-foreground">{about}</span>}
            </span>
            <Check aria-hidden className={cn("size-4 shrink-0", !checked && "invisible")} />
          </button>
        );
      })}
    </div>
  );
}

/** The volume, saved to `[system.sound] volume` once the slider rests; the sound follows it at once. */
function VolumeSlider({ list }: { list: SchemaSoundList }) {
  const setting = useSoundSetting();
  const [value, setValue] = useState(list.volume);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => setValue(list.volume), [list.volume]);
  useEffect(() => () => clearTimeout(timer.current), []);
  const id = useId();
  return (
    <div className="flex items-center gap-3 px-2.5 py-1.5">
      <label htmlFor={id} className="text-ui-sm text-muted-foreground">
        {t("sound.volume")}
      </label>
      <input
        id={id}
        type="range"
        min={0}
        max={100}
        step={5}
        value={value}
        onChange={(event) => {
          const next = Number(event.target.value);
          setValue(next);
          clearTimeout(timer.current);
          timer.current = setTimeout(() => setting.save("volume", next), VOLUME_DEBOUNCE_MS);
        }}
        className="flex-1 accent-(--color-primary)"
      />
    </div>
  );
}

/** One on/off setting of `[system.sound]` with its label. */
function Toggle({
  list,
  field,
  label,
}: {
  list: SchemaSoundList;
  field: "follow_theme" | "mute_in_meetings" | "duck_while_typing";
  label: string;
}) {
  const setting = useSoundSetting();
  const id = useId();
  const on = list[field];
  return (
    <div className="flex items-center gap-3 px-2.5 py-1.5">
      <span id={id} className="flex-1 text-ui">
        {label}
      </span>
      <Switch labelledBy={id} on={on} disabled={setting.isPending} onToggle={() => setting.save(field, !on)} />
    </div>
  );
}

/** The speaker button and its popover, beside the light/dark toggle; `compact` in the collapsed rail. */
export function SoundButton({ compact = false }: { compact?: boolean }) {
  const list = useSoundscapes();
  const sound = useSound();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const press = useRef<ReturnType<typeof setTimeout>>(undefined);
  const pressed = useRef(false);
  const current = effective(list.data);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      opener.current?.focus();
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const label = !current
    ? t("sound.choose")
    : sound.playing
      ? t("sound.pause", { title: current.title })
      : t("sound.play", { title: current.title });
  const size = compact ? "size-7" : "size-9";
  return (
    <div ref={ref} className={cn("relative flex items-center", compact && "flex-col")}>
      {open && list.data && (
        <div
          role="dialog"
          aria-label={t("sound.title")}
          className={cn(
            "absolute z-30 w-72 rounded-xl border border-border bg-popover p-1.5 shadow-xl",
            compact ? "bottom-0 left-full ml-2" : "right-0 bottom-full mb-1.5",
          )}
        >
          <p className="px-2.5 pt-1 pb-1.5 text-ui-sm text-muted-foreground">{t("sound.title")}</p>
          <SoundChoices list={list.data} />
          <Notes list={list.data} />
          <hr className="mx-2 my-1 border-border" />
          <VolumeSlider list={list.data} />
          <Toggle list={list.data} field="follow_theme" label={t("sound.followTheme")} />
          <Toggle list={list.data} field="mute_in_meetings" label={t("sound.muteInMeetings")} />
          {sound.problem && <p className="px-2.5 py-1 text-ui-sm text-bad">{sound.problem}</p>}
        </div>
      )}
      <button
        type="button"
        onClick={() => {
          if (pressed.current) {
            pressed.current = false; // the long press opened the options: this click doesn't also play
            return;
          }
          if (!current) setOpen((value) => !value);
          else toggle();
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          setOpen(true);
        }}
        onPointerDown={() => {
          clearTimeout(press.current);
          pressed.current = false;
          press.current = setTimeout(() => {
            pressed.current = true;
            setOpen(true);
          }, LONG_PRESS_MS);
        }}
        onPointerUp={() => clearTimeout(press.current)}
        onPointerLeave={() => clearTimeout(press.current)}
        aria-pressed={current ? sound.playing : undefined}
        aria-label={label}
        title={label}
        className={cn(
          "flex items-center justify-center rounded-lg text-icon transition-colors hover:bg-accent/70 [&_svg]:size-(--icon) [&_svg]:stroke-[1.75]",
          size,
        )}
      >
        {!current ? <Volume aria-hidden /> : sound.playing ? <Volume2 aria-hidden /> : <VolumeX aria-hidden />}
      </button>
      <button
        ref={opener}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t("sound.options")}
        title={t("sound.options")}
        className={cn(
          "flex items-center justify-center rounded-md text-icon hover:bg-accent/70",
          compact ? "h-3 w-7" : "-ml-1 h-9 w-4",
        )}
      >
        <ChevronUp aria-hidden className="size-3" />
      </button>
    </div>
  );
}

/** Why what plays isn't what is picked: the list's note, or the theme's own soundscape. */
function Notes({ list }: { list: SchemaSoundList }) {
  const current = effective(list);
  const following = current && current.name !== list.selected;
  return (
    <>
      {list.note && !following && <p className="mt-1 px-2.5 text-ui-sm text-warn">{list.note}</p>}
      {following && (
        <p className="mt-1 px-2.5 text-ui-sm text-muted-foreground">{t("sound.following", { title: current.title })}</p>
      )}
    </>
  );
}

/** Settings → General: the full Sound picker with every option, from the same data as the popover. */
export function SoundSettings() {
  const list = useSoundscapes();
  if (!list.data) return null;
  return (
    <section aria-labelledby="sound-settings">
      <h3 id="sound-settings" className="mb-1 text-ui font-medium">
        {t("sound.title")}
      </h3>
      <p className="mb-2 text-sm text-muted-foreground">{t("sound.help")}</p>
      <SoundChoices list={list.data} />
      <Notes list={list.data} />
      <div className="mt-2 border-t border-border/70 pt-2">
        <VolumeSlider list={list.data} />
        <Toggle list={list.data} field="follow_theme" label={t("sound.followTheme")} />
        <Toggle list={list.data} field="mute_in_meetings" label={t("sound.muteInMeetings")} />
        <Toggle list={list.data} field="duck_while_typing" label={t("sound.duckWhileTyping")} />
      </div>
    </section>
  );
}
