import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it, vi } from "vitest";

it("activation removes only old hz shell caches and preserves unrelated application caches", async () => {
  const source = readFileSync(resolve("public/sw.js"), "utf8");
  const current = /const SHELL = "([^"]+)"/.exec(source)?.[1];
  expect(current).toMatch(/^hz-shell-/);
  type Activate = (event: { waitUntil: (pending: Promise<unknown>) => void }) => void;
  let activate: Activate | undefined;
  const worker = {
    addEventListener: (name: string, handler: Activate) => {
      if (name === "activate") activate = handler;
    },
    clients: { claim: async () => {} },
  };
  const cache = {
    keys: async () => [current, "hz-shell-old", "other-app-data", "hz-private-uploads"],
    delete: vi.fn(async () => true),
  };
  new Function("self", "caches", source)(worker, cache);
  const pending: Promise<unknown>[] = [];
  expect(activate).toBeDefined();
  activate?.({ waitUntil: (promise) => pending.push(promise) });
  await Promise.all(pending);
  expect(cache.delete.mock.calls).toEqual([["hz-shell-old"]]);
});

type Handler = (event: Record<string, unknown>) => void;
function worker(match: (...args: unknown[]) => unknown, fetcher: () => Promise<unknown>) {
  const source = readFileSync(resolve("public/sw.js"), "utf8");
  let onFetch: Handler | undefined;
  const self = {
    addEventListener: (name: string, handler: Handler) => {
      if (name === "fetch") onFetch = handler;
    },
    location: { origin: "http://localhost" },
  };
  const cache = { open: async () => ({ put: vi.fn() }), match };
  new Function("self", "caches", "fetch", "Response", source)(self, cache, fetcher, { error: () => "network-error" });
  return (url: string, mode = "same-origin") => {
    let responded: Promise<unknown> | undefined;
    onFetch?.({
      request: { method: "GET", url, mode },
      respondWith: (pending: Promise<unknown>) => {
        responded = pending;
      },
      waitUntil: () => undefined,
    });
    return responded;
  };
}

it("serves the page from the network, and the offline page (never an old app page) when hz is down", async () => {
  const match = vi.fn(async (key: unknown) => (key === "/offline.html" ? "offline-page" : "old-app-page"));
  const down = worker(match, async () => {
    throw new TypeError("Failed to fetch");
  });
  expect(await down("http://localhost/", "navigate")).toBe("offline-page");
  expect(match).toHaveBeenCalledWith("/offline.html");
  const page = { ok: true };
  const up = worker(match, async () => page);
  expect(await up("http://localhost/", "navigate")).toBe(page);
});

it("finds the cached icon whatever version query the page asks with, and never answers the readiness probe itself", async () => {
  const match = vi.fn(async () => "cached-icon");
  const down = worker(match, async () => {
    throw new TypeError("Failed to fetch");
  });
  expect(await down("http://localhost/favicon.svg?v=bbe58f3f9911")).toBe("cached-icon");
  expect(match).toHaveBeenCalledWith(expect.anything(), { ignoreSearch: true });
  // The probe is left to the network: no respondWith, so a cached manifest can never make a dead hz look alive.
  expect(down("http://localhost/manifest.webmanifest?hz-probe=1")).toBeUndefined();
});

it("precaches the offline page and the retry script, not the app's page", () => {
  const source = readFileSync(resolve("public/sw.js"), "utf8");
  expect(source).toContain('const OFFLINE = "/offline.html"');
  expect(source).toContain('"/boot-retry.js"');
  expect(source).not.toMatch(/addAll\(\["\/"/);
  expect(/const SHELL = "hz-shell-v(\d+)"/.exec(source)?.[1]).toBeDefined();
});
