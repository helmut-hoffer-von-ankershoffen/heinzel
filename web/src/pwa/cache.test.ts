import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it, vi } from "vitest";

it("activation removes only old hz shell caches and preserves unrelated application caches", async () => {
  const source = readFileSync(resolve("public/sw.js"), "utf8");
  const build = /const BUILD = "([^"]+)"/.exec(source)?.[1];
  const current = `hz-shell-${build}`;
  expect(source).toMatch(/^const SHELL = `hz-shell-\$\{BUILD\}`;$/m);
  type Activate = (event: { waitUntil: (pending: Promise<unknown>) => void }) => void;
  let activate: Activate | undefined;
  const worker = {
    addEventListener: (name: string, handler: Activate) => {
      if (name === "activate") activate = handler;
    },
    clients: { claim: async () => {} },
  };
  const cache = {
    keys: async () => [current, "hz-shell-v4", "hz-shell-old", "other-app-data", "hz-private-uploads"],
    delete: vi.fn(async () => true),
  };
  new Function("self", "caches", source)(worker, cache);
  const pending: Promise<unknown>[] = [];
  expect(activate).toBeDefined();
  activate?.({ waitUntil: (promise) => pending.push(promise) });
  await Promise.all(pending);
  // The constant name every earlier build used (hz-shell-v4) is another build's cache too.
  expect(cache.delete.mock.calls).toEqual([["hz-shell-v4"], ["hz-shell-old"]]);
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
  const cache = { open: async () => ({ put: vi.fn(), match }), match };
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
  // The cache is named after the build the web build stamps in (src/pwa/shellBuild.ts), never a constant.
  expect(source.split('const BUILD = "dev";')).toHaveLength(2);
  expect(source).not.toMatch(/const SHELL = "hz-shell-v\d+"/);
});

it("serves an /assets/ file from the network on a cache miss even when the cache refuses the copy", async () => {
  const source = readFileSync(resolve("public/sw.js"), "utf8");
  let onFetch: Handler | undefined;
  const self = {
    addEventListener: (name: string, handler: Handler) => {
      if (name === "fetch") onFetch = handler;
    },
    location: { origin: "http://localhost" },
  };
  const put = vi.fn(async () => {
    throw new Error("quota exceeded");
  });
  const cache = { open: async () => ({ put }), match: vi.fn(async () => undefined) };
  const response = { ok: true, type: "basic", clone: () => "copy" };
  new Function("self", "caches", "fetch", "Response", source)(self, cache, async () => response, {});
  const waiting: Promise<unknown>[] = [];
  let responded: Promise<unknown> | undefined;
  onFetch?.({
    request: { method: "GET", url: "http://localhost/assets/index-abc123.js", mode: "same-origin" },
    respondWith: (pending: Promise<unknown>) => {
      responded = pending;
    },
    waitUntil: (pending: Promise<unknown>) => waiting.push(pending),
  });
  expect(await responded).toBe(response);
  expect(waiting).toHaveLength(1);
  await expect(Promise.all(waiting)).resolves.toBeDefined();
  expect(put).toHaveBeenCalledWith(expect.anything(), "copy");
});

type Lifecycle = (event: { waitUntil: (pending: Promise<unknown>) => void }) => void;
function installer(open: (name: string) => Promise<unknown>) {
  const source = readFileSync(resolve("public/sw.js"), "utf8");
  const handlers: Record<string, Lifecycle> = {};
  const self = {
    addEventListener: (name: string, handler: Lifecycle) => {
      handlers[name] = handler;
    },
    location: { origin: "http://localhost" },
    skipWaiting: vi.fn(async () => undefined),
    clients: { claim: async () => undefined },
  };
  const info = vi.spyOn(console, "info").mockImplementation(() => undefined);
  const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
  new Function("self", "caches", source)(self, { open });
  const install = async () => {
    const pending: Promise<unknown>[] = [];
    handlers.install?.({ waitUntil: (promise) => pending.push(promise) });
    await Promise.all(pending);
  };
  const events = () =>
    [...info.mock.calls, ...error.mock.calls].map(([line]) => JSON.parse(String(line)) as Record<string, unknown>);
  return {
    install,
    events,
    self,
    restore: () => {
      info.mockRestore();
      error.mockRestore();
    },
  };
}

