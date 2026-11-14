// The app's chat (ADR 0157, 0045), beside or below every screen, Home included. Every chat is with a Heinzel (canvas
// "Chat sidebar redesign"): the panel opens the Heinzel in the link (`?chat=hz-…`, a notification's or a search's),
// else the last one you talked to in this browser, else your first that is not retired; with none yet, the first start
// names one. Its conversation lives in hz, so it survives a reload. There is no general chat any more: earlier
// general-chat sessions stay on disk untouched and unused (no migration, owner 2026-11-14).
import { useEffect } from "react";
import { useSearchParams } from "react-router";
import { useHeinzelSessions } from "@/api/queries";
import { DurableSession } from "@/chat/DurableSession";
import { defaultHeinzel, rememberedHeinzel } from "@/chat/defaultHeinzel";
import { FirstHeinzel, settleFirstHeinzel } from "@/chat/FirstHeinzel";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { clearChatLink, isDurableSession } from "@/lib/chatIntent";

export function ChatPanel({ onCollapse }: { onCollapse: () => void }) {
  const [params] = useSearchParams();
  const durableId = params.get("chat");
  if (durableId && isDurableSession(durableId))
    return <DurableSession key={durableId} sessionId={durableId} onClose={onCollapse} />;
  return <DefaultHeinzel onCollapse={onCollapse} />;
}

/** The Heinzel the panel opens without a link; with none yet, the first start names one. */
function DefaultHeinzel({ onCollapse }: { onCollapse: () => void }) {
  const [, setParams] = useSearchParams();
  const heinzels = useHeinzelSessions();
  const listed = (heinzels.data?.sessions?.length ?? 0) > 0;
  // Once any Heinzel is listed the first start is over: a request a lost answer left behind is settled here.
  useEffect(() => {
    if (listed) settleFirstHeinzel();
  }, [listed]);
  if (heinzels.isPending)
    return (
      <p role="status" className="p-3 text-sm text-muted-foreground">
        {t("state.loading")}
      </p>
    );
  if (heinzels.isError)
    return (
      <div role="alert" className="space-y-2 p-3 text-sm">
        <p>{t("oneChat.loadError")}</p>
        <Button type="button" variant="outline" onClick={() => void heinzels.refetch()}>
          {t("oneChat.retry")}
        </Button>
      </div>
    );
  const fallback = defaultHeinzel(heinzels.data?.sessions ?? [], rememberedHeinzel());
  if (fallback) return <DurableSession key={fallback} sessionId={fallback} onClose={onCollapse} />;
  return (
    <FirstHeinzel
      onClose={onCollapse}
      onCreated={(sessionId) =>
        setParams((current) => {
          const next = new URLSearchParams(current);
          clearChatLink(next);
          next.set("chat", sessionId);
          return next;
        })
      }
    />
  );
}
