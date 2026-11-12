// The app's one chat, shared: the panel beside or below every screen shows it, and Home's cards start a chat about
// themselves in it, so there is never a second conversation on screen.
import { createContext, type ReactNode, type RefObject, useContext, useEffect, useRef } from "react";
import { onMenuAction } from "@/about/menu";
import { OPEN_CHAT, OPEN_NEW_CHAT } from "@/lib/chatIntent";
import { type Chat, useChat } from "./useChat";

type SharedChat = {
  chat: Chat;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  focus: () => void;
  startNewChat: () => void;
};

const ChatContext = createContext<SharedChat | null>(null);

export function ChatProvider({ children }: { children: ReactNode }) {
  const chat = useChat();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const openAndFocus = (event: string) => {
    const input = inputRef.current;
    const alreadyVisible = input && !input.closest("[hidden]");
    window.dispatchEvent(new Event(event));
    if (alreadyVisible) input.focus();
    else
      requestAnimationFrame(() => {
        if (!inputRef.current?.closest("[hidden]")) inputRef.current?.focus();
      });
  };
  // An empty chat is already new. Reuse it and focus its composer.
  const startNewChat = () => {
    if (chat.loading || chat.session?.messages?.length || chat.live) chat.fresh();
    openAndFocus(OPEN_NEW_CHAT);
  };
  const newChatAction = useRef(startNewChat);
  newChatAction.current = startNewChat;
  useEffect(() => onMenuAction(["new-chat"], () => newChatAction.current()), []);

  const value = { chat, inputRef, focus: () => openAndFocus(OPEN_CHAT), startNewChat };
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

/** The shared chat; only inside the shell, which provides it. */
export function useSharedChat(): SharedChat {
  const shared = useContext(ChatContext);
  if (!shared) throw new Error("useSharedChat needs a ChatProvider around it (the Shell has one)");
  return shared;
}
