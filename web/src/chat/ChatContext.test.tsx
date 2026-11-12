import { act, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OPEN_CHAT } from "@/lib/chatIntent";
import { ChatProvider, useSharedChat } from "./ChatContext";

vi.mock("./useChat", () => ({ useChat: () => ({ marker: "the chat" }) }));

function Probe() {
  const { chat, inputRef, focus } = useSharedChat();
  return (
    <div>
      <span>{(chat as unknown as { marker: string }).marker}</span>
      <textarea ref={inputRef} aria-label="Message" />
      <button type="button" onClick={focus}>
        focus
      </button>
    </div>
  );
}

describe("The app's one shared chat", () => {
  afterEach(() => vi.restoreAllMocks());

  it("refuses to be used outside the shell", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useSharedChat())).toThrow(/needs a ChatProvider/);
  });

  it("gives every user the same chat", () => {
    render(
      <ChatProvider>
        <Probe />
      </ChatProvider>,
    );
    expect(screen.getByText("the chat")).toBeInTheDocument();
  });

  it("opens the chat and focuses the input when it is already visible", () => {
    const opened = vi.fn();
    window.addEventListener(OPEN_CHAT, opened);
    render(
      <ChatProvider>
        <Probe />
      </ChatProvider>,
    );
    act(() => screen.getByRole("button", { name: "focus" }).click());
    expect(opened).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Message")).toHaveFocus();
    window.removeEventListener(OPEN_CHAT, opened);
  });

  it("waits for the hidden chat to show before it focuses the input", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => frames.push(cb));
    render(
      <div hidden>
        <ChatProvider>
          <Probe />
        </ChatProvider>
      </div>,
    );
    act(() => screen.getByRole("button", { name: "focus", hidden: true }).click());
    expect(screen.getByLabelText("Message", { selector: "textarea" })).not.toHaveFocus();
    expect(frames).toHaveLength(1);
  });
});
