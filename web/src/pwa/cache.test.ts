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
