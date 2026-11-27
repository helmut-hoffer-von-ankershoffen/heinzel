import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";

/** Exercise the same authority prelude and metadata read that the native host sends to WebKit. */
function versionRequest(
  fetch: typeof globalThis.fetch,
  clientIdentity = "",
  pageOrigin = "https://studio.example/",
): Promise<Record<string, unknown> | null> {
  const source = readFileSync(resolve(process.cwd(), "../apple/App/WorkspaceDocumentFacts.swift"), "utf8");
  const authority = source.match(/let authorityScript = """\n([\s\S]*?)\n\s*"""/)?.[1];
  const body = source.match(/callAsyncJavaScript\(authorityScript \+ """\s*\\n([\s\S]*?)\n\s*""", arguments:/)?.[1];
  if (!authority || !body) throw new Error("Missing native workspace authority or version request");
  const location = new URL(pageOrigin);
  return runInNewContext(`(async () => { ${authority}\n${body} })()`, {
    location,
    origin: "https://studio.example/",
    expected: "",
    clientIdentity,
    fetch,
    URL,
    URLSearchParams,
    TextEncoder,
    AbortSignal,
    crypto: webcrypto,
    sessionStorage: { getItem: (key: string) => (key === "hz.token" ? "test-session" : null) },
    localStorage: { getItem: () => null },
  }) as Promise<Record<string, unknown> | null>;
}

it("uses observed daemon metadata without turning it into a running version", async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      web: { version: "0.9.50", backend_ip: "192.0.2.58" },
      installed: { version: "0.9.50", updated_at: null },
      daemon: { last_observed_version: "0.9.49", last_observed_at: "2026-11-16T18:00:00Z", observation_stale: true },
    }),
  });
  const result = await versionRequest(fetch);
  expect(result).toMatchObject({
    version: "0.9.50",
    backendIP: "192.0.2.58",
    installedVersion: "0.9.50",
    installedUpdatedAt: null,
    daemonLastVersion: "0.9.49",
    daemonObservationStale: true,
  });
  expect(result?.daemonLastObservedAt).toBe(Date.parse("2026-11-16T18:00:00Z"));
  expect(result).not.toHaveProperty("runningVersion");
});

it.each(["", "flavor=native;app=0.9.58;backend=0.9.58;machine=Test"])(
  "preserves the evaluated native identity binding %j",
  async (identity) => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ web: { version: "0.9.57" } }) });
    const navigation = readFileSync(resolve(process.cwd(), "../apple/App/Navigation.swift"), "utf8");
    expect(navigation).toContain(
      "WorkspaceDocumentFacts.version(in: view, origin: endpoint.origin, clientIdentity: identity)",
    );
    expect(await versionRequest(fetch, identity)).toMatchObject({ version: "0.9.57" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/workspace-version",
      expect.objectContaining({
        headers: { Authorization: "Bearer test-session", ...(identity ? { "X-Heinzel-Client": identity } : {}) },
        redirect: "error",
        mode: "same-origin",
        credentials: "omit",
        cache: "no-store",
      }),
    );
  },
);

it("does not inspect a document outside the native endpoint origin", async () => {
  const fetch = vi.fn();
  expect(await versionRequest(fetch, "", "https://other.example/")).toBeNull();
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
  const identity = "flavor=native;app=0.9.58;backend=0.9.58;machine=Test";
  expect(await versionRequest(fetch, identity)).toEqual({ version: "0.9.49" });
  expect(fetch.mock.calls).toEqual([
    [
      "/api/workspace-version",
      expect.objectContaining({ headers: { Authorization: "Bearer test-session", "X-Heinzel-Client": identity } }),
    ],
    ["/api/system/info", expect.objectContaining({ headers: { Authorization: "Bearer test-session" } })],
  ]);
  const denied = vi.fn().mockResolvedValue({ ok: false, status: 403 });
  expect(await versionRequest(denied)).toBeNull();
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
    const result = await versionRequest(fetch);
    expect(result?.installedUpdatedAt).toBeNull();
    expect(result?.daemonLastObservedAt).toBeNull();
  },
);
