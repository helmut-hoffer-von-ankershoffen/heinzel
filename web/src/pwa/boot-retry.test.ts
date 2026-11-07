import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeAll, describe, expect, it, vi } from "vitest";

type Api = {
  delayFor: (attempt: number) => number;
  statusFor: (attempt: number, delay: number) => string;
  probe: (fetcher: unknown, now?: () => number) => Promise<boolean>;
  mayReload: (storage: Pick<Storage, "getItem" | "setItem">, now?: number) => boolean;
  retry: (options: Record<string, unknown>) => { stop: () => void; now: () => void };
};
let api: Api;

beforeAll(async () => {
  // A classic script: run it as the browser would, in this page.
  new Function(readFileSync(resolve("public/boot-retry.js"), "utf8"))();
  api = (globalThis as unknown as { hzBootRetry: Api }).hzBootRetry;
});

describe("boot-retry", () => {
  it("backs off exponentially to a bound", () => {
    expect([1, 2, 3, 4, 5, 6, 7, 50].map(api.delayFor)).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);
  });

  it("says what is happening in words that carry no version", () => {
    expect(api.statusFor(2, 2000)).toBe("hz isn't answering. Trying again in 2 seconds (try 2).");
    expect(api.statusFor(1, 1000)).toContain("1 second ");
    expect(api.statusFor(3, 4000)).not.toMatch(/v\d|0\.\d+\.\d+/);
  });

  it("counts only a manifest as hz answering, not the app's page or an error", async () => {
    const answer = (ok: boolean, type: string) => async () => ({
      ok,
      headers: { get: () => type },
    });
    expect(await api.probe(answer(true, "application/manifest+json"))).toBe(true);
    expect(await api.probe(answer(true, "text/html"))).toBe(false);
    expect(await api.probe(answer(false, "application/manifest+json"))).toBe(false);
    expect(
      await api.probe(async () => {
        throw new TypeError("Failed to fetch");
      }),
    ).toBe(false);
  });

  it("asks past the service worker with a fresh, uncached request", async () => {
    const fetcher = vi.fn(async () => ({ ok: true, headers: { get: () => "application/manifest+json" } }));
    await api.probe(fetcher, () => 42);
    expect(fetcher).toHaveBeenCalledWith(
      "/manifest.webmanifest?hz-probe=42",
      expect.objectContaining({ cache: "no-store", credentials: "omit" }),
    );
  });

  it("retries with growing waits while hz is down and reloads the moment it answers", async () => {
    const answers = [false, false, false, true];
    const waits: number[] = [];
    const said: string[] = [];
    const reload = vi.fn();
    let pending: (() => Promise<void>) | undefined;
    api.retry({
      probe: async () => answers.shift(),
      say: (text: string) => said.push(text),
      reload,
      schedule: (step: () => Promise<void>, delay: number) => {
        pending = step;
        waits.push(delay);
        return 1;
      },
      cancel: () => undefined,
    });
    for (let i = 0; i < 4; i++) {
      await vi.waitFor(() => expect(pending ?? reload.mock.calls.length).toBeTruthy());
      const step = pending;
      pending = undefined;
      if (step) await step();
    }
    expect(waits).toEqual([1000, 2000, 4000]);
    expect(said.at(-1)).toBe("hz answered. Opening…");
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("stops retrying when told to", async () => {
    const ask = vi.fn(async () => false);
    let step: (() => Promise<void>) | undefined;
    const running = api.retry({
      probe: ask,
      say: () => undefined,
      reload: () => undefined,
      schedule: (next: () => Promise<void>) => {
        step = next;
        return 1;
      },
      cancel: () => undefined,
    });
    await vi.waitFor(() => expect(step).toBeDefined());
    running.stop();
    await step?.();
    expect(ask).toHaveBeenCalledTimes(1);
  });

  it("allows only a few reloads in a row so a page that stays broken cannot loop", () => {
    const kept = new Map<string, string>();
    const storage = {
      getItem: (k: string) => kept.get(k) ?? null,
      setItem: (k: string, v: string) => void kept.set(k, v),
    };
    const now = 1_000_000;
    expect([0, 1, 2, 3].map((i) => api.mayReload(storage, now + i))).toEqual([true, true, true, false]);
    expect(api.mayReload(storage, now + 10 * 60_000)).toBe(true);
  });

  it("ships an offline page with an accessible status line, no version and the current command", () => {
    const page = readFileSync(resolve("public/offline.html"), "utf8");
    expect(page).toMatch(/id="boot-status" role="status" aria-live="polite"/);
    expect(page).toContain("hz ui serve");
    expect(page).not.toMatch(/v?\d+\.\d+\.\d+/);
    expect(page).not.toMatch(/<script>[^<]/); // no inline script: the CSP admits only the splash's, by hash
    expect(page).toContain('src="/boot-retry.js"');
  });
});
