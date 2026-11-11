import { beforeEach, expect, it, vi } from "vitest";
import { finishUpdate, loadInstalledCore, pendingUpdate, type UpdateTransport } from "./controller";

const before = { instance: "old", version: "0.1.0", restartable: true };
const after = { instance: "new", version: "0.2.0", restartable: true };
const frontend = {
  version: after.version,
  identity: "a".repeat(64),
  entrypoints: ["/assets/new.js"],
  files: { "/assets/new.js": "b".repeat(64) },
};

function setup(start = before) {
  let running = start;
  const order: string[] = [];
  const transport: UpdateTransport = {
    lifecycle: {
      read: vi.fn(async () => running),
      restart: vi.fn(async () => {
        order.push("restart");
        running = after;
        return { restarting: true, instance: before.instance };
      }),
      verify: vi.fn(async () => ({ ...running, matches: true, mismatched: [] })),
    },
    install: vi.fn(async () => {
      throw new Error("loading an installed core must not install");
    }),
    frontend: vi.fn(async () => frontend),
    assets: vi.fn(async (_frontend, _signal, loaded) => {
      order.push(loaded ? "loaded-assets" : "new-assets");
    }),
    clean: vi.fn(async () => {
      order.push("owned-cleanup");
    }),
    reload: vi.fn(() => {
      order.push("reload");
    }),
  };
  return { transport, order };
}

beforeEach(() => {
  sessionStorage.clear();
  document.body.innerHTML = "";
});

it("restarts once into the installed core, verifies it and its files, and proves the reloaded app", async () => {
  const { transport, order } = setup();
  await loadInstalledCore("0.2.0", new AbortController().signal, vi.fn(), transport);
  expect(order).toEqual(["restart", "new-assets", "owned-cleanup", "reload"]);
  expect(transport.install).not.toHaveBeenCalled();
  expect(transport.lifecycle.verify).toHaveBeenCalledWith(
    { instance: "new", version: "0.2.0", settings: [] },
    expect.any(AbortSignal),
  );
  expect(pendingUpdate()?.stage).toBe("reloading");
  expect(await finishUpdate(new AbortController().signal, transport)).toBe("Heinzel 0.2.0 is running.");
  expect(pendingUpdate()).toBeNull();
  expect(transport.lifecycle.restart).toHaveBeenCalledTimes(1);
});

it("does nothing when the installed core is already running", async () => {
  const { transport } = setup(after);
  expect(await loadInstalledCore("0.2.0", new AbortController().signal, vi.fn(), transport)).toBe(
    "Heinzel 0.2.0 is running.",
  );
  expect(transport.lifecycle.restart).not.toHaveBeenCalled();
  expect(pendingUpdate()).toBeNull();
});

it("names the supported action when this server cannot restart, and records nothing", async () => {
  const { transport } = setup({ ...before, restartable: false });
  await expect(loadInstalledCore("0.2.0", new AbortController().signal, vi.fn(), transport)).rejects.toThrow(
    "quit Heinzel and open it again",
  );
  expect(transport.lifecycle.restart).not.toHaveBeenCalled();
  expect(pendingUpdate()).toBeNull();
});

it("waits while open work is on screen instead of restarting under it", async () => {
  const { transport } = setup();
  document.body.innerHTML = '<div role="alertdialog"></div>';
  await expect(loadInstalledCore("0.2.0", new AbortController().signal, vi.fn(), transport)).rejects.toThrow(
    "Close the terminal or editor",
  );
  expect(transport.lifecycle.restart).not.toHaveBeenCalled();
  expect(pendingUpdate()).toBeNull();
});

it("never starts a second restart while a recorded one awaits verification", async () => {
  const { transport } = setup();
  sessionStorage.setItem("hz.update.receipt", JSON.stringify({ stage: "uncertain" }));
  await expect(loadInstalledCore("0.2.0", new AbortController().signal, vi.fn(), transport)).rejects.toThrow(
    "needs verification",
  );
  expect(transport.lifecycle.restart).not.toHaveBeenCalled();
});
