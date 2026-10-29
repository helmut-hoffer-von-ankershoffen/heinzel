import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TerminalPanel } from "./TerminalPanel";

const { terminals } = vi.hoisted(() => ({
  terminals: [] as Array<{ title: (value: string) => void; osc: Map<number, (value: string) => boolean> }>,
}));
const { desktopState } = vi.hoisted(() => ({ desktopState: vi.fn(() => "off") }));
vi.mock("@/notifications/desktop", () => ({ desktopState }));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    options = {};
    record = { title: (_value: string) => {}, osc: new Map<number, (value: string) => boolean>() };
    parser = {
      registerOscHandler: (id: number, callback: (value: string) => boolean) => {
        this.record.osc.set(id, callback);
        return { dispose() {} };
      },
    };
    constructor() {
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
  onmessage: ((event: { data: unknown }) => void) | null = null;
  constructor() {
    Socket.instances.push(this);
  }
}
afterEach(() => {
  terminals.length = 0;
  Socket.instances.length = 0;
  desktopState.mockReturnValue("off");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("terminal tabs", () => {
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
      terminals[0]?.osc.get(7501)?.("state=working:app=codex");
    });
    expect(screen.getByRole("tab", { name: /Investigate issue.*Working/ })).toBeInTheDocument();
    act(() => {
      terminals[0]?.osc.get(7501)?.("state=blocked:kind=permission:app=codex");
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
    send("state=working:app=codex");
    send("state=blocked:id=child:kind=question:app=codex");
    expect(screen.getByRole("tab", { name: /Waiting for input/ })).toBeInTheDocument();
    send("state=working:app=codex");
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
      terminals[0]?.osc.get(7501)?.("state=blocked:kind=permission:app=codex");
      terminals[0]?.osc.get(7501)?.("state=blocked:kind=permission:app=codex");
    });
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith("Terminal", { body: "Waiting for input" });
    act(() => instances[0]?.onclick?.());
    expect(onActivate).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText("New terminal notification")).not.toBeInTheDocument();
  });
});
