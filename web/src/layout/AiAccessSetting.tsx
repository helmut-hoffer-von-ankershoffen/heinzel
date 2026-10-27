// `hz ai access use` as one visible choice next to the theme (ADR 0057): one row per installed option with its title,
// summary and status, an "In use" marker on the chosen one, and a test button that runs `hz ai access test` only when
// pressed, since a live check may cost a little.
import { useId } from "react";
import type { SchemaAccessOption, SchemaAccessTestReport } from "@/api/client";
import { useAiAccess, useSetAiAccess, useTestAiAccess } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

const tone: Record<SchemaAccessOption["status"], string> = {
  "signed in": "text-ok",
  tested: "text-ok",
  "not signed in": "text-warn",
  "not tested": "text-muted-foreground",
  "test failed": "text-bad",
  "not installed": "text-muted-foreground",
  error: "text-bad",
};

export function AiAccessSetting() {
  const access = useAiAccess();
  const change = useSetAiAccess();
  const check = useTestAiAccess();
  const labelId = useId();
  const options = access.data?.options ?? [];
  const note = change.error?.message ?? change.data?.note ?? access.data?.note;
  const tested = check.data;
  return (
    <div className="border-b border-border/70 py-3">
      <p id={labelId} className="text-ui">
        {t("aiAccess.title")}
      </p>
      <p className="mt-0.5 text-sm text-muted-foreground">{t("aiAccess.intro")}</p>
      {note && <p className="mt-1 text-sm text-warn">{note}</p>}
      <div role="radiogroup" aria-labelledby={labelId} className="mt-3 flex flex-col gap-2">
        {options.map((option) => (
          <Option
            key={option.name}
            option={option}
            busy={change.isPending}
            testing={check.isPending && check.variables === option.name}
            result={tested?.name === option.name ? tested : undefined}
            onSelect={() => !option.in_use && change.mutate(option.name)}
            onTest={() => check.mutate(option.name)}
          />
        ))}
      </div>
    </div>
  );
}

type OptionProps = {
  option: SchemaAccessOption;
  busy: boolean;
  testing: boolean;
  result?: SchemaAccessTestReport;
  onSelect: () => void;
  onTest: () => void;
};

function Option({ option, busy, testing, result, onSelect, onTest }: OptionProps) {
  const detailId = useId();
  const usable = option.status !== "not installed" && option.status !== "error";
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-2 rounded-lg border border-border px-3 py-2 sm:flex-row sm:gap-3",
        option.in_use && "border-primary bg-accent/40",
      )}
    >
      {/* biome-ignore lint/a11y/useSemanticElements: a card of a radio group, with a status and a fix an input can't hold */}
      <button
        type="button"
        role="radio"
        aria-checked={option.in_use}
        aria-describedby={detailId}
        disabled={busy || (!usable && !option.in_use)}
        onClick={onSelect}
        className="w-full min-w-0 flex-1 text-left [overflow-wrap:anywhere] disabled:opacity-60"
      >
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-ui font-medium">{option.title}</span>
          {option.in_use && <span className="text-ui-sm text-primary">{t("aiAccess.inUse")}</span>}
          <span className={cn("text-ui-sm", tone[option.status])}>{option.status}</span>
        </span>
        <span id={detailId} className="block text-sm text-muted-foreground">
          {option.summary}
        </span>
        {option.fix && <span className="mt-0.5 block font-mono text-ui-sm text-muted-foreground">{option.fix}</span>}
        {result && (
          <span className={cn("mt-0.5 block text-sm", result.ok ? "text-ok" : "text-bad")}>
            {result.ok
              ? t("aiAccess.answered", { detail: result.detail })
              : t("aiAccess.failed", { detail: result.detail })}
          </span>
        )}
      </button>
      <Button
        variant="outline"
        size="sm"
        disabled={testing || !usable}
        onClick={onTest}
        aria-label={t("aiAccess.testOne", { title: option.title })}
      >
        {testing ? t("aiAccess.testing") : t("aiAccess.test")}
      </Button>
    </div>
  );
}
