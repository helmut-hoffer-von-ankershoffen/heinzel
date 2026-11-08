import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { connectTerminal } from "@/lib/terminal";
import { READY_FALLBACK_MS, READY_QUIET_MS, TerminalPanel } from "./TerminalPanel";

const { terminals } = vi.hoisted(() => ({
  terminals: [] as Array<{
    options: { theme?: { background?: string } };
    modes: { applicationCursorKeysMode: boolean };
    title: (value: string) => void;
    osc: Map<number, (value: string) => boolean>;
  }>,
}));
const { desktopState } = vi.hoisted(() => ({ desktopState: vi.fn(() => "off") }));
vi.mock("@/notifications/desktop", () => ({ desktopState }));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    options: { theme?: { background?: string } } = {};
    record = {
      options: this.options,
      modes: { applicationCursorKeysMode: false },
      title: (_value: string) => {},
      osc: new Map<number, (value: string) => boolean>(),
    };
    modes = this.record.modes;
    parser = {
      registerOscHandler: (id: number, callback: (value: string) => boolean) => {
        this.record.osc.set(id, callback);
        return { dispose() {} };
      },
    };
    constructor(options: object = {}) {
      Object.assign(this.options, options);
      terminals.push(this.record);
    }
    loadAddon() {}
    open() {}
    onTitleChange(callback: (value: string) => void) {
      this.record.title = callback;
      return { dispose() {} };
    }
    onData() {
      return { dispose() {} };
    }
    onSelectionChange() {
      return { dispose() {} };
    }
    focus() {}
    write() {}
    dispose() {}
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class {} }));
vi.mock("@/lib/terminal", () => ({ connectTerminal: vi.fn(), followSize: () => ({ refit() {}, dispose() {} }) }));
class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  send = vi.fn();
  close() {}
  onopen: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  constructor() {
    Socket.instances.push(this);
  }
}
afterEach(() => {
  localStorage.clear();
  terminals.length = 0;
  Socket.instances.length = 0;
  desktopState.mockReturnValue("off");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("typing a command for the person", () => {
  afterEach(() => vi.useRealTimers());

  it("waits until the shell has drawn its prompt, or the line would be echoed twice with a stray %", () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", Socket);
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    const socket = Socket.instances[0];
    act(() => socket?.onopen?.());
    expect(connectTerminal).not.toHaveBeenCalledWith(expect.any(Function));
    // The shell speaks (its prompt arrives in pieces) and a command may only go once it has been quiet a moment.
    act(() => socket?.onmessage?.({ data: new ArrayBuffer(8) }));
    act(() => void vi.advanceTimersByTime(READY_QUIET_MS - 1));
    act(() => socket?.onmessage?.({ data: new ArrayBuffer(8) }));
    act(() => void vi.advanceTimersByTime(READY_QUIET_MS - 1));
    expect(connectTerminal).not.toHaveBeenCalledWith(expect.any(Function));
    act(() => void vi.advanceTimersByTime(1));
    expect(connectTerminal).toHaveBeenCalledWith(expect.any(Function));
  });

  it("does not hold a command back for good when the shell never says anything", () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", Socket);
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    act(() => Socket.instances[0]?.onopen?.());
    act(() => void vi.advanceTimersByTime(READY_FALLBACK_MS - 1));
    expect(connectTerminal).not.toHaveBeenCalledWith(expect.any(Function));
    act(() => void vi.advanceTimersByTime(1));
    expect(connectTerminal).toHaveBeenCalledWith(expect.any(Function));
  });
});

const pointer = (coarse: boolean) =>
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: coarse && query === "(pointer: coarse)",
    addEventListener() {},
    removeEventListener() {},
  }));

describe("special keys", () => {
  it.each([
    [false, false],
    [true, true],
  ])("shows Esc, Tab, Ctrl and arrows only on a touch screen (coarse pointer %s)", (coarse, shown) => {
    vi.stubGlobal("WebSocket", Socket);
    pointer(coarse);
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    expect(screen.queryByRole("group", { name: "Terminal keys" }) !== null).toBe(shown);
    expect(screen.queryByRole("button", { name: "Escape" }) !== null).toBe(shown);
    expect(screen.queryByRole("button", { name: "Arrow up" }) !== null).toBe(shown);
  });

  it("shows no keys where the browser cannot tell the pointer", () => {
    vi.stubGlobal("WebSocket", Socket);
    vi.stubGlobal("matchMedia", undefined);
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    expect(screen.queryByRole("group", { name: "Terminal keys" })).toBeNull();
  });
});

