import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInThisContext } from "node:vm";
import { act, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

it("reports versioned geometry for older workspaces and yields to the versioned React reporter", async () => {
  const swift = ["WorkspaceView", "NativePrompt", "TitleBarDragLayout", "Navigation"]
    .map((name) => readFileSync(resolve(process.cwd(), `../apple/App/${name}.swift`), "utf8"))
    .join("\n");
  const start = swift.indexOf("                let footerMetrics =");
  const end = swift.indexOf('                Object.defineProperty(window, "heinzelDesktop"', start);
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  const report = vi.fn().mockResolvedValue(true);
  vi.stubGlobal("webkit", { messageHandlers: { heinzelFooter: { postMessage: report } } });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  document.body.innerHTML =
    '<div data-testid="app-shell"><footer data-footer-width="390"><div data-footer-part="@version">v0.9.49</div><button>Health</button></footer></div>';
  const bar = document.querySelector("footer");
  if (!bar) throw new Error("missing fixture footer");
  vi.spyOn(bar, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 762, 390, 82));
  // Execute the actual document-start compatibility code, not a copy of its logic.
  const bridge = runInThisContext(`(() => {${swift.slice(start, end)}\nreturn footer;})()`) as {
    reportSlot: (slot: { revision?: number; generation?: number }) => Promise<boolean>;
  };
  const update = (
    window as Window & {
      __heinzelUpdateFooter?: (width: number, bottom: number, revision: number, generation: number) => void;
    }
  ).__heinzelUpdateFooter;
  if (!update) throw new Error("missing native footer handshake");
  update(100, 34, 7, 3);
  await waitFor(() => expect(report).toHaveBeenCalled());
  expect(document.head.textContent).toContain("padding-left:100px!important");
  expect(document.head.textContent).toContain("padding-bottom:34px!important");
  expect(report).toHaveBeenLastCalledWith(
    expect.objectContaining({ x: 0, y: 762, width: 100, height: 48, revision: 7, generation: 3 }),
  );
  const slot = document.createElement("div");
  slot.setAttribute("data-native-footer-slot", "");
  bar.appendChild(slot);
  expect(await bridge.reportSlot({})).toBe(false);
  update(100, 34, 7, 4);
  await waitFor(() => expect(report).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 7, generation: 4 })));
  expect(document.head.textContent).toContain("padding-left:100px!important");

  act(() => slot.setAttribute("data-native-footer-version", "2"));
  await waitFor(() => expect(document.head.textContent).not.toContain("padding-left:100px!important"));
  report.mockClear();
  expect(await bridge.reportSlot({ revision: 7, generation: 3 })).toBe(false);
  expect(report).not.toHaveBeenCalled();
  expect(await bridge.reportSlot({ revision: 7, generation: 4 })).toBe(true);
  expect(report).toHaveBeenCalledExactlyOnceWith({ revision: 7, generation: 4 });
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
