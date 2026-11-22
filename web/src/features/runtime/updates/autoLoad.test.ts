import { beforeEach, expect, it, vi } from "vitest";
import { autoLoadInstalledCore, autoLoadTried } from "./autoLoad";
import type { UpdateStatus } from "./controller";
import { registerReloadHold } from "./work";

const status: UpdateStatus = {
  current: "0.1.0",
  latest: "0.2.0",
  installed_at: null,
  available: true,
  supported: true,
  reason: null,
  installed: "0.2.0",
  idle: true,
};
const granted = { request: vi.fn(async (_name, _options, run) => run({ name: "x", mode: "exclusive" })) };
const taken = { request: vi.fn(async (_name, _options, run) => run(null)) };

beforeEach(() => {
  localStorage.clear();
  document.body.innerHTML = "";
});

it("loads once per version across windows and remembers the attempt before restarting", async () => {
  const load = vi.fn(async () => {
    expect(autoLoadTried("0.2.0")).toBe(true);
  });
  expect(await autoLoadInstalledCore("0.2.0", load, async () => status, granted as never)).toBe("loaded");
  // Another window, or this one after a failure, does not restart again on its own.
  expect(await autoLoadInstalledCore("0.2.0", load, async () => status, granted as never)).toBe("tried");
  expect(load).toHaveBeenCalledTimes(1);
  expect(autoLoadTried("0.3.0")).toBe(false);
});

it("waits while the server is busy with a chat answer, an install or a daemon job", async () => {
  const load = vi.fn();
  const busy = { ...status, idle: false };
  expect(await autoLoadInstalledCore("0.2.0", load, async () => busy, granted as never)).toBe("held");
  expect(load).not.toHaveBeenCalled();
  expect(autoLoadTried("0.2.0")).toBe(false);
});

it("waits while work is open in this window", async () => {
  const load = vi.fn();
  const read = vi.fn(async () => status);
  document.body.innerHTML = '<div class="xterm"></div>';
  expect(await autoLoadInstalledCore("0.2.0", load, read, granted as never)).toBe("held");
  document.body.innerHTML = "";
  const release = registerReloadHold(() => true);
  try {
    expect(await autoLoadInstalledCore("0.2.0", load, read, granted as never)).toBe("held");
  } finally {
    release();
  }
  expect(read).not.toHaveBeenCalled();
  expect(load).not.toHaveBeenCalled();
});

it("leaves it to the window that holds the lock", async () => {
  const load = vi.fn();
  expect(await autoLoadInstalledCore("0.2.0", load, async () => status, taken as never)).toBe("elsewhere");
  expect(load).not.toHaveBeenCalled();
  expect(autoLoadTried("0.2.0")).toBe(false);
});

it("stops when the server already runs the installed core", async () => {
  const load = vi.fn();
  const current = { ...status, installed: null };
  expect(await autoLoadInstalledCore("0.2.0", load, async () => current, granted as never)).toBe("gone");
  expect(load).not.toHaveBeenCalled();
});

it("remembers the attempt in this window when local storage is unavailable", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("denied", "SecurityError");
  });
  try {
    const load = vi.fn(async () => {});
    expect(await autoLoadInstalledCore("0.4.0", load, async () => ({ ...status, installed: "0.4.0" }))).toBe("loaded");
    expect(await autoLoadInstalledCore("0.4.0", load, async () => ({ ...status, installed: "0.4.0" }))).toBe("tried");
    expect(load).toHaveBeenCalledTimes(1);
  } finally {
    setItem.mockRestore();
  }
});
