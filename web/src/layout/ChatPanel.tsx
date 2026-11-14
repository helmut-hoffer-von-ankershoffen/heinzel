// The app's one chat (`hz chat`, ADR 0045, 0039), beside or below every screen, Home included: opened about the screen
// it was started on. The session lives in hz, so it survives a reload and `hz chat --continue` picks it up in a
// terminal. `?chat=<session>` opens a session (a notification's link); `?chat-context=<kind:ref>` opens one about it.
// The header's switcher picks another session, the Workshop or a new chat.
// With the flag `one_chat` every chat is with a Heinzel (bead agqz, canvas "Chat sidebar redesign"): the panel opens the
// Heinzel in the link, else the last one you talked to, else your first; with none yet it asks for a name. The general
// chat stays reachable from More → Earlier chats until its history moves into the default Heinzel.
import { useQueryClient } from "@tanstack/react-query";
import { PanelRightClose, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { api, problem } from "@/api/client";
import { useHeinzelSessions, usePreviewFeature } from "@/api/queries";
import { useSharedChat } from "@/chat/ChatContext";
import { Composer, Conversation } from "@/chat/Conversation";
import { DurableSession, type OneChat } from "@/chat/DurableSession";
import { defaultHeinzel, rememberedHeinzel } from "@/chat/defaultHeinzel";
import { FirstHeinzel } from "@/chat/FirstHeinzel";
import { SessionSwitcher } from "@/chat/SessionSwitcher";
import { isDurableSession } from "@/chat/useChat";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { clearChatLink, createDraftPath } from "@/lib/chatIntent";

const PERSONAL_HANDOFF = "hz.personal-heinzel.handoff";
type PersonalHandoff = { request_id: string; message: string };

function readPersonalHandoff(): PersonalHandoff | null {
  try {
    const raw = sessionStorage.getItem(PERSONAL_HANDOFF);
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    if (
      !value ||
      typeof value !== "object" ||
      !("request_id" in value) ||
      !("message" in value) ||
      typeof value.request_id !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value.request_id) ||
      typeof value.message !== "string" ||
      !value.message.trim() ||
      value.message.length > 4000
    )
      return null;
    return { request_id: value.request_id, message: value.message };
  } catch {
    return null;
  }
}