describe("renaming tabs", () => {
  it("renames a tab from its pencil, keeps the name against the shell's title, and clears it back to automatic", async () => {
    vi.stubGlobal("WebSocket", Socket);
    const user = userEvent.setup();
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Rename Terminal" }));
    const field = screen.getByRole("textbox", { name: "Tab name" });
    await user.clear(field);
    await user.type(field, "build box{Enter}");
    expect(screen.getByRole("tab", { name: "build box" })).toBeInTheDocument();
    act(() => Socket.instances[0]?.onopen?.());
    act(() => terminals[0]?.title("zsh"));
    expect(screen.getByRole("tab", { name: "build box" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Rename build box" }));
    await user.clear(screen.getByRole("textbox", { name: "Tab name" }));
    await user.keyboard("{Enter}");
    expect(screen.getByRole("tab", { name: "zsh" })).toBeInTheDocument();
  });

  it("renames on double click and F2, and Escape leaves the name as it was", async () => {
    vi.stubGlobal("WebSocket", Socket);
    const user = userEvent.setup();
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    await user.dblClick(screen.getByRole("tab", { name: "Terminal" }));
    await user.type(screen.getByRole("textbox", { name: "Tab name" }), " one{Escape}");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByRole("tab", { name: "Terminal" })).toBeInTheDocument();
    screen.getByRole("tab", { name: "Terminal" }).focus();
    await user.keyboard("{F2}");
    await user.keyboard("{Enter}");
    expect(screen.getByRole("tab", { name: "Terminal" })).toBeInTheDocument();
  });

  it("offers Rename tab and Close tab first in the options menu, with the F2 hint", async () => {
    vi.stubGlobal("WebSocket", Socket);
    const user = userEvent.setup();
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Terminal options" }));
    const items = screen.getAllByRole("menuitem");
    expect(items[0]).toHaveTextContent("Rename tab");
    expect(items[0]).toHaveTextContent("F2");
    expect(items[1]).toHaveTextContent("Close tab");
    await user.click(items[0] as HTMLElement);
    expect(screen.queryByRole("menu")).toBeNull();
    const field = screen.getByRole("textbox", { name: "Tab name" });
    expect(field).toHaveFocus();
    await user.type(field, "{Control>}a{/Control}deploy{Enter}");
    expect(screen.getByRole("tab", { name: "deploy" })).toBeInTheDocument();
  });

  it("closes the shown tab from the options menu", async () => {
    vi.stubGlobal("WebSocket", Socket);
    const user = userEvent.setup();
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "New terminal tab" }));
    await user.click(screen.getByRole("button", { name: "Terminal options" }));
    await user.click(screen.getByRole("menuitem", { name: /Close tab/ }));
    expect(screen.getAllByRole("tab")).toHaveLength(1);
    expect(screen.getByRole("tab", { name: "Terminal" })).toBeInTheDocument();
  });

  it("remembers names across reloads, per tab, and an emptied name is forgotten", async () => {
    vi.stubGlobal("WebSocket", Socket);
    const user = userEvent.setup();
    const first = render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Rename Terminal" }));
    await user.type(screen.getByRole("textbox", { name: "Tab name" }), "{Control>}a{/Control}logs{Enter}");
    first.unmount();
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    expect(screen.getByRole("tab", { name: "logs" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Rename logs" }));
    await user.clear(screen.getByRole("textbox", { name: "Tab name" }));
    await user.keyboard("{Enter}");
    expect(JSON.parse(localStorage.getItem("hz.terminal.names") ?? "{}")).toEqual({});
  });

  it("ignores a damaged stored name list", () => {
    vi.stubGlobal("WebSocket", Socket);
    localStorage.setItem("hz.terminal.names", '{"1": 5, "2": ""}');
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    expect(screen.getByRole("tab", { name: "Terminal" })).toBeInTheDocument();
  });

  it("renames on a long press with touch, not on a short tap or with a mouse", () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", Socket);
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    const tab = screen.getByRole("tab", { name: "Terminal" });
    fireEvent.pointerDown(tab, { pointerType: "touch" });
    act(() => void vi.advanceTimersByTime(300));
    fireEvent.pointerUp(tab, { pointerType: "touch" });
    act(() => void vi.advanceTimersByTime(1000));
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.pointerDown(tab, { pointerType: "mouse" });
    act(() => void vi.advanceTimersByTime(1000));
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.pointerDown(tab, { pointerType: "touch" });
    act(() => void vi.advanceTimersByTime(600));
    expect(screen.getByRole("textbox", { name: "Tab name" })).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("names each tab on its own", async () => {
    vi.stubGlobal("WebSocket", Socket);
    const user = userEvent.setup();
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "New terminal tab" }));
    await user.click(screen.getByRole("button", { name: "Rename Terminal 2" }));
    await user.type(screen.getByRole("textbox", { name: "Tab name" }), "{Control>}a{/Control}logs{Enter}");
    expect(screen.getByRole("tab", { name: "logs" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Terminal" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close logs" })).toBeInTheDocument();
  });
});

