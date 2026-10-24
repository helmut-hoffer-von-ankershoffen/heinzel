// The right sidebar: a chat with the configured agent runner through `hz system runner ask` (ADR 0039, 0029).
// Stateless: each turn sends the recent conversation again, within the operation's prompt limit.
import { MessageSquare, PanelRightClose, Send } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import type { SchemaAgentResult } from "@/api/client";
import { useAsk, useConfig } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { format, t } from "@/i18n";
import { cn } from "@/lib/utils";

export const PROMPT_LIMIT = 6000;
type Turn =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "agent"; text: string; result: SchemaAgentResult };

/** The conversation as one prompt: as many recent turns as fit, oldest first, ending with the new message. */
export function transcript(turns: Pick<Turn, "role" | "text">[], message: string, limit = PROMPT_LIMIT): string {
  const last = `User: ${message}`;
  const lines: string[] = [];
  let size = last.length;
  for (const turn of [...turns].reverse()) {
    const line = `${turn.role === "user" ? "User" : "Assistant"}: ${turn.text}`;
    if (size + line.length + 2 > limit) break;
    lines.unshift(line);
    size += line.length + 2;
  }
  return [...lines, last].join("\n\n").slice(-limit);
}

function cost(result: SchemaAgentResult): string {
  const usd = result.usage?.cost_usd;
  return t("chat.answerCost", { runner: result.runner, cost: usd == null ? t("chat.costUnknown") : format.usd(usd) });
}

export function ChatPanel({ onCollapse }: { onCollapse: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [message, setMessage] = useState("");
  const ask = useAsk();
  const config = useConfig();
  const inputId = useId();
  const cap = config.data?.settings.find((s) => s.section === "system.runner" && s.key === "ask_max_usd")?.value;
  const send = (event: FormEvent) => {
    event.preventDefault();
    const text = message.trim();
    if (!text || ask.isPending) return;
    const prompt = transcript(turns, text);
    setTurns((current) => [...current, { id: current.length, role: "user", text }]);
    setMessage("");
    ask.mutate(
      { prompt },
      {
        onSuccess: (result) =>
          setTurns((current) => [
            ...current,
            { id: current.length, role: "agent", text: result.status === "completed" ? result.draft : "", result },
          ]),
      },
    );
  };
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border p-3">
        <MessageSquare aria-hidden className="size-4" />
        <h2 className="text-sm font-semibold">{t("chat.title")}</h2>
        {turns.length > 0 && (
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setTurns([])}>
            {t("chat.clear")}
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className={cn(turns.length === 0 && "ml-auto")}
          onClick={onCollapse}
          aria-label={t("chat.collapse")}
          title={t("chat.collapse")}
        >
          <PanelRightClose aria-hidden />
        </Button>
      </div>
      <p className="border-b border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
        {t("chat.cost", { cap: typeof cap === "number" ? format.usd(cap) : "…" })}
      </p>
      <ol className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3" aria-live="polite">
        {turns.map((turn) => (
          <li
            key={turn.id}
            className={cn(
              "rounded-lg p-2 text-sm",
              turn.role === "user" ? "ml-6 bg-accent" : "mr-6 border border-border",
            )}
          >
            <span className="sr-only">{turn.role === "user" ? t("chat.you") : t("chat.agent")}: </span>
            {turn.role === "agent" && turn.result.status !== "completed" ? (
              <p className="text-bad">{t("chat.stopped", { detail: turn.result.detail || turn.result.status })}</p>
            ) : (
              <p className="whitespace-pre-wrap">{turn.text}</p>
            )}
            {turn.role === "agent" && <p className="mt-1 text-xs text-muted-foreground">{cost(turn.result)}</p>}
          </li>
        ))}
        {ask.isPending && (
          <li role="status" className="text-sm text-muted-foreground">
            {t("chat.thinking")}
          </li>
        )}
        {ask.error && (
          <li role="alert" className="text-sm text-bad">
            {ask.error.message}
          </li>
        )}
      </ol>
      <form onSubmit={send} className="flex items-end gap-2 border-t border-border p-3">
        <label htmlFor={inputId} className="sr-only">
          {t("chat.label")}
        </label>
        <textarea
          id={inputId}
          rows={2}
          value={message}
          maxLength={PROMPT_LIMIT - 100}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) send(event);
          }}
          placeholder={t("chat.placeholder")}
          className="min-h-9 flex-1 resize-none rounded-md border border-border bg-background px-2 py-1 text-sm"
        />
        <Button type="submit" size="icon" disabled={!message.trim() || ask.isPending} aria-label={t("chat.send")}>
          <Send aria-hidden />
        </Button>
      </form>
    </div>
  );
}