export function ChatPanel({ onCollapse }: { onCollapse: () => void }) {
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const creating = useRef(false);
  const [createError, setCreateError] = useState(false);
  const [handoff, setHandoff] = useState(readPersonalHandoff);
  const [handoffUnreadable] = useState(() => {
    try {
      return sessionStorage.getItem(PERSONAL_HANDOFF) !== null && !readPersonalHandoff();
    } catch {
      return true;
    }
  });
  const [params, setParams] = useSearchParams();
  const durableId = params.get("chat");
  const oneChat = usePreviewFeature("one_chat");
  const heinzels = useHeinzelSessions(oneChat);
  const [earlier, setEarlier] = useState(false);
  const openHeinzel = (sessionId: string) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      clearChatLink(next);
      next.set("chat", sessionId);
      return next;
    });
  const controls: OneChat = {
    onCollapse,
    onEarlier: () => {
      setEarlier(true);
      setParams((current) => {
        const next = new URLSearchParams(current);
        clearChatLink(next);
        return next;
      });
    },
  };
  const { chat, inputRef, startNewChat } = useSharedChat();
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
  const empty = !chat.loading && !chat.session?.messages?.length && !chat.live;
  // A message handed over creates its Heinzel at once; without one, the page's draft asks for a name and look first.
  const createPersonal = async (initialMessage: string) => {
    if (creating.current) return false;
    creating.current = true;
    setCreateError(false);
    try {
      const key = "hz.personal-heinzel.creation";
      const priorRaw = sessionStorage.getItem(PERSONAL_HANDOFF);
      const prior = readPersonalHandoff();
      if (priorRaw !== null && (!prior || !initialMessage || prior.message !== initialMessage))
        throw new Error("An earlier creation request needs recovery.");
      const storedId = localStorage.getItem(key);
      if (prior && storedId && storedId !== prior.request_id) throw new Error("The creation request changed.");
      const request_id = prior?.request_id ?? storedId ?? crypto.randomUUID();
      localStorage.setItem(key, request_id);
      if (initialMessage) {
        const encoded = JSON.stringify({ request_id, message: initialMessage });
        sessionStorage.setItem(PERSONAL_HANDOFF, encoded);
        if (sessionStorage.getItem(PERSONAL_HANDOFF) !== encoded)
          throw new Error("The request could not be preserved.");
        setHandoff({ request_id, message: initialMessage });
      }
      const { data, error, response } = await api.POST("/heinzel/new-chat", {
        body: { request_id, name: "New Heinzel" },
      });
      if (!response.ok || !data) throw problem(response.status, error);
      if (!/^hz-[0-9A-HJKMNP-TV-Z]{26}$/.test(data.session_id) || data.heinzel_id !== data.session_id) {
        throw new Error("Invalid creation acknowledgement");
      }
      if (initialMessage) {
        const draftKey = `hz.durable.composer.${data.session_id}`;
        const raw = sessionStorage.getItem(draftKey);
        const draft = {
          version: 1,
          session_id: data.session_id,
          text: initialMessage,
          pending: null,
          agentPending: null,
          checkInRequest: null,
        };
        const encoded = JSON.stringify(draft);
        if (raw !== null && raw !== encoded) throw new Error("This agent already has unsent work in this tab.");
        sessionStorage.setItem(draftKey, encoded);
        if (sessionStorage.getItem(draftKey) !== encoded) throw new Error("The message could not be preserved.");
      }
      localStorage.removeItem(key);
      if (initialMessage) {
        sessionStorage.removeItem(PERSONAL_HANDOFF);
        if (sessionStorage.getItem(PERSONAL_HANDOFF) !== null) throw new Error("The request could not be cleared.");
        setHandoff(null);
      }
      client.setQueryData(["heinzel", "session", data.session_id], data);
      void client.invalidateQueries({ queryKey: ["heinzel", "sessions"] });
      setParams((current) => {
        const next = new URLSearchParams(current);
        for (const key of [
          "agent-chat",
          "approval-chat",
          "approval-request",
          "agent-open-request",
          "contract-chat",
          "schedule-chat",
          "schedule-request",
          "notification-chat",
          "connected-chat",
        ])
          next.delete(key);
        next.set("chat", data.session_id);
        return next;
      });
      return true;
    } catch {
      setCreateError(true);
      return false;
    } finally {
      creating.current = false;
    }
  };
  if (durableId && isDurableSession(durableId)) {
    return (
      <DurableSession
        key={durableId}
        sessionId={durableId}
        oneChat={oneChat ? controls : undefined}
        onClose={() =>
          setParams((current) => {
            const next = new URLSearchParams(current);
            clearChatLink(next);
            return next;
          })
        }
      />
    );
  }
  const fallback = oneChat ? defaultHeinzel(heinzels.data?.sessions ?? [], rememberedHeinzel()) : null;
  if (oneChat && !earlier) {
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
    if (fallback) return <DurableSession key={fallback} sessionId={fallback} oneChat={controls} onClose={onCollapse} />;
    return <FirstHeinzel onCreated={openHeinzel} onClose={onCollapse} />;
  }
  return (
    <div className="flex h-full flex-col">
      <div className="chat-panel-header flex h-10 shrink-0 items-center sm:h-12 gap-1 border-b-[0.5px] border-foreground/20 px-3">
        <h2 className="sr-only">{t("chat.title")}</h2>
        <SessionSwitcher chat={chat} onNew={startNewChat} onCreatePersonal={() => navigate(createDraftPath(params))} />
        {oneChat && fallback && (
          <Button type="button" variant="ghost" size="sm" data-pane-action onClick={() => setEarlier(false)}>
            {t("oneChat.backTo", {
              name: heinzels.data?.sessions?.find((item) => item.session_id === fallback)?.name ?? "",
            })}
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="text-icon"
          onClick={startNewChat}
          aria-label={t("chat.new")}
          title={empty ? t("chat.isNew") : t("chat.new")}
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
      {createError && (
        <p role="alert" className="px-3 text-sm">
          {t("personal.createError")}
        </p>
      )}
      {handoffUnreadable && (
        <p role="alert" className="px-3 text-sm">
          An earlier agent creation request could not be read. It has not been cleared or retried.
        </p>
      )}
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto p-3">
        {context && (
          <p className="mb-3 text-xs text-muted-foreground">
            {t("chat.context", { about: `${context.kind} ${context.ref}`.trim() })}
          </p>
        )}
        <Conversation chat={chat} empty={location.pathname === "/" ? t("today.empty") : undefined} />
        <div ref={end} />
      </div>
      <hr className="shrink-0 border-border" />
      <div className="chat-panel-composer p-3">
        <Composer
          chat={chat}
          context={`route:${location.pathname}`}
          inputRef={inputRef}
          onCreatePersonal={createPersonal}
          initialPersonalMessage={handoff?.message}
        />
      </div>
    </div>
  );
}