it("installs this build's shell past the HTTP cache under the build's own cache, and logs the step", async () => {
  const addAll = vi.fn(async (_requests: Request[]) => undefined);
  const open = vi.fn(async () => ({ addAll }));
  const { install, events, self, restore } = installer(open);
  try {
    await install();
    expect(open).toHaveBeenCalledWith("hz-shell-dev");
    const requests = addAll.mock.calls[0]?.[0] ?? [];
    expect(requests.map((request) => new URL(request.url).pathname)).toEqual([
      "/offline.html",
      "/manifest.webmanifest",
      "/favicon.svg",
      "/icons/icon-192.png",
      "/boot-retry.js",
    ]);
    // A browser's HTTP cache may still hold an older build's offline page: every file comes from hz itself.
    expect(requests.every((request) => request.cache === "reload")).toBe(true);
    expect(self.skipWaiting).toHaveBeenCalledTimes(1);
    const [start, end] = events();
    expect(start).toEqual({ event: "web.shell.install", version: "dev", operation: "web.shell", phase: "start" });
    expect(end).toMatchObject({ event: "web.shell.install", version: "dev", phase: "end", outcome: "ok", count: 5 });
    expect(typeof end?.duration_ms).toBe("number");
  } finally {
    restore();
  }
});

it("logs a failed install as an error outcome and leaves the last worker in charge", async () => {
  const open = vi.fn(async () => ({
    addAll: async () => {
      throw new TypeError("Failed to fetch");
    },
  }));
  const { install, events, self, restore } = installer(open);
  try {
    await expect(install()).rejects.toThrow("Failed to fetch");
    expect(self.skipWaiting).not.toHaveBeenCalled();
    expect(events().at(-1)).toMatchObject({
      event: "web.shell.install",
      phase: "end",
      outcome: "error",
      error_type: "TypeError",
      error: "Failed to fetch",
    });
  } finally {
    restore();
  }
});

it("answers a dead hz with this build's offline page only, never another build's cache, and logs it", async () => {
  const source = readFileSync(resolve("public/sw.js"), "utf8");
  let onFetch: Handler | undefined;
  const self = {
    addEventListener: (name: string, handler: Handler) => {
      if (name === "fetch") onFetch = handler;
    },
    location: { origin: "http://localhost" },
  };
  const own = vi.fn(async () => "this-builds-offline-page");
  const everywhere = vi.fn(async () => "an-older-builds-offline-page");
  const open = vi.fn(async () => ({ match: own }));
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  try {
    new Function("self", "caches", "fetch", "Response", source)(
      self,
      { open, match: everywhere },
      async () => {
        throw new TypeError("Load failed");
      },
      { error: () => "network-error" },
    );
    let responded: Promise<unknown> | undefined;
    onFetch?.({
      request: { method: "GET", url: "http://localhost/", mode: "navigate" },
      respondWith: (pending: Promise<unknown>) => {
        responded = pending;
      },
      waitUntil: () => undefined,
    });
    expect(await responded).toBe("this-builds-offline-page");
    expect(open).toHaveBeenCalledWith("hz-shell-dev");
    expect(everywhere).not.toHaveBeenCalled();
    expect(JSON.parse(String(warn.mock.calls[0]?.[0]))).toEqual({
      event: "web.shell.offline",
      version: "dev",
      operation: "web.shell",
      step: "navigate",
      outcome: "skipped",
      reason: "network",
      error_type: "TypeError",
    });
  } finally {
    warn.mockRestore();
  }
});
