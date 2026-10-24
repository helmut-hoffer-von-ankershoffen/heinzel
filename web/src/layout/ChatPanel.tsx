// The right sidebar of every screen but Home: the app's one chat (`hz chat`, ADR 0045, 0039), opened about the screen
// it was started on. The session lives in hz, so it survives a reload and `hz chat --continue` picks it up in a
// terminal. `?chat=<session>` opens a session (a notification's link); `?chat-context=<kind:ref>` opens one about it.
import { MessageSquare, PanelRightClose, Plus } from "lucide-react";
import { useLocation } from "react-router";
import { Composer, Conversation, Costs } from "@/chat/Conversation";
import { useChat } from "@/chat/useChat";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

export function ChatPanel({ onCollapse }: { onCollapse: () => void }) {
  const location = useLocation();
  const chat = useChat();
  const context = chat.session?.context;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border p-3">
        <MessageSquare aria-hidden className="size-4" />
        <h2 className="text-sm font-semibold">{t("chat.title")}</h2>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={chat.fresh}
          disabled={chat.live != null || !chat.session}
          title={t("chat.new")}
        >
          <Plus aria-hidden />
          {t("chat.new")}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={onCollapse}
          aria-label={t("chat.collapse")}
          title={t("chat.collapse")}
        >
          <PanelRightClose aria-hidden />
        </Button>
      </div>
      <div className="border-b border-border bg-muted px-3 py-2">
        <Costs chat={chat} />
        {context && (
          <p className="mt-1 text-xs text-muted-foreground">
            {t("chat.context", { about: `${context.kind} ${context.ref}`.trim() })}
          </p>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <Conversation chat={chat} />
      </div>
      <Composer chat={chat} context={`route:${location.pathname}`} className="border-t border-border p-3" />
    </div>
  );
}
