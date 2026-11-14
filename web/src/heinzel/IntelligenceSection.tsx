// How hard one Heinzel thinks (ADR 0158): the AI provider, then Auto / Low / Medium / High, saved at once with Undo.
// The provider defines what each level runs on, so the section says in words what was resolved and never names a model
// outside the "Advanced: exact model" disclosure, which is the only place an exact model is chosen.
import { BatteryFull, BatteryLow, BatteryMedium, ChevronDown, Sparkles, Undo2 } from "lucide-react";
import { useId, useState } from "react";
import type { SchemaAccessOption, SchemaLevelView } from "@/api/client";
import { type LevelChange, useAiAccess, useHeinzelLevel, useSetHeinzelLevel } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { type Segment, SegmentedControl } from "@/components/ui/segmented";
import { type MessageKey, t } from "@/i18n";

type Level = SchemaLevelView["level"];
type Failed = "level" | "provider" | "model" | "undo";
type Undoable = { readonly message: string; readonly restore: LevelChange };

const LEVELS: readonly Segment<Level>[] = [
  { value: "auto", label: t("intelligence.level.auto"), icon: Sparkles },
  { value: "low", label: t("intelligence.level.low"), icon: BatteryLow },
  { value: "medium", label: t("intelligence.level.medium"), icon: BatteryMedium },
  { value: "high", label: t("intelligence.level.high"), icon: BatteryFull },
];
const LEVEL_WORD: Record<Level, MessageKey> = {
  auto: "intelligence.level.auto",
  low: "intelligence.level.low",
  medium: "intelligence.level.medium",
  high: "intelligence.level.high",
};
const MEANS: Record<Level, MessageKey> = {
  auto: "intelligence.means.auto",
  low: "intelligence.means.low",
  medium: "intelligence.means.medium",
  high: "intelligence.means.high",
};
const ERROR: Record<Failed, MessageKey> = {
  level: "intelligence.error.level",
  provider: "intelligence.error.provider",
  model: "intelligence.error.model",
  undo: "intelligence.error.undo",
};
const ROW = "flex min-h-11 items-center gap-2 rounded-md px-2 text-ui";

/** The resolved choice in words, e.g. "Your provider has no Low model, so Medium is used." Never a model name. */
export function sourceSentence(view: SchemaLevelView): string | null {
  const ran = view.runs_as?.level;
  if (!ran) return null;
  const word = (level: Level | "low" | "medium" | "high") => t(LEVEL_WORD[level]);
  // Auto asks for Medium by one fixed rule, so "nearest" is measured from Medium.
  const asked = ran.asked === "auto" ? "medium" : ran.asked;
  const base = {
    exact: t("intelligence.source.exact"),
    declared: t("intelligence.source.declared", { ran: word(ran.ran) }),
    nearest: t("intelligence.source.nearest", { asked: word(asked), ran: word(ran.ran) }),
    "provider-default": t("intelligence.source.providerDefault"),
  }[ran.source];
  return ran.asked === "auto" && ran.source !== "exact" && ran.source !== "provider-default"
    ? `${t("intelligence.source.auto", { ran: word(asked) })} ${base}`
    : base;
}

