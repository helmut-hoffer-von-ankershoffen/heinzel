// The composer's model and effort picker, as in Claude: the models of the AI access in use with a check mark on the
// chosen one, an Effort section (medium recommended, max with its usage warning) and "More models" for any name the
// access takes. A popover of buttons, not an ARIA menu: Tab moves through it, Escape closes it and focus returns to
// the trigger. The choice is the conversation's: it goes with the next message and the session keeps it (ADR 0057).
import { Check, ChevronDown } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import type { ModelChoice } from "@/api/chat";
import { useAiModels } from "@/api/queries";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

const NAME = /^[A-Za-z0-9][A-Za-z0-9._:/@[\]-]*$/;

type Props = { value: ModelChoice; onChange: (choice: ModelChoice) => void; disabled?: boolean };

function Option({
  label,
  chosen,
  note,
  onPick,
}: {
  label: string;
  chosen: boolean;
  note?: string;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={chosen}
      onClick={onPick}
      className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs hover:bg-accent focus-visible:bg-accent"
    >
      <Check aria-hidden className={cn("size-3 shrink-0", chosen ? "" : "invisible")} />
      <span className="flex-1">{label}</span>
      {note && <span className="text-warn">{note}</span>}
    </button>
  );
}

export function ModelPicker({ value, onChange, disabled }: Props) {
  const models = useAiModels();
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const [typed, setTyped] = useState("");
  const panelId = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector("button")?.focus();
    const close = (event: MouseEvent) => !root.current?.contains(event.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const list = models.data;
  if (!list || (!list.models.length && !list.efforts.length)) return null;
  const effort = list.efforts.find((item) => item.name === value.effort);
  const label = [
    list.models.find((item) => item.name === value.model)?.title ?? (value.model || t("chat.modelDefault")),
    effort?.title,
  ]
    .filter(Boolean)
    .join(" · ");
  const close = () => {
    setOpen(false);
    setMore(false);
    trigger.current?.focus();
  };
  const pick = (choice: ModelChoice) => {
    onChange({ ...value, ...choice });
    close();
  };
  const onEscape = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  };
  const submitMore = (event: FormEvent) => {
    event.preventDefault();
    if (NAME.test(typed.trim())) pick({ model: typed.trim() });
  };
  return (
    <div ref={root} className="relative">
      <button
        ref={trigger}
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={t("chat.modelPicker", { choice: label })}
        onClick={() => (open ? close() : setOpen(true))}
        className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-accent/70 disabled:opacity-60 [&_svg]:size-3"
      >
        {label}
        <ChevronDown aria-hidden />
      </button>
      {open && (
        // biome-ignore lint/a11y/noStaticElementInteractions: Escape closes the popover from anywhere inside it
        <div
          ref={panel}
          id={panelId}
          onKeyDown={onEscape}
          className="absolute bottom-7 left-0 z-(--layer-popover) w-64 rounded-lg border border-border bg-popover p-1 shadow-md"
        >
          <fieldset aria-label={t("chat.modelMenu", { access: list.title })}>
            <Option label={t("chat.modelDefault")} chosen={!value.model} onPick={() => pick({ model: "" })} />
            {list.models.map((model) => (
              <Option
                key={model.name}
                label={model.title}
                chosen={value.model === model.name}
                onPick={() => pick({ model: model.name })}
              />
            ))}
          </fieldset>
          {list.efforts.length > 0 && (
            <fieldset aria-label={t("chat.effort")} className="mt-1 border-t border-border pt-1">
              <p className="px-2 py-0.5 text-[0.7rem] text-muted-foreground">{t("chat.effort")}</p>
              <Option label={t("chat.modelDefault")} chosen={!value.effort} onPick={() => pick({ effort: "" })} />
              {list.efforts.map((choice) => (
                <Option
                  key={choice.name}
                  label={choice.recommended ? `${choice.title} (${t("chat.recommended")})` : choice.title}
                  chosen={value.effort === choice.name}
                  note={choice.warning ?? undefined}
                  onPick={() => pick({ effort: choice.name })}
                />
              ))}
            </fieldset>
          )}
          <div className="mt-1 border-t border-border pt-1">
            <button
              type="button"
              aria-expanded={more}
              onClick={() => setMore((shown) => !shown)}
              className="flex w-full items-center rounded-md px-2 py-1 text-left text-xs hover:bg-accent focus-visible:bg-accent"
            >
              {t("chat.moreModels")}
            </button>
            {more && (
              <form onSubmit={submitMore} className="flex gap-1 px-2 pb-1">
                <label className="sr-only" htmlFor={`${panelId}-name`}>
                  {t("chat.modelName")}
                </label>
                <input
                  id={`${panelId}-name`}
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                  placeholder={t("chat.modelNamePlaceholder")}
                  className="min-w-0 flex-1 rounded-md border border-border bg-input px-1.5 py-0.5 text-xs"
                />
                <button
                  type="submit"
                  disabled={!NAME.test(typed.trim())}
                  className="rounded-md px-1.5 text-xs hover:bg-accent disabled:opacity-50"
                >
                  {t("chat.use")}
                </button>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
