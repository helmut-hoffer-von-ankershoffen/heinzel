// One quiet line under the person's own message when every chat is with a Heinzel (flag `one_chat`, canvas board 7):
// "Sending…", "✖ Not sent · Try again", "✖ Not answered · Try again" or "Queued · after the one above". The
// symbol is decoration beside the word. Only a failure that happens in front of the person is announced as an alert;
// one found on reopening is shown, not shouted. An uncertain turn never gets here: it keeps its own recovery card.
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { type MessageKey, t } from "@/i18n";

export type MessageStateKind = "sending" | "not-sent" | "not-answered" | "queued";

const WORDS: Record<MessageStateKind, MessageKey> = {
  sending: "durable.status.sending",
  "not-sent": "oneChat.notSent",
  "not-answered": "oneChat.notAnswered",
  queued: "oneChat.queued",
};

export function MessageState({
  kind,
  announce = false,
  onRetry,
  retryDisabled = false,
}: {
  kind: MessageStateKind;
  /** A failure that just happened in this view; a failure found on reopening is not announced. */
  announce?: boolean;
  onRetry?: () => void;
  retryDisabled?: boolean;
}) {
  const failed = kind === "not-sent" || kind === "not-answered";
  return (
    <div
      role={failed ? (announce ? "alert" : undefined) : kind === "queued" ? "status" : undefined}
      data-message-state={kind}
      className="flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground"
    >
      <span className={failed ? "font-medium text-bad" : undefined}>
        {failed && <span aria-hidden="true">✖ </span>}
        {t(WORDS[kind])}
      </span>
      {failed && onRetry && (
        <Button type="button" variant="outline" size="sm" data-pane-action disabled={retryDisabled} onClick={onRetry}>
          <RotateCcw aria-hidden />
          {t("oneChat.tryAgain")}
        </Button>
      )}
    </div>
  );
}
