// The app's one chat, shared: the panel beside or below every screen shows it, and Home's cards start a chat about
// themselves in it, so there is never a second conversation on screen.
import { createContext, type ReactNode, type RefObject, useContext, useRef } from "react";
import { OPEN_CHAT } from "@/lib/chatIntent";
import { type Chat, useChat } from "./useChat";

type SharedChat = { chat: Chat; inputRef: RefObject<HTMLTextAreaElement | null>; focus: () => void };

const ChatContext = createContext<SharedChat | null>(null);

export function ChatProvider({ children }: { children: ReactNode }) {
  const chat = useChat();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // A new value each render, as `chat` is: its users follow every change of the conversation anyway.
  const value = {
    chat,
    inputRef,
    focus: () => {
      const input = inputRef.current;
      const alreadyVisible = input && !input.closest("[hidden]");
      window.dispatchEvent(new Event(OPEN_CHAT));
      if (alreadyVisible) input.focus();
      else
        requestAnimationFrame(() => {
          if (!inputRef.current?.closest("[hidden]")) inputRef.current?.focus();
        });
    },
  };
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

/** The shared chat; only inside the shell, which provides it. */
export function useSharedChat(): SharedChat {
  const shared = useContext(ChatContext);
  if (!shared) throw new Error("useSharedChat needs a ChatProvider around it (the Shell has one)");
  return shared;
}
