// One chat for the whole app (ADR 0045): Home and the right sidebar show the same hz session, kept by hz and
// remembered in this tab, so moving between screens continues the conversation.
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { chatKey, openChat, sendChat, useChatSession, useDecide } from "@/api/chat";
import type { SchemaChatSession } from "@/api/client";

const KEY = "hz.chat";

function remembered(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function remember(id: string | null) {
  try {
    if (id) sessionStorage.setItem(KEY, id);
    else sessionStorage.removeItem(KEY);
  } catch {
    // No storage: hz still keeps the session, it just isn't reopened on reload.
  }
}

/** The answer being written: the message sent, the text streamed so far and the tools called. */
export type Live = { message: string; text: string; tools: string[] };

export function useChat() {
  const [params, setParams] = useSearchParams();
  const [sessionId, setSessionId] = useState<string | null>(() => params.get("chat") ?? remembered());
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chat = useChatSession(sessionId);
  // A remembered session still loading: sending now would open another one and leave it behind.
  const loading = sessionId != null && chat.isLoading;
  const decide = useDecide();
  const client = useQueryClient();
  const opened = useRef<string | null>(null);

  const adopt = useCallback(
    (session: SchemaChatSession) => {
      client.setQueryData(chatKey(session.id), session);
      setSessionId(session.id);
      remember(session.id);
    },
    [client],
  );

  /** A new session about `context` (`event:<id>`, `route:/doctor`), with what the opener knows in `detail`. */
  const openAbout = useCallback(
    async (context: string, detail?: string) => {
      setError(null);
      try {
        adopt(await openChat(context, detail));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    },
    [adopt],
  );

  // A link from a notification: `?chat=<id>` resumes, `?chat-context=event:<id>` opens a chat about it; both are then
  // dropped from the address, so a reload doesn't open another chat.
  useEffect(() => {
    const context = params.get("chat-context");
    const linked = params.get("chat");
    if (!linked && !context) return;
    if (linked) {
      setSessionId(linked);
      remember(linked);
    }
    if (context && opened.current !== context) {
      opened.current = context;
      void openAbout(context);
    }
    const next = new URLSearchParams(params);
    next.delete("chat");
    next.delete("chat-context");
    setParams(next, { replace: true });
  }, [params, setParams, openAbout]);

  // A remembered session hz no longer keeps (pruned, removed): start afresh.
  useEffect(() => {
    if (chat.error && sessionId) {
      setSessionId(null);
      remember(null);
    }
  }, [chat.error, sessionId]);

  /** Send `text`; without a session, open one about `context` first. False when it failed. */
  const send = async (text: string, context: string): Promise<boolean> => {
    if (!text.trim() || live || loading) return false;
    setLive({ message: text, text: "", tools: [] });
    setError(null);
    let target: SchemaChatSession | undefined = chat.data;
    try {
      if (!target) {
        target = await openChat(context);
        adopt(target);
      }
      const answered = await sendChat(target.id, text, ({ event, data }) => {
        if (event === "text" && typeof data.text === "string") {
          const delta = data.text;
          setLive((current) => current && { ...current, text: current.text + delta });
        }
        if (event === "tool" && typeof data.name === "string") {
          const name = data.name;
          setLive((current) => current && { ...current, tools: [...current.tools, name] });
        }
      });
      adopt(answered);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      // What hz kept of it (the message, or a stopped answer) shows; the session's own id, not this render's.
      if (target) await client.invalidateQueries({ queryKey: chatKey(target.id) });
      return false;
    } finally {
      setLive(null);
    }
  };

  const fresh = () => {
    setSessionId(null);
    remember(null);
    setError(null);
  };

  /** The Workshop: a new session about nothing in particular. */
  const openGeneral = async () => {
    setError(null);
    try {
      adopt(await openChat(null));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  /** Continue a session hz keeps (from the switcher), as a `?chat=<id>` link would. */
  const select = (id: string) => {
    setSessionId(id);
    remember(id);
    setError(null);
  };

  const decideAction = (action: string, confirm: boolean) => {
    if (chat.data) decide.mutate({ session: chat.data.id, action, confirm });
  };

  return {
    session: chat.data,
    live,
    error: error ?? decide.error?.message ?? null,
    deciding: decide.isPending,
    loading,
    send,
    fresh,
    select,
    openGeneral,
    openAbout,
    decide: decideAction,
  };
}

export type Chat = ReturnType<typeof useChat>;
