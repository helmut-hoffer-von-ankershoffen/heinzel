import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { probeServer } from "@/splash/Splash";
import { api, authorityMiddleware } from "./client";
import { dropRefusedDevice, dropStaleSession } from "./device";
import { authorityEpoch, DEVICE_KEY, deviceToken, sessionToken } from "./token";

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

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

  it("drops a refused session token and reloads on a paired browser; never loops without a device token", () => {
    const reload = vi.fn();
    sessionStorage.setItem("hz.token", "old-session");
    dropRefusedDevice(401, "old-session", reload);
    expect(reload).not.toHaveBeenCalled();
    expect(sessionToken()).toBe("old-session");
    localStorage.setItem(DEVICE_KEY, "hzd_device");
    dropRefusedDevice(401, "old-session", reload);
    expect(reload).toHaveBeenCalledOnce();
    expect(sessionToken()).toBeNull();
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
    expect(sent).toEqual(["Bearer old-session", "Bearer hzd_device"]);
    // A refused device token, with nothing left to ask with, is the one that asks to sign in.
    sent.length = 0;
    expect((await probeServer()).kind).toBe("ok");
    localStorage.setItem(DEVICE_KEY, "hzd_revoked");
    expect((await probeServer()).kind).toBe("refused");
    expect(sent).toEqual(["Bearer hzd_device", "Bearer hzd_revoked"]);
  });

  it("drops a revoked device token only when no session token is in use", () => {
    localStorage.setItem(DEVICE_KEY, "hzd_device");
    dropRefusedDevice(401, "hzd_device", vi.fn());
    expect(deviceToken()).toBeNull();
  });
});
