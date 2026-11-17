import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInThisContext } from "node:vm";
import { act, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

it("reserves space on an older workspace and yields when a new workspace supplies its own slot", async () => {
  const swift = readFileSync(resolve(process.cwd(), "../apple/App/WorkspaceView.swift"), "utf8");
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
  runInThisContext(`(() => {${swift.slice(start, end)}\n})()`);
  const update = (
    window as Window & { __heinzelUpdateFooter?: (width: number, bottom: number, revision: number) => void }
  ).__heinzelUpdateFooter;
  if (!update) throw new Error("missing native footer handshake");
  update(100, 34, 7);
  await waitFor(() => expect(report).toHaveBeenCalled());
  expect(document.head.textContent).toContain("padding-left:100px!important");
  expect(document.head.textContent).toContain("padding-bottom:34px!important");
  expect(report).toHaveBeenLastCalledWith(
    expect.objectContaining({ x: 0, y: 762, width: 100, height: 48, revision: 7 }),
  );
  act(() => {
    const slot = document.createElement("div");
    slot.setAttribute("data-native-footer-slot", "");
    bar.appendChild(slot);
  });
  await waitFor(() => expect(document.head.textContent).not.toContain("padding-left:100px!important"));
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
