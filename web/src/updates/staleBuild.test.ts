import { afterEach, describe, expect, it, vi } from "vitest";
import {
  autoReloadAllowed,
  countAutoReload,
  forgetReload,
  isStale,
  prepareReload,
  RELOAD_WINDOW_MS,
  reloadBlocker,
  reloadedFor,
  servedEntrypoints,
  takeReloadNotice,
} from "./staleBuild";
import { registerReloadHold, registerRunningWork, registerUpdatePreparation } from "./work";

const OLD = ["/assets/index-old.css", "/assets/index-old.js"];
const NEW = ["/assets/index-new.css", "/assets/index-new.js"];
const page = (paths: string[]) =>
  `<!doctype html><html><head>${paths
    .map((path) =>
      path.endsWith(".css") ? `<link rel="stylesheet" href="${path}">` : `<script src="${path}"></script>`,
    )
    .join("")}</head></html>`;
const html = (body: string, status = 200, type = "text/html; charset=utf-8") =>
  new Response(body, { status, headers: { "Content-Type": type } });

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("the served web build", () => {
  it("is read from the served page, never a cached copy", async () => {
    const fetch = vi.fn(async (_request: Request) => html(page(NEW)));
    vi.stubGlobal("fetch", fetch);
    expect(await servedEntrypoints()).toEqual(NEW);
    const [request] = fetch.mock.calls[0] ?? [];
    expect(new URL(request?.url ?? "").pathname).toBe("/");
    expect(request?.cache).toBe("no-store");
  });

  it.each([
    ["hz is away", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["an error page", async () => html(page(NEW), 503)],
    ["not a page", async () => html("{}", 200, "application/json")],
    ["a page without a build (a dev server)", async () => html("<p>dev</p>")],
    ["an oversized page", async () => html(`${page(NEW)}${" ".repeat(1024 * 1024)}`)],
  ])("is unknown when %s", async (_case, answer) => {
    vi.stubGlobal("fetch", vi.fn(answer));
    expect(await servedEntrypoints()).toBeNull();
  });

  it("is stale only against a page with a build of its own", () => {
    expect(isStale(NEW, OLD)).toBe(true);
    expect(isStale(OLD, OLD)).toBe(false);
    expect(isStale(null, OLD)).toBe(false);
    expect(isStale([], OLD)).toBe(false);
    expect(isStale(NEW, [])).toBe(false);
  });
});

describe("a safe reload", () => {
  it("waits for a running turn even in the background, for open work, and for unsent text only in front", () => {
    let running = true;
    let text = true;
    const releases = [registerRunningWork(() => running), registerReloadHold(() => text)];
    try {
      expect(reloadBlocker(false)).toBe("turn");
      running = false;
      document.body.innerHTML = '<div role="dialog"></div>';
      expect(reloadBlocker(true)).toBe("screen");
      document.body.innerHTML = "";
      expect(reloadBlocker(true)).toBe("draft");
      expect(reloadBlocker(false)).toBeNull(); // in the background the text is saved and restored instead
      text = false;
      expect(reloadBlocker(true)).toBeNull();
    } finally {
      for (const release of releases) release();
    }
  });

  it("saves the unsent text, remembers the build it reloads for, and leaves one notice", () => {
    const prepare = vi.fn();
    const release = registerUpdatePreparation(prepare);
    try {
      expect(prepareReload(NEW)).toBe(true);
      expect(prepare).toHaveBeenCalledOnce();
      expect(reloadedFor(NEW)).toBe(true);
      expect(reloadedFor(OLD)).toBe(false);
      expect(takeReloadNotice()).toBe(true);
      expect(takeReloadNotice()).toBe(false); // shown once
      forgetReload();
      expect(reloadedFor(NEW)).toBe(false);
    } finally {
      release();
    }
  });

  it("keeps the page when the work can't be saved", () => {
    const release = registerUpdatePreparation(() => {
      throw new Error("The chat draft could not be preserved.");
    });
    try {
      expect(prepareReload(NEW)).toBe(false);
      expect(reloadedFor(NEW)).toBe(false);
      expect(takeReloadNotice()).toBe(false);
    } finally {
      release();
    }
  });

  it("remembers no build for a reload a person asked for", () => {
    expect(prepareReload(null)).toBe(true);
    expect(sessionStorage.getItem("hz.update.staleBuild")).toBeNull();
    expect(takeReloadNotice()).toBe(true);
  });

  it("reloads a tab by itself at most twice in ten minutes, so a flapping server can't loop it", () => {
    const start = 1_000_000;
    expect(autoReloadAllowed(start)).toBe(true);
    countAutoReload(start);
    expect(autoReloadAllowed(start + 1000)).toBe(true);
    countAutoReload(start + 1000);
    expect(autoReloadAllowed(start + 2000)).toBe(false);
    expect(autoReloadAllowed(start + RELOAD_WINDOW_MS + 1000)).toBe(true); // the first one has aged out
    sessionStorage.setItem("hz.update.autoReloads", "not json");
    expect(autoReloadAllowed(start)).toBe(true);
    sessionStorage.setItem("hz.update.autoReloads", JSON.stringify({ at: start }));
    expect(autoReloadAllowed(start)).toBe(true);
  });
});
