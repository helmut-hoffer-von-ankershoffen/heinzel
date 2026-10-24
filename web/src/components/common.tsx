// Building blocks every screen uses: a page header, loading and error states, empty states, copyable commands.
import { Check, Copy, Play } from "lucide-react";
import { type ReactNode, useState } from "react";
import { ApiError } from "@/api/client";
import { t } from "@/i18n";
import { runInTerminal, runsAsIs } from "@/lib/terminal";
import { Button } from "./ui/button";
import { Badge } from "./ui/card";

export function Page({
  title,
  intro,
  actions,
  children,
}: {
  title: string;
  intro?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-8 pt-4 pb-16">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="sr-only">{title}</h1>
          {intro && <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">{intro}</p>}
        </div>
        {actions}
      </header>
      {children}
    </div>
  );
}

/** Loading and error states for one query; children render once there is data. */
export function Loaded<T>({
  query,
  children,
}: {
  query: { data?: T; error: Error | null; isPending: boolean };
  children: (data: T) => ReactNode;
}) {
  if (query.error instanceof ApiError && query.error.status === 401) {
    // `hz ui` restarted, so this tab's token is stale: the link it printed now has a new one.
    return (
      <div role="alert" className="flex flex-col items-start gap-2 rounded-md border border-bad/40 p-3 text-sm">
        <p>{t("token.refused")}</p>
        <CopyCommand command="hz ui" />
      </div>
    );
  }
  if (query.error) {
    return (
      <p role="alert" className="rounded-md border border-bad/40 p-3 text-sm text-bad">
        {t("state.error")} {query.error.message}
      </p>
    );
  }
  if (query.isPending || query.data === undefined) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("state.loading")}
      </p>
    );
  }
  return <>{children(query.data)}</>;
}

/** A command to copy, in a monospace box with a copy button. */
export function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(command);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  return (
    <span className="inline-flex max-w-full items-center gap-1 rounded-md border border-border bg-muted py-0.5 pr-0.5 pl-2">
      <code className="truncate font-mono text-xs">{command}</code>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 px-1.5"
        onClick={copy}
        aria-label={t("copy.aria", { command })}
        title={copied ? t("copy.done") : t("copy.label")}
      >
        {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
        <span className="sr-only" aria-live="polite">
          {copied ? t("copy.done") : ""}
        </span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 px-1.5"
        onClick={() => runInTerminal(command)}
        aria-label={t("run.aria", { command })}
        title={runsAsIs(command) ? t("run.label") : t("run.review")}
      >
        <Play aria-hidden />
      </Button>
    </span>
  );
}

export function Empty({ text, commands = [] }: { text: string; commands?: string[] }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed border-border p-6 text-sm">
      <p className="text-muted-foreground">{text}</p>
      {commands.map((command) => (
        <CopyCommand key={command} command={command} />
      ))}
    </div>
  );
}

const statusTone = { up: "ok", degraded: "warn", down: "bad" } as const;
const statusMark = { up: "✔", degraded: "▲", down: "✖" } as const;

/** A health status as a word with a mark, never colour alone. */
export function StatusBadge({ status }: { status: "up" | "degraded" | "down" }) {
  return (
    <Badge tone={statusTone[status]}>
      <span aria-hidden className="mr-1">
        {statusMark[status]}
      </span>
      {t(`status.${status}`)}
    </Badge>
  );
}
