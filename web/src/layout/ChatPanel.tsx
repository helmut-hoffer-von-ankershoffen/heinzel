// The app's chat (ADR 0157, 0045), beside or below every screen, Home included. Every chat is with a Heinzel (canvas
// "Chat sidebar redesign"): the panel opens the Heinzel in the link (`?chat=hz-…`, a notification's or a search's),
// else the last one you talked to in this browser, else your first that is not retired; with none yet, the first start
// names one. Its conversation lives in hz, so it survives a reload. There is no general chat any more: earlier
// general-chat sessions stay on disk untouched and unused (no migration, owner 2026-11-14).
import { useEffect, useSyncExternalStore } from "react";
import { useSearchParams } from "react-router";
import { useHeinzelSessions } from "@/api/queries";
import { DurableSession } from "@/chat/DurableSession";
import { defaultHeinzel, rememberedHeinzel } from "@/chat/defaultHeinzel";
import { FirstHeinzel, settleFirstHeinzel } from "@/chat/FirstHeinzel";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { clearChatLink, isDurableSession } from "@/lib/chatIntent";
import { splash } from "@/splash/store";

export function ChatPanel({ onCollapse }: { onCollapse: () => void }) {
  const [params, setParams] = useSearchParams();
  const linked = params.get("chat");
  const durableId = linked && isDurableSession(linked) ? linked : null;
  // The list is read only without a link: it names the default Heinzel, or says there is none yet.
  const heinzels = useHeinzelSessions(durableId === null);
  // While the splash is up (hz isn't answering, or is restarting) it already says what is wrong: the panel's error is
  // then a status, not a second alert (role="alert" only for a new failure in this view).
  const splashUp = useSyncExternalStore(splash.subscribe, splash.get).phase !== "ready";
  const listed = (heinzels.data?.sessions?.length ?? 0) > 0;
  // Once any Heinzel is listed the first start is over: a request a lost answer left behind is settled here.
  useEffect(() => {
    if (listed) settleFirstHeinzel();
  }, [listed]);
  // One place and one key for the Heinzel's chat, linked or the default: when Start links the Heinzel the panel already
  // shows, it is the same chat, so nothing typed right after Start is lost to a fresh composer.
  const chat = (sessionId: string) => <DurableSession key={sessionId} sessionId={sessionId} onClose={onCollapse} />;
  if (durableId) return chat(durableId);
  if (heinzels.isPending)
    return (
      <p role="status" className="p-3 text-sm text-muted-foreground">
        {t("state.loading")}
      </p>
    );
  if (heinzels.isError)
    return (
      <div role={splashUp ? "status" : "alert"} className="space-y-2 p-3 text-sm">
        <p>{t("oneChat.loadError")}</p>
        <Button type="button" variant="outline" onClick={() => void heinzels.refetch()}>
          {t("oneChat.retry")}
        </Button>
      </div>
    );
  const fallback = defaultHeinzel(heinzels.data?.sessions ?? [], rememberedHeinzel());
  if (fallback) return chat(fallback);
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
