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

it("keeps a copy of a served page in the cache without letting a failing cache fail the response", async () => {
  const source = readFileSync(resolve("public/sw.js"), "utf8");
  type Fetch = (event: Record<string, unknown>) => void;
  let onFetch: Fetch | undefined;
  const worker = {
    addEventListener: (name: string, handler: Fetch) => {
      if (name === "fetch") onFetch = handler;
    },
    location: { origin: "http://localhost" },
  };
  const put = vi.fn(async () => {
    throw new Error("quota exceeded");
  });
  const cache = { open: async () => ({ put }), match: vi.fn() };
  const response = { ok: true, type: "basic", clone: () => "copy" };
  new Function("self", "caches", "fetch", source)(worker, cache, async () => response);
  const waiting: Promise<unknown>[] = [];
  let responded: Promise<unknown> | undefined;
  const request = { method: "GET", url: "http://localhost/", mode: "navigate" };
  onFetch?.({
    request,
    respondWith: (pending: Promise<unknown>) => {
      responded = pending;
    },
    waitUntil: (pending: Promise<unknown>) => waiting.push(pending),
  });
  expect(await responded).toBe(response);
  await expect(Promise.all(waiting)).resolves.toBeDefined();
  expect(waiting).toHaveLength(1);
  expect(put).toHaveBeenCalledWith("/", "copy");
});