describe("terminal tabs", () => {
  it("sends mobile keys only to the active open terminal", async () => {
    vi.stubGlobal("WebSocket", Socket);
    pointer(true);
    const user = userEvent.setup();
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    const escapeKey = screen.getByRole("button", { name: "Escape" });
    expect(escapeKey).toBeDisabled();
    act(() => Socket.instances[0]?.onopen?.());
    expect(escapeKey).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "New terminal tab" }));
    expect(screen.getByRole("button", { name: "Escape" })).toBeDisabled();
    act(() => Socket.instances[1]?.onopen?.());
    const first = Socket.instances[0];
    const second = Socket.instances[1];
    first?.send.mockClear();
    second?.send.mockClear();
    for (const label of [
      "Escape",
      "Tab",
      "Control+C",
      "Control+Z",
      "Control+D",
      "Arrow left",
      "Arrow down",
      "Arrow up",
      "Arrow right",
    ]) {
      await user.click(screen.getByRole("button", { name: label }));
    }
    expect(first?.send).not.toHaveBeenCalled();
    expect(second?.send.mock.calls.map(([data]) => new TextDecoder().decode(data))).toEqual([
      "\x1b",
      "\t",
      "\x03",
      "\x1a",
      "\x04",
      "\x1b[D",
      "\x1b[B",
      "\x1b[A",
      "\x1b[C",
    ]);
    const left = screen.getByRole("button", { name: "Arrow left" });
    act(() => {
      if (terminals[1]) terminals[1].modes.applicationCursorKeysMode = true;
    });
    await user.click(left);
    expect(new TextDecoder().decode(second?.send.mock.calls.at(-1)?.[0])).toBe("\x1bOD");
    act(() => second?.onclose?.({ reason: "ended" } as CloseEvent));
    expect(left).toBeDisabled();
  });
  it("shows each terminal's title and notification on its own tab", async () => {
    vi.stubGlobal("WebSocket", Socket);
    const user = userEvent.setup();
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "New terminal tab" }));
    act(() => terminals[0]?.title("Investigate issue"));
    expect(screen.getByRole("tab", { name: "Investigate issue" })).toBeInTheDocument();
    act(() => {
      expect(terminals[0]?.osc.get(7501)?.("?")).toBe(true);
    });
    expect(new TextDecoder().decode(Socket.instances[0]?.send.mock.calls[0]?.[0])).toBe(
      "\x1b]7501;?:states=idle,working,done,blocked,error:kinds=permission,question,auth\x1b\\",
    );
    act(() => {
      terminals[0]?.osc.get(7501)?.("state=working:app=claude");
    });
    expect(screen.getByRole("tab", { name: /Investigate issue.*Working/ })).toBeInTheDocument();
    act(() => {
      terminals[0]?.osc.get(7501)?.("state=blocked:kind=permission:app=claude");
    });
    expect(screen.getByLabelText("New terminal notification")).toHaveAttribute("title", "Waiting for input");
    act(() => {
      terminals[0]?.osc.get(21337)?.("status=Waiting;indicator=#ff0000");
    });
    expect(screen.getByRole("tab", { name: /Investigate issue.*Waiting/ })).toBeInTheDocument();
    act(() => {
      terminals[0]?.osc.get(9)?.("Approval requested");
    });
    expect(screen.getByLabelText("New terminal notification")).toHaveAttribute("title", "Approval requested");
    await user.click(screen.getByRole("tab", { name: /Investigate issue/ }));
    expect(screen.queryByLabelText("New terminal notification")).not.toBeInTheDocument();
    act(() => terminals[0]?.title(""));
    expect(screen.getByRole("tab", { name: /Terminal.*Waiting/ })).toBeInTheDocument();
  });

  it("keeps a blocked child visible until it clears, and resets state", () => {
    vi.stubGlobal("WebSocket", Socket);
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    const send = (value: string) =>
      act(() => {
        terminals[0]?.osc.get(7501)?.(value);
      });
    send("state=working:app=claude");
    send("state=blocked:id=child:kind=question:app=claude");
    expect(screen.getByRole("tab", { name: /Waiting for input/ })).toBeInTheDocument();
    send("state=working:app=claude");
    expect(screen.getByRole("tab", { name: /Waiting for input/ })).toBeInTheDocument();
    send("state=clear:id=child");
    expect(screen.getByRole("tab", { name: /Working/ })).toBeInTheDocument();
    send("state=clear");
    expect(screen.queryByText("Working")).not.toBeInTheDocument();
  });

  it("shows the foreground agent's name without mixing up terminal tabs", async () => {
    vi.stubGlobal("WebSocket", Socket);
    const user = userEvent.setup();
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "New terminal tab" }));
    act(() => Socket.instances[0]?.onmessage?.({ data: JSON.stringify({ agent_title: "First task" }) }));
    expect(screen.getByRole("tab", { name: "First task" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Terminal 2" })).toBeInTheDocument();
    act(() => Socket.instances[0]?.onmessage?.({ data: JSON.stringify({ agent_title: "Renamed task" }) }));
    expect(screen.getByRole("tab", { name: "Renamed task" })).toBeInTheDocument();
    act(() => Socket.instances[0]?.onmessage?.({ data: JSON.stringify({ agent_title: null }) }));
    expect(screen.getByRole("tab", { name: "Terminal" })).toBeInTheDocument();
  });

  it("notifies once when a background agent becomes blocked, if desktop notifications are enabled", () => {
    vi.stubGlobal("WebSocket", Socket);
    const notify = vi.fn();
    const onActivate = vi.fn();
    const instances: FakeNotification[] = [];
    class FakeNotification {
      onclick: (() => void) | null = null;
      close = vi.fn();
      constructor(title: string, options: NotificationOptions) {
        notify(title, options);
        instances.push(this);
      }
    }
    vi.stubGlobal("Notification", FakeNotification);
    vi.spyOn(window, "focus").mockImplementation(() => {});
    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    desktopState.mockReturnValue("on");
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} onActivate={onActivate} />);
    act(() => {
      terminals[0]?.osc.get(7501)?.("state=blocked:kind=permission:app=claude");
      terminals[0]?.osc.get(7501)?.("state=blocked:kind=permission:app=claude");
    });
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith("Terminal", { body: "Waiting for input" });
    act(() => instances[0]?.onclick?.());
    expect(onActivate).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText("New terminal notification")).not.toBeInTheDocument();
  });
});

describe("terminal colours", () => {
  afterEach(() => {
    document.documentElement.className = "";
    document.documentElement.removeAttribute("style");
  });

  it("starts with the active theme's colours and follows a switch of theme or mode", async () => {
    vi.stubGlobal("WebSocket", Socket);
    document.documentElement.style.setProperty("--color-background", "#10203a");
    render(<TerminalPanel onClose={vi.fn()} full={false} onToggleFull={vi.fn()} />);
    expect(terminals[0]?.options.theme?.background).toBe("#10203a");
    document.documentElement.style.setProperty("--color-background", "#f4e8d0");
    await vi.waitFor(() => expect(terminals[0]?.options.theme?.background).toBe("#f4e8d0"));
    document.documentElement.style.removeProperty("--color-background");
    document.documentElement.classList.add("dark");
    await vi.waitFor(() => expect(terminals[0]?.options.theme?.background).toBe("#18231e"));
  });
});
