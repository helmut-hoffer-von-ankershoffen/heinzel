import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { probeServer } from "@/splash/Splash";
import { api, authorityMiddleware, failedWorkspaceRead, unwrap } from "./client";
import { dropRefusedDevice, dropStaleSession } from "./device";
import { authorityEpoch, DEVICE_KEY, deviceToken, sessionToken } from "./token";

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("does not send a scoped decision if authority rotates while the request is queued", async () => {
  sessionStorage.setItem("hz.token", "original-token");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const decision = api.POST("/system/runner/chat/confirm", {
    params: { query: { session: "chat", action: "draft-share" } },
    middleware: authorityMiddleware(authorityEpoch()),
  });
  sessionStorage.setItem("hz.token", "replacement-token");
  await expect(decision).rejects.toThrow("Browser authority changed");
  expect(fetcher).not.toHaveBeenCalled();
});

describe("a window that outlived hz ui", () => {
  it("uses the device token once hz ui refuses the old session token", async () => {
    sessionStorage.setItem("hz.token", "old-session");
    localStorage.setItem(DEVICE_KEY, "hzd_device");
    const fetcher = vi.fn(async () => new Response("{}", { status: 401 }));
    await dropStaleSession(fetcher as unknown as typeof fetch);
    expect(sessionToken()).toBeNull();
    expect(deviceToken()).toBe("hzd_device");
  });

  it("keeps a session token hz ui still accepts, and asks nothing without a device token", async () => {
    sessionStorage.setItem("hz.token", "live-session");
    const fetcher = vi.fn(async () => new Response("{}", { status: 200 }));
    await dropStaleSession(fetcher as unknown as typeof fetch);
    expect(fetcher).not.toHaveBeenCalled();
    localStorage.setItem(DEVICE_KEY, "hzd_device");
    await dropStaleSession(fetcher as unknown as typeof fetch);
    expect(sessionToken()).toBe("live-session");
  });

  it("keeps the session token while hz isn't answering", async () => {
    sessionStorage.setItem("hz.token", "old-session");
    localStorage.setItem(DEVICE_KEY, "hzd_device");
    await dropStaleSession((() => Promise.reject(new TypeError("offline"))) as unknown as typeof fetch);
    expect(sessionToken()).toBe("old-session");
  });

  it("confirms a resource refusal with one lifecycle read and ignores a late refusal after a switch", async () => {
    sessionStorage.setItem("hz.token", "old-session");
    localStorage.setItem(DEVICE_KEY, "paired-device");
    const healthy = vi.fn(async () => new Response(null, { status: 200 }));
    await dropRefusedDevice(401, "old-session", healthy);
    expect(sessionToken()).toBe("old-session");
    let finish!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const check = vi.fn(async (_request: RequestInfo | URL) => pending);
    const first = dropRefusedDevice(401, "old-session", check);
    const second = dropRefusedDevice(401, "old-session", check);
    expect(check).toHaveBeenCalledOnce();
    const read = check.mock.calls[0]?.[0] as Request;
    expect([read.method, new URL(read.url).pathname, read.redirect]).toEqual(["GET", "/api/lifecycle", "error"]);
    sessionStorage.setItem("hz.token", "new-session");
    finish(new Response(null, { status: 401 }));
    await Promise.all([first, second]);
    expect(sessionToken()).toBe("new-session");
  });

  it.each([false, true])(
    "reloads only browser windows after a confirmed launch-token refusal (native: %s)",
    async (native) => {
      if (native) vi.stubGlobal("heinzelDesktop", { workspaces: { open: vi.fn() } });
      sessionStorage.setItem("hz.token", "old-session");
      localStorage.setItem(DEVICE_KEY, "paired-device");
      const fetcher = vi.fn(async () => new Response(null, { status: 401 }));
      const reload = vi.fn();

      await Promise.all([
        dropRefusedDevice(401, "old-session", fetcher, reload),
        dropRefusedDevice(401, "old-session", fetcher, reload),
      ]);

      expect(fetcher).toHaveBeenCalledOnce();
      expect(sessionToken()).toBeNull();
      expect(deviceToken()).toBe("paired-device");
      expect(reload).toHaveBeenCalledTimes(native ? 0 : 1);
    },
  );

  it("keeps a refused session without attempting recovery when no paired device exists", async () => {
    sessionStorage.setItem("hz.token", "old-session");
    const fetcher = vi.fn(async () => new Response(null, { status: 401 }));
    const reload = vi.fn();

    await dropRefusedDevice(401, "old-session", fetcher, reload);

    expect(fetcher).not.toHaveBeenCalled();
    expect(reload).not.toHaveBeenCalled();
    expect(sessionToken()).toBe("old-session");
  });

  it("never tells a paired window to sign in again while it moves to its device token", async () => {
    sessionStorage.setItem("hz.token", "old-session");
    localStorage.setItem(DEVICE_KEY, "hzd_device");
    const sent: (string | null)[] = [];
    vi.stubGlobal("fetch", async (request: Request) => {
      const bearer = request.headers.get("Authorization");
      sent.push(bearer);
      return bearer === "Bearer hzd_device"
        ? Response.json({ version: "0.9.1" })
        : Response.json({ error: "refused" }, { status: 401 });
    });
    expect((await probeServer()).kind).toBe("ok");
    expect(sent).toEqual(["Bearer old-session", "Bearer old-session", "Bearer hzd_device"]);
    // A refused device token, with nothing left to ask with, is the one that asks to sign in.
    sent.length = 0;
    expect((await probeServer()).kind).toBe("ok");
    localStorage.setItem(DEVICE_KEY, "hzd_revoked");
    expect((await probeServer()).kind).toBe("refused");
    expect(sent).toEqual(["Bearer hzd_device", "Bearer hzd_revoked", "Bearer hzd_revoked"]);
  });

  it("retries a Profile read once using its surviving pair and leaves an authority-pinned read with its caller", async () => {
    localStorage.setItem(DEVICE_KEY, "paired-device");
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (request: Request) => {
      const path = new URL(request.url).pathname;
      const isPaired = request.headers.get("Authorization") === "Bearer paired-device";
      calls.push(`${request.method} ${path} ${isPaired ? "paired" : "session"}`);
      return isPaired ? Response.json({ handle: "studio" }) : new Response(null, { status: 401 });
    });
    sessionStorage.setItem("hz.token", "first-session");
    expect((await api.GET("/org/show")).response.ok).toBe(true);
    expect(calls).toEqual(["GET /api/org/show session", "GET /api/lifecycle session", "GET /api/org/show paired"]);
    calls.length = 0;
    sessionStorage.setItem("hz.token", "second-session");
    const pinned = api.GET("/org/show", { middleware: authorityMiddleware(authorityEpoch()) });
    const error = await unwrap(pinned).catch((failure: unknown) => failure);
    expect(error).toMatchObject({ status: 401 });
    expect(failedWorkspaceRead(error)).toBe(false);
    expect(calls).toEqual(["GET /api/org/show session", "GET /api/lifecycle session"]);
  });

  it("allows restoration of ordinary failed reads and excludes refused writes", async () => {
    vi.stubGlobal("fetch", async () => Response.json({ error: "unavailable" }, { status: 503 }));
    expect(failedWorkspaceRead(await unwrap(api.GET("/org/show")).catch((failure: unknown) => failure))).toBe(true);
    const write = api.POST("/system/runner/chat/confirm", {
      params: { query: { session: "test", action: "draft-share" } },
    });
    expect(failedWorkspaceRead(await unwrap(write).catch((failure: unknown) => failure))).toBe(false);
  });

  it("drops a revoked device token only when no session token is in use", async () => {
    localStorage.setItem(DEVICE_KEY, "hzd_device");
    await dropRefusedDevice(401, "hzd_device", async () => new Response(null, { status: 401 }));
    expect(deviceToken()).toBeNull();
  });
});
