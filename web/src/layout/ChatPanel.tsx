// The app's one chat (`hz chat`, ADR 0045, 0039), beside or below every screen, Home included: opened about the screen
// it was started on. The session lives in hz, so it survives a reload and `hz chat --continue` picks it up in a
// terminal. `?chat=<session>` opens a session (a notification's link); `?chat-context=<kind:ref>` opens one about it.
// The header's switcher picks one of your Heinzelmännchen, Ask hz or an earlier question; + starts a new
// Heinzelmännchen.
import { PanelRightClose, Plus } from "lucide-react";
import { useEffect, useRef } from "react";
import { useLocation } from "react-router";
import { useSharedChat } from "@/chat/ChatContext";
import { Composer, Conversation, Costs } from "@/chat/Conversation";
import { NEW_HEINZEL, SessionSwitcher } from "@/chat/SessionSwitcher";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

export function ChatPanel({ onCollapse }: { onCollapse: () => void }) {
  const location = useLocation();
  const { chat, inputRef, focus } = useSharedChat();
  const context = chat.session?.context;
  const end = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const count = (chat.session?.messages?.length ?? 0) + (chat.live ? 1 : 0);
  // Keep the newest message in view as the conversation grows or the answer streams.
  // biome-ignore lint/correctness/useExhaustiveDependencies: scrolls when the conversation changes
  useEffect(() => {
    // Only while the person is at the end: someone scrolled up to read stays where they are.
    const box = scroller.current;
    if (box && box.scrollHeight - box.scrollTop - box.clientHeight > 120) return;
    end.current?.scrollIntoView?.({ block: "end" });
  }, [count, chat.live?.text]);
  // A new Heinzelmännchen starts as a conversation: the agent asks what it keeps true, then proposes its contract.
  // One that hasn't started yet is reused, and a second click while one opens does nothing.
  const starting = useRef(false);
  const about = chat.session?.context;
  const unstarted = about?.kind === "text" && about.ref === NEW_HEINZEL.slice(5) && !chat.session?.messages?.length;
  const fresh = () => {
    if (unstarted) return focus();
    if (starting.current) return;
    starting.current = true;
    void chat
      .openAbout(NEW_HEINZEL)
      .then(focus)
      .finally(() => {
        starting.current = false;
      });
  };
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center gap-1 border-b-[0.5px] border-foreground/20 pr-2 pl-3">
        <h2 className="sr-only">{t("chat.title")}</h2>
        <SessionSwitcher chat={chat} onNew={fresh} />
        <Button
          variant="ghost"
          size="icon"
          className="text-icon"
          onClick={fresh}
          aria-label={t("chat.new")}
          title={t("chat.newHelp")}
        >
          <Plus aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="text-icon"
          onClick={onCollapse}
          aria-label={t("chat.collapse")}
          title={t("chat.collapse")}
        >
          <PanelRightClose aria-hidden />
        </Button>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto p-3">
        {context && (
          <p className="mb-3 text-xs text-muted-foreground">
            {t("chat.context", { about: `${context.kind} ${context.ref}`.trim() })}
          </p>
        )}
        <Conversation chat={chat} empty={location.pathname === "/" ? t("today.empty") : undefined} />
        <div ref={end} />
      </div>
      <div className="border-t border-border p-3">
        <Composer chat={chat} context={`route:${location.pathname}`} inputRef={inputRef} />
        <Costs chat={chat} className="mt-1.5" />
      </div>
    </div>
  );
}
