// "Ask <Name>" on Today cards, widgets and the team graph (ADR 0157): every chat is with a Heinzel, so a card's question
// goes to the default Heinzel's chat, typed into its composer and never sent. It reads no earlier chat and needs no
// backend: the text waits in the composer's draft (`hz.durable.composer.<id>`) until the person sends or changes it.
import { useSearchParams } from "react-router";
import { useHeinzelSessions } from "@/api/queries";
import { clearChatLink, OPEN_CHAT, requestComposerFocus } from "@/lib/chatIntent";
import { defaultHeinzel, rememberedHeinzel } from "./defaultHeinzel";
import { prefillDurableDraft } from "./durableDraft";

export type AskHeinzel = { name: string; ask: (text: string) => void };

/** The default Heinzel to ask and how, or null while none is named (the chat's first start names one). */
export function useAskHeinzel(): AskHeinzel | null {
  const [, setParams] = useSearchParams();
  const sessions = useHeinzelSessions().data?.sessions ?? [];
  const id = defaultHeinzel(sessions, rememberedHeinzel());
  const heinzel = sessions.find((session) => session.session_id === id);
  if (!id || !heinzel) return null;
  return {
    name: heinzel.name,
    ask: (text) => {
      prefillDurableDraft(id, text);
      setParams((current) => {
        const next = new URLSearchParams(current);
        clearChatLink(next);
        next.set("chat", id);
        return next;
      });
      window.dispatchEvent(new Event(OPEN_CHAT));
      requestComposerFocus();
    },
  };
}
