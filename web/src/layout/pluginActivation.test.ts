import { afterEach, beforeEach, expect, it, vi } from "vitest";

const restart = vi.hoisted(() => vi.fn());
const resume = vi.hoisted(() => vi.fn());

vi.mock("@/lifecycle/controller", async (original) => ({
  ...(await original<typeof import("@/lifecycle/controller")>()),
  restartAndApply: restart,
  resumeRestartAndApply: resume,
}));

const observation = { retired: ["old"], version: "0.9.23", settings: [] };
const key = "hz.plugin-activation.v1";

beforeEach(() => {
  vi.resetModules();
  restart.mockReset();
  resume.mockReset();
});
afterEach(() => sessionStorage.removeItem(key));

it("restores a saved restart after a fresh document loads and verifies without posting", async () => {
  sessionStorage.setItem(key, JSON.stringify({ acknowledged: true, observation }));
  const { activatePlugins } = await import("./pluginActivation");
  const reload = vi.fn();

  await activatePlugins(reload, () => true);

  expect(restart).not.toHaveBeenCalled();
  expect(resume).toHaveBeenCalledExactlyOnceWith(observation, expect.objectContaining({ signal: expect.anything() }));
  expect(reload).toHaveBeenCalledOnce();
  expect(sessionStorage.getItem(key)).toBeNull();
});

it("prevents a second activation during a plugin change and then only observes its recorded restart", async () => {
  const { acquirePluginChange, activatePlugins, retainPluginRestart } = await import("./pluginActivation");
  const release = acquirePluginChange();
  expect(release).not.toBeNull();
  const reload = vi.fn();

  retainPluginRestart(observation);
  expect(sessionStorage.getItem(key)).toBe(JSON.stringify({ acknowledged: false, observation }));
  await activatePlugins(reload, () => true);
  expect(restart).not.toHaveBeenCalled();
  expect(resume).not.toHaveBeenCalled();
  release?.();

  await activatePlugins(reload, () => true);
  expect(restart).not.toHaveBeenCalled();
  expect(resume).toHaveBeenCalledOnce();
  expect(reload).toHaveBeenCalledOnce();
});

it("refuses a fresh restart when stored evidence cannot be read", async () => {
  sessionStorage.setItem(key, "{");
  const { activatePlugins } = await import("./pluginActivation");

  await activatePlugins(vi.fn(), () => true);

  expect(restart).not.toHaveBeenCalled();
  expect(resume).not.toHaveBeenCalled();
  expect(sessionStorage.getItem(key)).toBe("{");
});

it("fails closed on reload when a restart was sent without durable acknowledgement", async () => {
  sessionStorage.setItem(key, JSON.stringify({ acknowledged: false, observation }));
  const { activatePlugins } = await import("./pluginActivation");
  await activatePlugins(vi.fn(), () => true);
  expect(restart).not.toHaveBeenCalled();
  expect(resume).not.toHaveBeenCalled();
});
