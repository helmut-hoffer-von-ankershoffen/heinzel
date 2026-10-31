import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dropRefusedDevice, dropStaleSession } from "./device";
import { DEVICE_KEY, deviceToken, sessionToken } from "./token";

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

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

  it("drops a revoked device token only when no session token is in use", () => {
    localStorage.setItem(DEVICE_KEY, "hzd_device");
    dropRefusedDevice(401, "hzd_device", vi.fn());
    expect(deviceToken()).toBeNull();
  });
});
