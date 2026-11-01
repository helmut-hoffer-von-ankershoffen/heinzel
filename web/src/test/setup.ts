import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { resetOperationRun } from "@/lib/operationRun";
import { splash } from "@/splash/store";

// The API is mocked and answers at once, so a findBy or waitFor only waits for this process's CPU. At Testing Library's
// default of 1 s a full-route render failed 4 of 6 runs at CPU stress; no test measures speed with it.
configure({ asyncUtilTimeout: 10_000 });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  localStorage.clear();
  resetOperationRun();
  splash.reset(); // the page's splash store, which the query cache, the theme and a restart report to
});

// jsdom has neither; React Flow and the theme ask for them.
class Observer {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= Observer as unknown as typeof ResizeObserver;
window.matchMedia ??= ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener() {},
  removeEventListener() {},
  addListener() {},
  removeListener() {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

// jsdom's <dialog> can't open as a modal; opening it is what the search palette needs from it.
HTMLDialogElement.prototype.showModal ??= function showModal(this: HTMLDialogElement) {
  this.open = true;
};
HTMLDialogElement.prototype.close ??= function close(this: HTMLDialogElement) {
  this.open = false;
  this.dispatchEvent(new Event("close"));
};