export function IntelligenceSection({ sessionId }: { readonly sessionId: string }) {
  const level = useHeinzelLevel(sessionId);
  const access = useAiAccess();
  const save = useSetHeinzelLevel(sessionId);
  const [failed, setFailed] = useState<{ kind: Failed; reason: string } | null>(null);
  const [undo, setUndo] = useState<Undoable | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const advancedId = useId();
  const providerLegend = useId();
  const view = level.data;
  if (level.isPending) return <p className="text-ui text-muted-foreground">{t("intelligence.loading")}</p>;
  if (!view)
    return (
      <p role="alert" className="text-ui text-bad">
        {t("intelligence.loadFailed", { reason: level.error?.message ?? "" })}
      </p>
    );

  const options = access.data?.options ?? [];
  const selected = access.data?.selected ?? null;
  const configured = options.find((option) => option.name === selected);
  const providerName = view.provider ?? selected;
  const offer = options.find((option) => option.name === providerName);
  // The configured AI access is the first row ("as set in Settings"), so it is not listed a second time unless pinned.
  const providers = options.filter(
    (option) =>
      option.name === view.provider || (option.name !== selected && (option.status !== "needs setup" || option.in_use)),
  );
  const pending = save.isPending;

  const change = (kind: Failed, change: LevelChange, restore: LevelChange | null, message: string) => {
    setFailed(null);
    setDone(null);
    save.mutate(
      { ...change, expectedRevision: view.profile_revision },
      {
        onSuccess: () => {
          setUndo(restore ? { message, restore } : null);
          setDone(restore ? message : t("intelligence.undone"));
        },
        onError: (error) => {
          setUndo(null);
          setFailed({ kind, reason: error.message });
        },
      },
    );
  };
  const pickLevel = (next: Level) => {
    if (next === view.level || pending) return;
    change(
      "level",
      { level: next },
      { level: view.level },
      t("intelligence.saved.level", { level: t(LEVEL_WORD[next]) }),
    );
  };
  const pickProvider = (next: string) => {
    if ((view.provider ?? "") === next || pending) return;
    // An exact model belongs to its provider, so it goes when the provider does.
    change(
      "provider",
      { provider: next, model: "" },
      { provider: view.provider ?? "", model: view.model ?? "" },
      t("intelligence.saved.provider"),
    );
  };
  const pickModel = (next: string) => {
    if ((view.model ?? "") === next || pending) return;
    change("model", { model: next }, { model: view.model ?? "" }, t("intelligence.saved.model"));
  };
  const undoLast = () => {
    if (!undo) return;
    const restore = undo.restore;
    change("undo", restore, null, t("intelligence.undone"));
  };

  const sentence = sourceSentence(view);
  return (
    <section aria-labelledby={`${providerLegend}-title`} className="space-y-4">
      <h3 id={`${providerLegend}-title`} className="text-ui font-medium">
        {t("intelligence.title")}
      </h3>
      <fieldset className="space-y-1" aria-busy={pending || undefined}>
        <legend id={providerLegend} className="text-xs text-muted-foreground">
          {t("intelligence.provider")}
        </legend>
        <ProviderRow
          name=""
          label={
            configured
              ? t("intelligence.providerConfigured", { title: configured.title })
              : t("intelligence.providerConfiguredNone")
          }
          checked={!view.provider}
          disabled={pending}
          onPick={pickProvider}
        />
        {providers.map((option) => (
          <ProviderRow
            key={option.name}
            name={option.name}
            label={option.title}
            checked={view.provider === option.name}
            disabled={pending}
            onPick={pickProvider}
          />
        ))}
      </fieldset>
      <div className="space-y-1">
        <SegmentedControl
          legend={t("intelligence.level")}
          segments={LEVELS}
          value={view.level}
          onSelect={pickLevel}
          selectOnArrow
          busy={pending && save.variables?.level ? save.variables.level : null}
        />
        <p className="text-ui text-muted-foreground">{t(MEANS[view.level])}</p>
        {sentence && <p className="text-ui">{sentence}</p>}
        {view.problem && <p className="text-ui text-warn">{t("intelligence.problem", { problem: view.problem })}</p>}
      </div>
      <div aria-live="polite" className="flex min-h-11 flex-wrap items-center gap-2">
        {done && !failed && <span className="text-ui">{done}</span>}
        {undo && !failed && (
          <Button type="button" variant="ghost" size="sm" className="min-h-11" disabled={pending} onClick={undoLast}>
            <Undo2 aria-hidden className="size-4" />
            {t("intelligence.undo")}
          </Button>
        )}
      </div>
      {failed && (
        <p role="alert" className="text-ui text-bad">
          {t(ERROR[failed.kind], { reason: failed.reason })}
        </p>
      )}
      <div className="space-y-2">
        <button
          type="button"
          aria-expanded={advanced}
          aria-controls={advancedId}
          onClick={() => setAdvanced(!advanced)}
          className="flex min-h-11 items-center gap-2 text-ui text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring"
        >
          <ChevronDown aria-hidden className={`size-4 ${advanced ? "" : "-rotate-90"}`} />
          {t("intelligence.advanced")}
        </button>
        {advanced && (
          <div id={advancedId}>
            <Offers offer={offer} model={view.model} disabled={pending} onPick={pickModel} />
          </div>
        )}
      </div>
    </section>
  );
}

function ProviderRow(props: {
  readonly name: string;
  readonly label: string;
  readonly checked: boolean;
  readonly disabled: boolean;
  readonly onPick: (name: string) => void;
}) {
  return (
    <label className={ROW}>
      <input
        type="radio"
        name="intelligence-provider"
        className="size-4"
        checked={props.checked}
        disabled={props.disabled}
        onChange={() => props.onPick(props.name)}
      />
      {props.label}
    </label>
  );
}

/** The provider's offers. One that didn't answer is shown unavailable and can't be picked. */
function Offers(props: {
  readonly offer: SchemaAccessOption | undefined;
  readonly model: string | null;
  readonly disabled: boolean;
  readonly onPick: (name: string) => void;
}) {
  const models = props.offer?.models ?? [];
  if (models.length === 0) return <p className="text-ui text-muted-foreground">{t("intelligence.modelsEmpty")}</p>;
  const silent = new Set(props.offer?.unavailable_models ?? []);
  return (
    <fieldset className="space-y-1">
      <legend className="text-xs text-muted-foreground">{t("intelligence.models")}</legend>
      <p className="text-xs text-muted-foreground">{t("intelligence.advancedNote")}</p>
      <label className={ROW}>
        <input
          type="radio"
          name="intelligence-model"
          className="size-4"
          checked={!props.model}
          disabled={props.disabled}
          onChange={() => props.onPick("")}
        />
        {t("intelligence.modelNone")}
      </label>
      {models.map((name) => {
        const gone = silent.has(name);
        return (
          <label key={name} className={ROW}>
            <input
              type="radio"
              name="intelligence-model"
              className="size-4"
              checked={props.model === name}
              disabled={props.disabled || (gone && props.model !== name)}
              onChange={() => {
                if (!gone) props.onPick(name);
              }}
            />
            {gone ? t("intelligence.modelUnavailable", { name }) : name}
          </label>
        );
      })}
    </fieldset>
  );
}
