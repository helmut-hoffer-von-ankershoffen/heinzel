// What a chat shows wherever it is (Home, the right sidebar): the messages, the answer as it streams with the tools
// it calls, every proposed action as a card that runs only on Confirm, the costs, and the composer.
import { Check, Info, Plus, Send, X } from "lucide-react";
import { type FormEvent, type RefObject, useEffect, useId, useState } from "react";
import type { ModelChoice } from "@/api/chat";
import type { SchemaChatAction, SchemaChatMessage } from "@/api/client";
import { useConfig } from "@/api/queries";
import { CopyCommand } from "@/components/common";
import { Prose } from "@/components/Prose";
import { Button } from "@/components/ui/button";
import { format, t } from "@/i18n";
import { cn } from "@/lib/utils";
import { typed } from "@/sound/store";
import { useUpdateDraft } from "@/updates/draft";
import { MessageInfo } from "./MessageInfo";
import { ModelPicker } from "./ModelPicker";
import type { Chat } from "./useChat";

export const MESSAGE_LIMIT = 4000;

/** Tool calls as small chips, folded away until asked for. */
function Tools({ names }: { names: string[] }) {
  if (!names.length) return null;
  return (
    <details className="mt-1 text-xs text-muted-foreground">
      <summary className="cursor-pointer">{t("chat.toolsUsed", { count: names.length })}</summary>
      <ul className="mt-1 flex flex-wrap gap-1">
        {names.map((name, index) => (
          // The same tool may be called twice; its position tells the calls apart.
          // biome-ignore lint/suspicious/noArrayIndexKey: see above
          <li key={index} className="rounded-full border border-border px-2 py-0.5 font-mono">
            {name.replace(/^mcp__hz__/, "")}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function ActionCard({
  action,
  busy,
  onDecide,
  onAuthorizeMcp,
  proposalTitle,
}: {
  action: SchemaChatAction;
  busy: boolean;
  onDecide: (confirm: boolean) => void;
  onAuthorizeMcp?: () => void;
  proposalTitle?: string;
}) {
  const titleId = useId();
  return (
    <li className="rounded-lg border border-warn bg-muted p-3 text-sm">
      <section aria-labelledby={titleId}>
        <h3 id={titleId} className="font-semibold">
          {proposalTitle ?? t("chat.proposal")}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">{t("chat.proposalHelp")}</p>
        <code className="mt-2 block break-all rounded bg-background p-2 text-xs">{action.command}</code>
        {Object.keys(action.arguments ?? {}).length > 0 && (
          <>
            <p className="mt-2 text-xs text-muted-foreground">{t("chat.arguments")}</p>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-background p-2 text-xs">
              {JSON.stringify(action.arguments, null, 2)}
            </pre>
          </>
        )}
        <div className="mt-2 flex gap-2">
          {onAuthorizeMcp && (
            <Button size="sm" variant="outline" disabled={busy} onClick={onAuthorizeMcp}>
              {action.mcp_authorized ? t("chat.refreshMcpAuthorization") : t("chat.authorizeMcp")}
            </Button>
          )}
          {action.mcp_authorized && <span role="status">{t("chat.authorizedMcp")}</span>}
          <Button size="sm" disabled={busy} onClick={() => onDecide(true)}>
            <Check aria-hidden />
            {t("chat.confirm")}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => onDecide(false)}>
            <X aria-hidden />
            {t("chat.decline")}
          </Button>
        </div>
      </section>
    </li>
  );
}

function MessageItem({ message, first }: { message: SchemaChatMessage; first: boolean }) {
  if (message.role === "action") {
    return <li className="whitespace-pre-wrap text-xs text-muted-foreground">{message.text}</li>;
  }
  const user = message.role === "user";
  return (
    <li className={cn("text-sm", user ? "ml-8 rounded-lg bg-accent p-3" : "mr-4 py-1")}>
      <span className="sr-only">{user ? t("chat.you") : t("chat.agent")}: </span>
      {!user && message.status !== "completed" ? (
        <>
          <p className="text-bad">{t("chat.stopped", { detail: message.detail || message.status })}</p>
          {message.fix && (
            <div className="mt-1">
              <CopyCommand command={message.fix} />
            </div>
          )}
        </>
      ) : (
        <Prose>{message.text}</Prose>
      )}
      {!user && (
        <>
          <div className="mt-0.5 flex items-center gap-1">
            <MessageInfo message={message} first={first} />
          </div>
          <Tools names={Object.entries(message.tools ?? {}).flatMap(([name, count]) => Array(count).fill(name))} />
        </>
      )}
    </li>
  );
}

/** The caps before anything is sent, and what this chat has cost. */
export function Costs({ chat, className }: { chat: Chat; className?: string }) {
  const config = useConfig();
  const value = (section: string, key: string) => {
    const found = config.data?.settings.find((s) => s.section === section && s.key === key)?.value;
    return typeof found === "number" ? found : null;
  };
  const message = chat.session?.costs.message_max_usd ?? value("system.runner", "ask_max_usd");
  const session = chat.session?.costs.session_max_usd ?? value("system.runner.chat", "session_max_usd");
  const values = {
    message: message == null ? "…" : format.usd(message),
    session: session == null ? "…" : format.usd(session),
    spent: format.usd(chat.session?.costs.spent_usd ?? 0),
  };
  const full = t("chat.cost", values);
  // One short line; the whole explanation is the info icon's tooltip, and read out to screen readers.
  return (
    <p className={cn("flex items-center gap-1 text-xs text-muted-foreground", className)}>
      <span className="truncate">{t("chat.costShort", values)}</span>
      <span title={full} className="inline-flex shrink-0 cursor-help text-icon">
        <Info aria-hidden className="size-3.5" />
        <span className="sr-only">{full}</span>
      </span>
    </p>
  );
}

export function Conversation({ chat, empty }: { chat: Chat; empty?: string }) {
  const messages = chat.session?.messages ?? [];
  const firstAnswer = messages.findIndex((message) => message.role === "assistant");
  const pending = (chat.session?.actions ?? []).filter((action) => action.status === "pending");
  return (
    <ol className="flex flex-col gap-3" aria-live="polite">
      {!messages.length && !chat.live && empty && <li className="text-sm text-muted-foreground">{empty}</li>}
      {messages.map((item, index) => (
        // Messages are append-only, so their position is their identity.
        // biome-ignore lint/suspicious/noArrayIndexKey: see above
        <MessageItem key={index} message={item} first={index === firstAnswer} />
      ))}
      {pending.map((action) => (
        <ActionCard
          key={action.id}
          action={action}
          busy={chat.live != null || chat.deciding}
          onDecide={(confirm) => chat.decide(action.id, confirm)}
        />
      ))}
      {chat.live && (
        <>
          <li className="ml-8 rounded-lg bg-accent p-3 text-sm">
            <span className="sr-only">{t("chat.you")}: </span>
            <p className="whitespace-pre-wrap">{chat.live.message}</p>
          </li>
          <li role="status" className="mr-4 py-1 text-sm">
            {chat.live.text ? <Prose>{chat.live.text}</Prose> : t("chat.thinking")}
            {chat.live.tools.length > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("chat.tools", { tools: chat.live.tools.map((name) => name.replace(/^mcp__hz__/, "")).join(", ") })}
              </p>
            )}
          </li>
        </>
      )}
      {chat.error && (
        <li role="alert" className="text-sm text-bad">
          {chat.error}
        </li>
      )}
    </ol>
  );
}

export function Composer({
  chat,
  context,
  inputRef,
  className,
  onCreatePersonal,
  initialPersonalMessage,
}: {
  chat: Chat;
  context: string;
  inputRef?: RefObject<HTMLTextAreaElement | null>;
  className?: string;
  onCreatePersonal?: (message: string) => Promise<boolean>;
  initialPersonalMessage?: string;
}) {
  const [creating, setCreating] = useState(false);
  const sessionId = chat.session?.id;
  const { message, setMessage, picked, setPicked } = useUpdateDraft(
    context,
    sessionId ?? null,
    chat.live != null || chat.deciding || chat.loading,
  );
  useEffect(() => {
    if (initialPersonalMessage) setMessage((current) => current || initialPersonalMessage);
  }, [initialPersonalMessage, setMessage]);
  const choice: ModelChoice = picked ?? { model: chat.session?.model ?? null, effort: chat.session?.effort ?? null };
  const inputId = useId();
  const createPersonal = async () => {
    const text = message;
    if (!text.trim() || !onCreatePersonal || creating || chat.live || chat.loading) return;
    setCreating(true);
    try {
      if (await onCreatePersonal(text)) setMessage((current) => (current === text ? "" : current));
    } finally {
      setCreating(false);
    }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = message.trim();
    if (!text || chat.live || chat.loading || creating || initialPersonalMessage) return;
    setMessage("");
    if (!(await chat.send(text, context, picked ?? {})))
      setMessage((current) => current || text); // nothing lost
    else setPicked(null);
  };
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <form onSubmit={submit} className="flex items-end gap-2">
        <label htmlFor={inputId} className="sr-only">
          {t("chat.label")}
        </label>
        <textarea
          id={inputId}
          ref={inputRef}
          rows={2}
          value={message}
          maxLength={MESSAGE_LIMIT}
          disabled={creating}
          readOnly={Boolean(initialPersonalMessage)}
          onChange={(event) => {
            setMessage(event.target.value);
            typed(); // a soundscape plays softer while you type (ADR 0058)
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) void submit(event);
          }}
          placeholder={t("chat.placeholder")}
          className="min-h-9 min-w-0 flex-1 resize-none rounded-lg border border-border bg-input px-2 py-1 text-sm"
        />
        {onCreatePersonal && (
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label="Create Heinzel and take this message"
            title="Create Heinzel and take this message"
            disabled={!message.trim() || creating || chat.live != null || chat.loading}
            onClick={() => void createPersonal()}
          >
            <Plus aria-hidden />
          </Button>
        )}
        <Button
          type="submit"
          size="icon"
          disabled={!message.trim() || chat.live != null || chat.loading || creating || Boolean(initialPersonalMessage)}
          aria-label={t("chat.send")}
        >
          <Send aria-hidden />
        </Button>
      </form>
      {creating && (
        <span role="status" className="sr-only">
          Creating your Heinzel…
        </span>
      )}
      {initialPersonalMessage && !creating && (
        <p role="status" className="text-xs text-muted-foreground">
          An earlier creation is unresolved. Retry it with the preserved message.
        </p>
      )}
      <ModelPicker value={choice} onChange={setPicked} disabled={chat.live != null || creating} />
    </div>
  );
}
