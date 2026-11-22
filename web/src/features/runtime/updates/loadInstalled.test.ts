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

const fast = { timeout: 200, interval: 5, staleAfter: 40, requestTimeout: 50, readinessTimeout: 100 };

it.each([
  [
    "a refused restart",
    (transport: UpdateTransport) => {
      transport.lifecycle.restart = vi.fn(async () => {
        throw new Error("This server can't restart itself");
      });
    },
  ],
  [
    "a restart into the wrong version",
    (transport: UpdateTransport) => {
      let restarted = false;
      transport.lifecycle.read = vi.fn(async () => (restarted ? { ...after, version: "0.1.5" } : before));
      transport.lifecycle.restart = vi.fn(async () => {
        restarted = true;
        return { restarting: true, instance: before.instance };
      });
    },
  ],
  [
    "a restart that never answers again",
    (transport: UpdateTransport) => {
      let restarted = false;
      transport.lifecycle.read = vi.fn(async () => {
        if (restarted) throw new TypeError("Failed to fetch");
        return before;
      });
      transport.lifecycle.restart = vi.fn(async () => {
        restarted = true;
        return { restarting: true, instance: before.instance };
      });
    },
  ],
  [
    "an old server that keeps answering (stale)",
    (transport: UpdateTransport) => {
      transport.lifecycle.restart = vi.fn(async () => ({ restarting: true, instance: before.instance }));
    },
  ],
])("%s leaves no receipt to inspect, so loading again stays available", async (_name, arrange) => {
  const { transport } = setup();
  arrange(transport);
  await expect(
    loadInstalledCore("0.2.0", new AbortController().signal, vi.fn(), transport, vi.fn(), fast),
  ).rejects.toThrow();
  expect(transport.lifecycle.restart).toHaveBeenCalledTimes(1);
  expect(pendingUpdate()).toBeNull();
  expect(transport.reload).not.toHaveBeenCalled();
});

it("drops a load receipt the tab reloaded away from before verification, instead of asking to inspect", () => {
  sessionStorage.setItem(
    "hz.update.receipt",
    JSON.stringify({ stage: "installed", load: true, result: { updated: true, after: "0.2.0", message: "x" } }),
  );
  expect(pendingUpdate()).toBeNull();
  expect(sessionStorage.getItem("hz.update.receipt")).toBeNull();
});

it("keeps an install receipt without a verified replacement for inspection", () => {
  const receipt = JSON.stringify({ stage: "installed", result: { updated: true, after: "0.2.0", message: "x" } });
  sessionStorage.setItem("hz.update.receipt", receipt);
  expect(pendingUpdate()?.stage).toBe("installed");
});
