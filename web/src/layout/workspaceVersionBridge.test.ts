import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInThisContext } from "node:vm";
import { expect, it, vi } from "vitest";

function versionRequest(clientIdentity = "") {
  const source = ["WorkspaceView", "NativePrompt", "TitleBarDragLayout", "Navigation"]
    .map((name) => readFileSync(resolve(process.cwd(), `../apple/App/${name}.swift`), "utf8"))
    .join("\n");
  const section = source.split("private func resolveWorkspaceVersion(in view: WKWebView)")[1];
  const body = section?.split('body: """')[1]?.split('"""')[0];
  if (!body) throw new Error("missing native version request");
  // WebKit exposes the arguments dictionary as bindings in the evaluated script.
  // Evaluate the actual inspection wrapper as well as its interpolated body so
  // authentication and origin checks follow the same path as the packaged app.
  expect(section).toContain('arguments: ["clientIdentity": identity]');
  const wrapper = source
    .split("private func inspectWorkspace(")[1]
    ?.split('callAsyncJavaScript("""')[1]
    ?.split('"""')[0];
  if (!wrapper?.includes("\\(body)")) throw new Error("missing native inspection wrapper");
  const evaluate =
    runInThisContext(`(async ({ origin, clientIdentity, fetch, location, sessionStorage, localStorage }) => {
    ${wrapper.replace("\\(body)", body)}
  })`) as (bindings: Record<string, unknown>) => Promise<Record<string, unknown> | null>;
  return (fetch: typeof globalThis.fetch, pageOrigin = "https://workspace.example") => {
    return evaluate({
      origin: "https://workspace.example/",
      clientIdentity,
      fetch,
      location: { origin: pageOrigin },
      sessionStorage: { getItem: () => "session-token" },
      localStorage: { getItem: () => null },
    });
  };
}

it("uses observed daemon metadata without turning it into a running version", async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      web: { version: "0.9.50" },
      installed: { version: "0.9.50", updated_at: null },
      daemon: { last_observed_version: "0.9.49", last_observed_at: "2026-11-16T18:00:00Z", observation_stale: true },
    }),
  });
  const result = await versionRequest()(fetch);
  expect(result).toMatchObject({
    version: "0.9.50",
    installedVersion: "0.9.50",
    installedUpdatedAt: null,
    daemonLastVersion: "0.9.49",
    daemonObservationStale: true,
  });
  expect(result?.daemonLastObservedAt).toBe(Date.parse("2026-11-16T18:00:00Z"));
  expect(result).not.toHaveProperty("runningVersion");
});

it.each(["", "Heinzel/0.9.57 test-machine"])("preserves the evaluated native identity binding %j", async (identity) => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ web: { version: "0.9.57" } }) });
  expect(await versionRequest(identity)(fetch)).toMatchObject({ version: "0.9.57" });
  expect(fetch).toHaveBeenCalledWith(
    "/api/workspace-version",
    expect.objectContaining({
      headers: { Authorization: "Bearer session-token", ...(identity ? { "X-Heinzel-Client": identity } : {}) },
      redirect: "error",
      mode: "same-origin",
      credentials: "omit",
      cache: "no-store",
    }),
  );
});

it("does not inspect a document outside the native endpoint origin", async () => {
  const fetch = vi.fn();
  expect(await versionRequest()(fetch, "https://other.example")).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it("falls back only when the older server does not provide the metadata route", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce({ ok: false, status: 404 })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ version: "0.9.49" }),
    });
  expect(await versionRequest()(fetch)).toEqual({ version: "0.9.49" });
  expect(fetch.mock.calls.map(([path]) => path)).toEqual(["/api/workspace-version", "/api/system/info"]);
  const denied = vi.fn().mockResolvedValue({ ok: false, status: 403 });
  expect(await versionRequest()(denied)).toBeNull();
  expect(denied).toHaveBeenCalledTimes(1);
});

it.each(["2026-02-31T18:00:00Z", "2026-11-16T18:00:00", "11/16/2026"])(
  "does not invent an observation time from malformed date %s",
  async (value) => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        web: { version: "0.9.50" },
        installed: { updated_at: value },
        daemon: { last_observed_at: value },
      }),
    });
    const result = await versionRequest()(fetch);
    expect(result?.installedUpdatedAt).toBeNull();
    expect(result?.daemonLastObservedAt).toBeNull();
  },
);
