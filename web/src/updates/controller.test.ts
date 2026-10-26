import { beforeEach, expect, it, vi } from "vitest";
import { finishUpdate, pendingUpdate, startUpdate, type UpdateTransport } from "./controller";
import { registerUpdatePreparation } from "./work";

const before = { instance: "old", version: "0.1.0", restartable: true };
const after = { instance: "new", version: "0.2.0", restartable: true };
const frontend = {
  version: after.version,
  identity: "a".repeat(64),
  entrypoints: ["/assets/new.js"],
  files: { "/assets/new.js": "b".repeat(64) },
};
const result = {
  before: before.version,
  after: after.version,
  latest: after.version,
  pinned_to: "v0.2.0",
  updated: true,
  message: "Core and plugins updated.",
  next: [],
  plugins: [],
};

function setup() {
  let running = before;
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
      order.push("install");
      return result;
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

beforeEach(() => sessionStorage.clear());

it("installs once, verifies replacement and assets, then proves the reloaded app before success", async () => {
  const { transport, order } = setup();
  const unregister = registerUpdatePreparation(() => order.push("preserve"));
  try {
    await startUpdate(new AbortController().signal, vi.fn(), transport);
    expect(order).toEqual([
      "preserve",
      "install",
      "preserve",
      "restart",
      "new-assets",
      "owned-cleanup",
      "preserve",
      "reload",
    ]);
    expect(pendingUpdate()?.stage).toBe("reloading");
    expect(await finishUpdate(new AbortController().signal, transport)).toContain("Updated to hz 0.2.0");
    expect(order.at(-1)).toBe("loaded-assets");
    expect(pendingUpdate()).toBeNull();
    expect(transport.install).toHaveBeenCalledTimes(1);
    expect(transport.lifecycle.restart).toHaveBeenCalledTimes(1);
  } finally {
    unregister();
  }
});

it("preserves a lost-install acknowledgement and never repeats installation during recovery", async () => {
  const { transport } = setup();
  transport.install = vi.fn(async () => {
    throw new TypeError("lost acknowledgement");
  });
  await expect(startUpdate(new AbortController().signal, vi.fn(), transport)).rejects.toThrow("lost acknowledgement");
  expect(pendingUpdate()?.stage).toBe("uncertain");
  await expect(finishUpdate(new AbortController().signal, transport)).rejects.toThrow(/needs inspection/);
  await expect(startUpdate(new AbortController().signal, vi.fn(), transport)).rejects.toThrow(/will not be repeated/);
  expect(transport.install).toHaveBeenCalledTimes(1);
  expect(transport.reload).not.toHaveBeenCalled();
});

it("refuses before mutation when user work cannot be preserved", async () => {
  const { transport } = setup();
  const unregister = registerUpdatePreparation(() => {
    throw new Error("Draft unavailable");
  });
  try {
    await expect(startUpdate(new AbortController().signal, vi.fn(), transport)).rejects.toThrow("Draft unavailable");
    expect(transport.install).not.toHaveBeenCalled();
  } finally {
    unregister();
  }
});

it("does not claim success or clear recovery if loaded assets fail after reload", async () => {
  const { transport } = setup();
  await startUpdate(new AbortController().signal, vi.fn(), transport);
  transport.assets = vi.fn(async () => {
    throw new Error("stale shell");
  });
  await expect(finishUpdate(new AbortController().signal, transport)).rejects.toThrow("stale shell");
  expect(pendingUpdate()?.stage).toBe("reloading");
  expect(transport.install).toHaveBeenCalledTimes(1);
});

it("keeps a deliberate pin unchanged and does not restart or reload an unchanged result", async () => {
  const { transport } = setup();
  transport.install = vi.fn(async () => ({
    ...result,
    updated: false,
    after: before.version,
    message: "Pinned to a commit.",
  }));
  expect(await startUpdate(new AbortController().signal, vi.fn(), transport)).toBe("Pinned to a commit.");
  expect(transport.lifecycle.restart).not.toHaveBeenCalled();
  expect(transport.reload).not.toHaveBeenCalled();
  expect(pendingUpdate()).toBeNull();
});
