import { afterEach, describe, expect, it, vi } from "vitest";
import { deviceName, dropRefusedDevice, forgetDevice, pairDevice } from "@/api/device";
import { DEVICE_KEY, deviceToken, token } from "@/api/token";
import { captureInstallPrompt, install, installState } from "./install";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("pairing", () => {
  it("asks for a device token once, with the session token, and keeps it", async () => {
    sessionStorage.setItem("hz.token", "session");
    const requests: Request[] = [];
    const fetcher = vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json({ id: "ab12", name: "Chrome on macOS", token: "hzd_device" });
    });
    expect(await pairDevice(fetcher as unknown as typeof fetch)).toBe(true);
    expect(deviceToken()).toBe("hzd_device");
    expect(requests[0]?.headers.get("Authorization")).toBe("Bearer session");
    expect(new URL(requests[0]?.url ?? "").pathname).toBe("/api/_web/device");
    expect(await pairDevice(fetcher as unknown as typeof fetch)).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1); // paired already
    expect(token()).toBe("session"); // the session token comes first
    sessionStorage.clear();
    expect(token()).toBe("hzd_device"); // reopened from the Dock
  });

  it("doesn't pair without a session token, or when the server refuses", async () => {
    const fetcher = vi.fn(async () => Response.json({ error: "no" }, { status: 403 }));
    expect(await pairDevice(fetcher as unknown as typeof fetch)).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
    sessionStorage.setItem("hz.token", "session");
    expect(await pairDevice(fetcher as unknown as typeof fetch)).toBe(false);
    expect(deviceToken()).toBeNull();
  });

  it("drops a device token the server refused, and forgets it on request", async () => {
    localStorage.setItem(DEVICE_KEY, "hzd_old");
    dropRefusedDevice(200, "hzd_old");
    expect(deviceToken()).toBe("hzd_old");
    dropRefusedDevice(401, "hzd_old");
    expect(deviceToken()).toBeNull();
    localStorage.setItem(DEVICE_KEY, "hzd_mine");
    const fetcher = vi.fn(async () => Response.json({ revoked: ["ab12"] }));
    await forgetDevice(fetcher as unknown as typeof fetch);
    expect(deviceToken()).toBeNull();
  });

  it("names the device by browser and system only", () => {
    const mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)";
    expect(deviceName(`${mac} Chrome/141.0 Safari/537.36`)).toBe("Chrome on macOS");
    expect(deviceName(`${mac} Version/19.0 Safari/605.1.15`)).toBe("Safari on macOS");
    expect(deviceName("Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/145.0")).toBe("Firefox on Linux");
  });
});

describe("installing", () => {
  it("keeps the browser's prompt and shows it when asked", async () => {
    const target = new EventTarget() as unknown as Window;
    captureInstallPrompt(target);
    const prompt = vi.fn(async () => {});
    const event = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), {
      prompt,
      userChoice: Promise.resolve({ outcome: "accepted" }),
    });
    target.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(installState()).toBe("prompt");
    expect(await install()).toBe(true);
    expect(prompt).toHaveBeenCalled();
    expect(installState()).not.toBe("prompt");
  });

  it("tells Safari's menu item", () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/19.0 Safari/605.1.15",
    );
    expect(installState()).toBe("safari");
  });
});
