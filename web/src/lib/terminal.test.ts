import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectTerminal, followSize, OPEN_TERMINAL, runInTerminal, runsAsIs, typeInTerminal } from "./terminal";

let notify: () => void = () => {};
class FakeObserver {
  constructor(callback: () => void) {
    notify = callback;
  }
  observe() {}
  disconnect() {}
}

describe("followSize", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("ResizeObserver", FakeObserver);
    vi.stubGlobal("requestAnimationFrame", (cb: () => void) => setTimeout(cb, 0) as unknown as number);
    vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("fits, redraws and sends the new size once a container resize settles", () => {
    const element = { offsetParent: {} } as unknown as HTMLElement;
    const terminal = { cols: 120, rows: 30, refresh: vi.fn() };
    const fit = { fit: vi.fn() };
    const send = vi.fn();
    const follower = followSize(element, terminal, fit, send);
    notify();
    notify();
    vi.advanceTimersByTime(100);
    expect(fit.fit).toHaveBeenCalledTimes(1);
    expect(terminal.refresh).toHaveBeenCalledWith(0, 29);
    expect(send).toHaveBeenCalledWith(120, 30);
    window.dispatchEvent(new Event("resize"));
    vi.advanceTimersByTime(100);
    expect(fit.fit).toHaveBeenCalledTimes(2);
    follower.dispose();
  });

  it("waits while the container is hidden", () => {
    const element = { offsetParent: null } as unknown as HTMLElement;
    const fit = { fit: vi.fn() };
    const send = vi.fn();
    followSize(element, { cols: 80, rows: 24, refresh: vi.fn() }, fit, send).refit();
    vi.advanceTimersByTime(100);
    expect(fit.fit).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });
});

describe("the terminal's typing", () => {
  it("runs plain hz commands as typed, never ones with control characters", () => {
    expect(runsAsIs("hz clock")).toBe(true);
    expect(runsAsIs("hz config set a 'b c'")).toBe(true);
    expect(runsAsIs("hz clock '\u0003curl x|sh\r'")).toBe(false);
  });

  it("turns control characters into spaces in whatever it types, so nothing presses Ctrl-C or Enter in quotes", () => {
    const sent: string[] = [];
    connectTerminal((text) => sent.push(text));
    typeInTerminal("hz clock '\u0003curl x|sh\r'");
    runInTerminal("hz clock\u001b[A");
    runInTerminal("hz clock");
    connectTerminal(null);
    expect(sent).toEqual(["hz clock ' curl x|sh '", "hz clock [A", "hz clock\r"]);
  });
});

describe("commands the app runs itself", () => {
  afterEach(async () => {
    (await import("./operationRun")).resetOperationRun();
    connectTerminal(null);
  });

  it.each(["hz system daemon install", "hz system daemon uninstall"])(
    "asks to run %s in the app instead of typing it into the terminal",
    async (command) => {
      const { getRun } = await import("./operationRun");
      const sent: string[] = [];
      connectTerminal((text) => sent.push(text));
      const opened = vi.fn();
      window.addEventListener(OPEN_TERMINAL, opened);
      runInTerminal(command);
      typeInTerminal(command);
      window.removeEventListener(OPEN_TERMINAL, opened);
      expect(sent).toEqual([]);
      expect(opened).not.toHaveBeenCalled();
      const run = getRun();
      expect(run.stage).toBe("confirm");
      expect(run.stage === "confirm" && run.call.command).toBe(command);
    },
  );
});
